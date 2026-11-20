#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Tail observer.

``TailObserver`` is a Pipecat observer that turns everything a pipeline emits
into messages for the Tail app. It runs a private ``RTVIObserver``, so RTVI
messages (what the user said, what the bot says as it says it, speaking and
VAD state) reach Tail as they are, and Pipecat's other observers alongside to
add what RTVI does not carry: per-turn latency breakdowns, turn boundaries,
startup timing, errors, function calls, service metrics, the LLM context,
frame flow between processors, audio levels and structured logs.

``TailObserver`` is deliberately not an ``RTVIObserver``: a pipeline worker
that finds an ``RTVIObserver`` among its observers assumes the app set up RTVI
itself and skips its own, which would leave the bot's client without RTVI.

It works with every Pipecat 1.x release. Where an observer it would use does
not exist yet, it asks RTVI for the same information instead (see
``pipecat_tail.compat``).

Standalone use, where the observer serves its own websocket::

    worker = PipelineWorker(pipeline, observers=[TailObserver()])

Runner use, where a ``TailWorker`` owns the connection and gives each
pipeline worker its observer::

    await runner.add_workers(worker, TailWorker(worker))
"""

import dataclasses
import datetime
import functools
import math
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    AudioRawFrame,
    BotStoppedSpeakingFrame,
    ControlFrame,
    ErrorFrame,
    Frame,
    ImageRawFrame,
    InputAudioRawFrame,
    LLMContextFrame,
    MetricsFrame,
    OutputAudioRawFrame,
    StartFrame,
    SystemFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import ProcessingMetricsData
from pipecat.observers.base_observer import BaseObserver, FrameProcessed, FramePushed
from pipecat.observers.startup_timing_observer import StartupTimingObserver
from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import PipelineSink, PipelineSource
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.models import (
    AudioLevelMessageData,
    BotAudioLevelMessage,
    UserAudioLevelMessage,
)
from pipecat.processors.frameworks.rtvi.observer import RTVIObserver, RTVIObserverParams
from pipecat.services.llm_service import LLMService
from pipecat.transports.base_output import BaseOutputTransport
from pydantic import BaseModel

from pipecat_tail import compat
from pipecat_tail.compat import default_rtvi_params
from pipecat_tail.context import ContextTracker
from pipecat_tail.messages import (
    TailErrorMessage,
    TailFlowMessage,
    TailFunctionCallMessage,
    TailLatencyMessage,
    TailLLMContextMessage,
    TailLogMessage,
    TailMessage,
    TailPipelineFinishedMessage,
    TailPipelineStartedMessage,
    TailReadyMessage,
    TailServiceLatencyMessage,
    TailServiceUsageMessage,
    TailStartupMessage,
    TailTransportTimingMessage,
    TailTurnMessage,
    TailVADMessage,
    tail_serializer,
)
from pipecat_tail.sink import DEFAULT_HOST, DEFAULT_PORT, TailSink, TailWebSocketServer

__all__ = ["TailObserver", "default_rtvi_params"]

DEFAULT_FLOW_INTERVAL_SECS = 0.25
_SEEN_LIMIT = 4096


def _data(value: Any) -> dict[str, Any]:
    data = tail_serializer(value)
    return data if isinstance(data, dict) else {}


def _pipeline_of(worker) -> Any:
    for name in ("pipeline", "_pipeline"):
        pipeline = getattr(worker, name, None)
        if pipeline is not None:
            return pipeline
    return None


def _pipeline_processor_names(pipeline) -> list[str]:
    """Walk a pipeline and return its processor names in order, flattened.

    Compound processors (pipelines, parallel pipelines) are replaced by their
    children. Pipeline sources and sinks are left out since they are plumbing.
    """
    names: list[str] = []

    def walk(processor):
        children = getattr(processor, "processors", None) or []
        if children:
            for child in children:
                walk(child)
        elif not isinstance(processor, (PipelineSource, PipelineSink)):
            names.append(processor.name)

    walk(pipeline)
    return names


def _frame_kind(frame) -> str:
    if isinstance(frame, AudioRawFrame):
        return "audio"
    if isinstance(frame, ImageRawFrame):
        return "image"
    if isinstance(frame, SystemFrame):
        return "system"
    if isinstance(frame, ControlFrame):
        return "control"
    return "data"


def _worker_of(processor) -> Any:
    """The worker (or task) whose pipeline starts at this processor, if any.

    A worker feeds its pipeline through a ``PipelineSource`` that hands
    upstream frames back to the worker. A pipeline's own sources hand them to
    the pipeline, which is a processor.
    """
    if not isinstance(processor, PipelineSource):
        return None
    owner = getattr(getattr(processor, "_upstream_push_frame", None), "__self__", None)
    if owner is None or isinstance(owner, FrameProcessor):
        return None
    return owner


def forward_logs(send: Callable[[TailMessage], Awaitable[None]], level: str) -> int:
    """Send loguru's records as log messages, and return the handler's id.

    Logs belong to the process, not to a pipeline: whoever owns the connection
    to the app forwards them, once.
    """

    async def sink(message):
        record = message.record
        when: datetime.datetime = record["time"]
        exception = record.get("exception")
        data = {
            "time": when.isoformat(),
            "level": record["level"].name,
            "name": record["name"],
            "function": record["function"],
            "line": record["line"],
            "message": record["message"],
        }
        if exception and exception.type is not None:
            data["exception"] = f"{exception.type.__name__}: {exception.value}"
        await send(TailLogMessage(data=data))

    def not_ours(record) -> bool:
        # Tail's own logs would feed the sink with itself.
        return not record["name"].startswith("pipecat_tail")

    return logger.add(sink, level=level, filter=not_ours, enqueue=False)


def stop_forwarding_logs(handler_id: Optional[int]) -> None:
    """Remove a handler ``forward_logs`` added, if there is one."""
    if handler_id is None:
        return
    try:
        logger.remove(handler_id)
    except ValueError:
        pass


def _is_plumbing(processor) -> bool:
    return isinstance(processor, (PipelineSource, PipelineSink)) or bool(
        getattr(processor, "processors", None)
    )


class _SeenFrames:
    """Frame ids already handled, for Pipecat before 1.12, bounded in size."""

    def __init__(self, limit: int = _SEEN_LIMIT):
        self._ids: OrderedDict[int, None] = OrderedDict()
        self._limit = limit

    def first(self, data: FramePushed) -> bool:
        """Whether this push is the first one Tail sees of its frame."""
        if compat.knows_first_push(data):
            return compat.first_push(data)
        frame_id = data.frame.id
        if frame_id in self._ids:
            return False
        self._ids[frame_id] = None
        if len(self._ids) > self._limit:
            self._ids.popitem(last=False)
        return True


def _contained(hook):
    """Run an observer hook so that no error of Tail's reaches Pipecat.

    Before Pipecat 1.16 an exception ends the observer's task: Tail would go
    quiet for the rest of the call while frames piled up for it. The error is
    logged once for each hook and kind, and the frame is skipped.
    """

    @functools.wraps(hook)
    async def run(self, *args):
        try:
            await hook(self, *args)
        except Exception as e:
            key = (hook.__name__, type(e).__name__)
            if key not in self._failed:
                self._failed.add(key)
                logger.opt(exception=e).error(f"ᓚᘏᗢ Tail: {hook.__name__} failed, skipping")

    return run


def audio_level(audio: bytes) -> float:
    """How loud 16-bit PCM audio is, from 0 to 1, for drawing.

    The RMS level in dBFS, from -50 dB (0) to 0 dB (1). Unlike RTVI's
    loudness, which integrates over a window and sits near 1 for any speech,
    this follows syllables, so the voice timeline shows speech as speech.
    """
    # A chunk can end mid-sample; the odd byte is dropped.
    samples = np.frombuffer(audio[: len(audio) // 2 * 2], dtype=np.int16)
    if samples.size == 0:
        return 0.0
    rms = math.sqrt(float(np.mean(np.square(samples.astype(np.float32)))))
    if rms < 1.0:
        return 0.0
    db = 20 * math.log10(rms / 32768.0)
    return max(0.0, min(1.0, (db + 50.0) / 50.0))


class _Level:
    """The loudest level of a voice over a reporting period."""

    def __init__(self):
        self.peak = 0.0
        self.last_sent_at = 0.0
        self.last_sent = 0.0


class _RTVIBridge(RTVIObserver):
    """Pipecat's RTVI observer, sending its messages to Tail instead of a client."""

    def __init__(self, owner: "TailObserver", params: RTVIObserverParams, **kwargs):
        super().__init__(params=params, **kwargs)
        self._owner = owner

    async def send_rtvi_message(self, model: BaseModel, exclude_none: bool = True):
        """Hand the message to the Tail observer."""
        await self._owner.send_rtvi_message(model, exclude_none)


def _without_audio_levels(params: RTVIObserverParams) -> RTVIObserverParams:
    # Tail measures audio levels itself (see ``audio_level``).
    return dataclasses.replace(
        params, user_audio_level_enabled=False, bot_audio_level_enabled=False
    )


class TailObserver(BaseObserver):
    """Observer that streams a pipeline's activity to the Tail app.

    Args:
        sink: Where to deliver messages. When omitted the observer starts its
            own websocket server on ``host`` and ``port``.
        host: Address for the built-in websocket server.
        port: Port for the built-in websocket server.
        worker: The pipeline worker (or task, before Pipecat 1.3) this
            observer is attached to. Used to tag messages with the worker name
            and to list the pipeline's processors. Optional: without it the
            observer finds the worker as its pipeline is set up.
        params: RTVI settings for the messages Tail receives. Defaults to
            ``default_rtvi_params()``. Audio levels are always Tail's own;
            ``audio_level_period_secs`` sets how often they are sent.
        log_level: Minimum loguru level forwarded as log messages, or None to
            leave the logs to whoever owns the connection: a ``TailWorker``
            forwards them once for all its pipelines.
        flow_interval_secs: How often frame flow counts are reported.
        **kwargs: Passed to the private ``RTVIObserver``.
    """

    def __init__(
        self,
        *,
        sink: Optional[TailSink] = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        worker=None,
        params: Optional[RTVIObserverParams] = None,
        log_level: Optional[str] = "DEBUG",
        flow_interval_secs: float = DEFAULT_FLOW_INTERVAL_SECS,
        **kwargs,
    ):
        """Initialize the observer. See the class docstring for the arguments."""
        super().__init__()
        params = params or default_rtvi_params()
        self._rtvi = _RTVIBridge(self, _without_audio_levels(params), **kwargs)
        self._level_period = params.audio_level_period_secs
        self._user_level = _Level()
        self._bot_level = _Level()
        # Bot audio is measured as the output transport plays it, when it
        # passes it on; otherwise as the TTS produces it.
        self._played_audio = False

        self._worker = worker
        self._worker_name: Optional[str] = getattr(worker, "name", None)
        self._log_level = log_level

        self._server: Optional[TailWebSocketServer] = None
        if sink is None:
            self._server = TailWebSocketServer(
                host=host, port=port, on_client_connected=self._on_client_connected
            )
            sink = self._server
        self._sink: TailSink = sink

        self._pipeline_finished = False
        self._seen = _SeenFrames()
        self._contexts = ContextTracker()
        self._logger_id: Optional[int] = None
        self._started = False
        self._failed: set[tuple[str, str]] = set()

        self._flow_interval = flow_interval_secs
        self._flow_started = time.monotonic()
        self._flow: dict[tuple[str, str], dict[str, int]] = {}

        # Pipecat observers that add what RTVI does not carry. The errors,
        # function calls and service metrics ones are newer than 1.0: without
        # them RTVI or the frames handled here carry the same.
        self._children: list[BaseObserver] = []
        self._add_child(
            UserBotLatencyObserver,
            on_latency_measured=self._on_latency_measured,
            on_first_bot_speech_latency=self._on_first_bot_speech,
            on_latency_breakdown=self._on_latency_breakdown,
        )
        self._add_child(
            StartupTimingObserver,
            on_startup_timing_report=self._on_startup_report,
            on_transport_timing_report=self._on_transport_report,
        )
        self._add_child(
            TurnTrackingObserver,
            on_turn_started=self._on_turn_started,
            on_turn_ended=self._on_turn_ended,
        )
        self._add_child(compat.ErrorObserver, on_error=self._on_error)
        self._add_child(
            compat.FunctionCallObserver,
            init={"include_arguments": True, "include_results": True},
            on_function_call_event=self._on_function_call,
        )
        self._add_child(
            compat.ServiceMetricsObserver,
            on_service_latency=self._on_service_latency,
            on_service_usage=self._on_service_usage,
        )

        # The breakdown fires right after one of the two latency events, so
        # pair them up.
        self._pending_latency: Optional[float] = None
        self._pending_first_bot_speech = False

    def _add_child(self, cls, *, init: Optional[dict[str, Any]] = None, **handlers) -> None:
        if cls is None:
            return
        child = cls(**(init or {}))
        for event, handler in handlers.items():
            child.add_event_handler(event, handler)
        self._children.append(child)

    def _find_worker(self, processor) -> None:
        """Learn the worker from its own pipeline source, when not given one."""
        if self._worker is not None:
            return
        worker = _worker_of(processor)
        if worker is not None:
            self._worker = worker
            self._worker_name = getattr(worker, "name", None)

    @property
    def worker_name(self) -> Optional[str]:
        """Name of the worker this observer reports for, if known."""
        return self._worker_name

    #
    # Lifecycle
    #

    async def setup(self, task_manager):
        """Set up child observers, the log sink and the built-in server.

        Pipecat calls this from 1.3 on. Before that observers are not set up,
        so the observer starts itself when the pipeline does.
        """
        parent = getattr(super(), "setup", None)
        if parent is not None:
            await parent(task_manager)
        for child in [self._rtvi, *self._children]:
            child_setup = getattr(child, "setup", None)
            if child_setup is not None:
                await child_setup(task_manager)
        await self._start()

    async def _start(self):
        if self._started:
            return
        self._started = True
        if self._log_level is not None:
            self._logger_id = forward_logs(self.send_tail_message, self._log_level)
        if self._server:
            try:
                await self._server.start()
            except OSError as e:
                logger.error(f"ᓚᘏᗢ Tail: unable to start server at {self._server.url}: {e}")

    async def cleanup(self):
        """Tear down child observers, the log sink and the built-in server."""
        stop_forwarding_logs(self._logger_id)
        self._logger_id = None
        # Child cleanup waits for their pending event handlers, so everything
        # they reported is delivered before the pipeline is declared finished.
        for child in self._children:
            await child.cleanup()
        if not self._pipeline_finished:
            self._pipeline_finished = True
            await self.send_tail_message(TailPipelineFinishedMessage())
        if self._server:
            await self._server.stop()
        await self._rtvi.cleanup()
        await super().cleanup()

    #
    # Frame hooks: run RTVI, then every child observer.
    #

    @_contained
    async def on_process_frame(self, data: FrameProcessed):
        """Forward the hook, finding the worker as the StartFrame leaves its source."""
        if not self._started:
            await self._start()
        if isinstance(data.frame, StartFrame):
            # Before Pipecat 1.8 there is no setup hook: the worker's own
            # source is the first to start.
            self._find_worker(data.processor)
        await self._rtvi.on_process_frame(data)
        for child in self._children:
            await child.on_process_frame(data)

    @_contained
    async def on_push_frame(self, data: FramePushed):
        """Forward the hook and emit Tail-specific messages for some frames."""
        if not self._started:
            await self._start()

        # Since Pipecat 1.12 observers that handle a frame once are only told
        # about its first push, and no longer de-duplicate on their own.
        first = compat.first_push(data)
        for child in [self._rtvi, *self._children]:
            if first or compat.observes_every_push(child):
                await child.on_push_frame(data)

        await self._count_flow(data)

        frame = data.frame
        if (
            isinstance(frame, OutputAudioRawFrame)
            and isinstance(data.source, BaseOutputTransport)
            and data.direction == FrameDirection.DOWNSTREAM
        ):
            # The output transport passes audio on as it plays it.
            self._played_audio = True
            await self._measure(self._bot_level, frame.audio, BotAudioLevelMessage)

        if isinstance(frame, LLMContextFrame) and isinstance(data.destination, LLMService):
            # Aggregators pass contexts around too; what matters is what
            # reaches an LLM, whichever push of the frame that is.
            await self._handle_llm_context(frame, data)

        if not self._seen.first(data):
            return
        if isinstance(frame, InputAudioRawFrame):
            await self._measure(self._user_level, frame.audio, UserAudioLevelMessage)
        elif isinstance(frame, TTSAudioRawFrame) and not self._played_audio:
            await self._measure(self._bot_level, frame.audio, BotAudioLevelMessage)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            if data.direction == FrameDirection.DOWNSTREAM:
                # Send a final zero so the meter does not freeze.
                await self._send_level(self._bot_level, 0.0, BotAudioLevelMessage)
        elif isinstance(frame, MetricsFrame):
            if compat.ServiceMetricsObserver is not None:
                await self._handle_processing_metrics(frame)
        elif isinstance(frame, ErrorFrame):
            if compat.ErrorObserver is None:
                await self._handle_error_frame(frame, data)
        elif isinstance(frame, (VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)):
            # VAD frames are broadcast as two copies; the downstream one is enough.
            if data.direction == FrameDirection.DOWNSTREAM:
                await self._handle_vad(frame)

    @_contained
    async def on_processor_setup(self, data):
        """Find the worker and forward the hook to child observers (Pipecat 1.8+)."""
        # A client can connect before the pipeline starts, and its timing
        # report must already carry the worker's name.
        self._find_worker(data.processor)
        parent = getattr(super(), "on_processor_setup", None)
        if parent is not None:
            await parent(data)
        for child in [self._rtvi, *self._children]:
            hook = getattr(child, "on_processor_setup", None)
            if hook is not None:
                await hook(data)

    @_contained
    async def on_startup_warmup(self, data):
        """Forward the hook to child observers (Pipecat 1.9 to 1.11)."""
        for child in self._children:
            hook = getattr(type(child), "on_startup_warmup", None)
            if hook is not None and hook is not getattr(BaseObserver, "on_startup_warmup", None):
                await hook(child, data)

    @_contained
    async def on_pipeline_started(self):
        """Announce the pipeline and forward the hook to child observers."""
        if not self._started:
            await self._start()
        await super().on_pipeline_started()
        await self._rtvi.on_pipeline_started()
        self._pipeline_finished = False
        pipeline = _pipeline_of(self._worker) if self._worker is not None else None
        processors = _pipeline_processor_names(pipeline) if pipeline is not None else []
        await self.send_tail_message(
            TailPipelineStartedMessage(
                data={"processors": list(processors), "capabilities": compat.capabilities()}
            )
        )
        for child in self._children:
            await child.on_pipeline_started()

    #
    # Sending
    #

    async def send_rtvi_message(self, model: BaseModel, exclude_none: bool = True):
        """Deliver an RTVI message to the sink instead of the transport.

        Messages a newer RTVI message replaces are dropped, so the app gets
        each piece of information once.
        """
        message = model.model_dump(exclude_none=exclude_none)
        if message.get("type") in compat.DEPRECATED_RTVI_MESSAGES:
            return
        message["timestamp"] = time.time()
        if self._worker_name:
            message["worker"] = self._worker_name
        await self._sink.emit(message)

    async def send_tail_message(self, model: TailMessage):
        """Deliver a Tail message to the sink."""
        if self._worker_name and model.worker is None:
            model.worker = self._worker_name
        await self._sink.emit(model.model_dump(mode="json", exclude_none=True))

    async def _on_client_connected(self):
        ready = TailReadyMessage()
        ready.data["capabilities"] = compat.capabilities()
        # Serving its own websocket, the observer reports one pipeline: the
        # runner's workers, jobs and bus need a TailWorker.
        ready.data["standalone"] = True
        await self.send_tail_message(ready)

    #
    # Audio levels
    #

    async def _measure(self, level: _Level, audio: bytes, message_type) -> None:
        level.peak = max(level.peak, audio_level(audio))
        now = time.monotonic()
        if now - level.last_sent_at >= self._level_period:
            peak, level.peak = level.peak, 0.0
            await self._send_level(level, peak, message_type, now)

    async def _send_level(self, level: _Level, value: float, message_type, now=None) -> None:
        # Silence is sent once, not every period.
        if value < 0.01 and level.last_sent < 0.01:
            return
        level.last_sent = value
        level.last_sent_at = now if now is not None else time.monotonic()
        await self.send_rtvi_message(
            message_type(data=AudioLevelMessageData(value=round(value, 3)))
        )

    #
    # Frame flow
    #

    async def _count_flow(self, data: FramePushed) -> None:
        kind = _frame_kind(data.frame)
        for processor, side in ((data.source, "out"), (data.destination, "in")):
            if _is_plumbing(processor):
                continue
            key = (processor.name, side)
            kinds = self._flow.get(key)
            if kinds is None:
                kinds = self._flow[key] = {}
            kinds[kind] = kinds.get(kind, 0) + 1
        now = time.monotonic()
        elapsed = now - self._flow_started
        if elapsed >= self._flow_interval:
            processors: dict[str, dict[str, dict[str, int]]] = {}
            for (name, side), counts in self._flow.items():
                processors.setdefault(name, {})[side] = counts
            self._flow = {}
            self._flow_started = now
            await self.send_tail_message(
                TailFlowMessage(data={"interval_secs": elapsed, "processors": processors})
            )

    #
    # Logs
    #

    #
    # Child observer events
    #

    async def _on_latency_measured(self, _observer, latency_secs: float):
        self._pending_latency = latency_secs
        self._pending_first_bot_speech = False

    async def _on_first_bot_speech(self, _observer, latency_secs: float):
        self._pending_latency = latency_secs
        self._pending_first_bot_speech = True

    async def _on_latency_breakdown(self, _observer, breakdown):
        data = _data(breakdown)
        data["latency_secs"] = (
            self._pending_latency if self._pending_latency is not None else data.get("total_secs")
        )
        data["first_bot_speech"] = self._pending_first_bot_speech
        self._pending_latency = None
        self._pending_first_bot_speech = False
        await self.send_tail_message(TailLatencyMessage(data=data))

    async def _on_error(self, _observer, event):
        await self.send_tail_message(TailErrorMessage(data=_data(event)))

    async def _on_startup_report(self, _observer, report):
        await self.send_tail_message(TailStartupMessage(data=_data(report)))

    async def _on_transport_report(self, _observer, report):
        await self.send_tail_message(TailTransportTimingMessage(data=_data(report)))

    async def _on_turn_started(self, _observer, turn_number: int):
        await self.send_tail_message(TailTurnMessage(data={"kind": "started", "turn": turn_number}))

    async def _on_turn_ended(self, _observer, turn_number: int, duration: float, was_interrupted):
        await self.send_tail_message(
            TailTurnMessage(
                data={
                    "kind": "ended",
                    "turn": turn_number,
                    "duration_secs": duration,
                    "was_interrupted": bool(was_interrupted),
                }
            )
        )

    async def _on_function_call(self, _observer, event):
        await self.send_tail_message(TailFunctionCallMessage(data=_data(event)))

    async def _on_service_latency(self, _observer, record):
        await self.send_tail_message(TailServiceLatencyMessage(data=_data(record)))

    async def _on_service_usage(self, _observer, record):
        await self.send_tail_message(TailServiceUsageMessage(data=_data(record)))

    #
    # Frames handled here
    #

    async def _handle_processing_metrics(self, frame: MetricsFrame):
        # ServiceMetricsObserver covers TTFB, TTFA, TTFAT and usage. Processing
        # time is the one latency it leaves out, so add it here.
        for metrics in frame.data:
            if isinstance(metrics, ProcessingMetricsData):
                await self.send_tail_message(
                    TailServiceLatencyMessage(
                        data={
                            "kind": "processing",
                            "processor": metrics.processor,
                            "model": metrics.model,
                            "timestamp": time.time(),
                            "seconds": metrics.value,
                        }
                    )
                )

    async def _handle_vad(self, frame: Frame):
        # The frame says when the VAD decided and how long it waited first, so
        # the app can place speech and silence where they really happened.
        speaking = isinstance(frame, VADUserStartedSpeakingFrame)
        secs = float(getattr(frame, "start_secs" if speaking else "stop_secs", 0.0) or 0.0)
        decided = float(getattr(frame, "timestamp", 0.0) or time.time())
        await self.send_tail_message(
            TailVADMessage(
                timestamp=decided,
                data={"speaking": speaking, "secs": secs, "at": decided - secs},
            )
        )

    async def _handle_error_frame(self, frame: ErrorFrame, data: FramePushed):
        # Before Pipecat 1.9 there is no ErrorObserver. The first push of an
        # error comes from the processor that failed.
        exception = getattr(frame, "exception", None)
        processor = getattr(frame, "processor", None) or data.source
        await self.send_tail_message(
            TailErrorMessage(
                data={
                    "message": str(frame.error),
                    "category": "unknown",
                    "processor": getattr(processor, "name", str(processor)),
                    "processor_usable": not bool(getattr(frame, "fatal", False)),
                    "timestamp": time.time(),
                    "exception_type": type(exception).__name__ if exception else None,
                }
            )
        )

    async def _handle_llm_context(self, frame: LLMContextFrame, data: FramePushed):
        llm = data.destination
        name = getattr(llm, "name", None) or type(llm).__name__
        try:
            payload = self._contexts.update(name, llm, frame.context)
        except Exception as e:
            # Never let a context Tail can't read break the bot.
            logger.debug(f"{self}: could not read the LLM context: {e}")
            return
        await self.send_tail_message(TailLLMContextMessage(data=payload))
