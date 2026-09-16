#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Tail observer.

``TailObserver`` is a Pipecat observer that turns everything a pipeline emits
into messages for the Tail app. It extends Pipecat's ``RTVIObserver`` so RTVI
messages (transcriptions, LLM and TTS text, speaking state, audio levels) are
forwarded as they are, and it runs Pipecat's other observers alongside to add
what RTVI does not carry: per-turn latency breakdowns, function calls, service
metrics, errors, speech events, turn boundaries, startup timing and structured
logs.

Standalone use, where the observer serves its own websocket::

    worker = PipelineWorker(pipeline, observers=[TailObserver()])

Runner use, where a ``TailServer`` worker owns the connection::

    worker.add_observer(server.create_observer(worker))
"""

import datetime
import time
from typing import Any, Optional

from loguru import logger
from pipecat.frames.frames import MetricsFrame, StartFrame
from pipecat.metrics.metrics import ProcessingMetricsData
from pipecat.observers.base_observer import (
    BaseObserver,
    FrameProcessed,
    FramePushed,
    ProcessorSetUp,
    StartupWarmup,
)
from pipecat.observers.error_observer import ErrorObserver
from pipecat.observers.function_call_observer import FunctionCallObserver
from pipecat.observers.service_metrics_observer import ServiceMetricsObserver
from pipecat.observers.speaking_observer import SpeakingObserver
from pipecat.observers.startup_timing_observer import StartupTimingObserver
from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import PipelineSink, PipelineSource
from pipecat.processors.frameworks.rtvi.observer import (
    RTVIFunctionCallReportLevel,
    RTVIObserver,
    RTVIObserverParams,
)
from pydantic import BaseModel

from pipecat_tail.messages import (
    TailErrorMessage,
    TailFunctionCallMessage,
    TailLatencyMessage,
    TailLogMessage,
    TailMessage,
    TailPipelineFinishedMessage,
    TailPipelineStartedMessage,
    TailReadyMessage,
    TailServiceLatencyMessage,
    TailServiceUsageMessage,
    TailSpeechMessage,
    TailStartupMessage,
    TailTransportTimingMessage,
    TailTurnMessage,
)
from pipecat_tail.sink import DEFAULT_HOST, DEFAULT_PORT, TailSink, TailWebSocketServer


def default_rtvi_params() -> RTVIObserverParams:
    """RTVI observer settings Tail uses.

    Audio levels are on so the meters move. Metrics, function calls and system
    logs are off because Tail gets richer versions of those from dedicated
    observers and its own log sink.
    """
    return RTVIObserverParams(
        user_audio_level_enabled=True,
        bot_audio_level_enabled=True,
        metrics_enabled=False,
        system_logs_enabled=False,
        function_call_report_level={"*": RTVIFunctionCallReportLevel.DISABLED},
    )


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


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


class TailObserver(RTVIObserver):
    """Observer that streams a pipeline's activity to the Tail app.

    Args:
        sink: Where to deliver messages. When omitted the observer starts its
            own websocket server on ``host`` and ``port``.
        host: Address for the built-in websocket server.
        port: Port for the built-in websocket server.
        worker: The pipeline worker this observer is attached to. Used to tag
            messages with the worker name and to list the pipeline's
            processors. Optional; without it processors are discovered as the
            ``StartFrame`` reaches them.
        params: RTVI observer settings. Defaults to ``default_rtvi_params()``.
        log_level: Minimum loguru level forwarded as log messages.
        **kwargs: Passed to ``RTVIObserver``.
    """

    def __init__(
        self,
        *,
        sink: Optional[TailSink] = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        worker=None,
        params: Optional[RTVIObserverParams] = None,
        log_level: str = "DEBUG",
        **kwargs,
    ):
        """Initialize the observer. See the class docstring for the arguments."""
        super().__init__(params=params or default_rtvi_params(), **kwargs)

        self._worker = worker
        self._worker_name: Optional[str] = worker.name if worker is not None else None
        self._log_level = log_level

        self._server: Optional[TailWebSocketServer] = None
        if sink is None:
            self._server = TailWebSocketServer(
                host=host, port=port, on_client_connected=self._on_client_connected
            )
            sink = self._server
        self._sink: TailSink = sink

        self._processors: list[str] = []
        self._pipeline_finished = False
        self._metrics_seen: set[int] = set()
        self._logger_id: Optional[int] = None

        # Pipecat observers that add what RTVI does not carry.
        self._latency = UserBotLatencyObserver()
        self._latency.add_event_handler("on_latency_measured", self._on_latency_measured)
        self._latency.add_event_handler("on_first_bot_speech_latency", self._on_first_bot_speech)
        self._latency.add_event_handler("on_latency_breakdown", self._on_latency_breakdown)
        self._errors = ErrorObserver()
        self._errors.add_event_handler("on_error", self._on_error)
        self._speaking = SpeakingObserver()
        self._speaking.add_event_handler("on_speech_event", self._on_speech_event)
        self._startup = StartupTimingObserver()
        self._startup.add_event_handler("on_startup_timing_report", self._on_startup_report)
        self._startup.add_event_handler("on_transport_timing_report", self._on_transport_report)
        self._turns = TurnTrackingObserver()
        self._turns.add_event_handler("on_turn_started", self._on_turn_started)
        self._turns.add_event_handler("on_turn_ended", self._on_turn_ended)
        self._function_calls = FunctionCallObserver(include_arguments=True, include_results=True)
        self._function_calls.add_event_handler("on_function_call_event", self._on_function_call)
        self._metrics = ServiceMetricsObserver()
        self._metrics.add_event_handler("on_service_latency", self._on_service_latency)
        self._metrics.add_event_handler("on_service_usage", self._on_service_usage)

        self._children: list[BaseObserver] = [
            self._latency,
            self._errors,
            self._speaking,
            self._startup,
            self._turns,
            self._function_calls,
            self._metrics,
        ]

        # Latency for the turn currently being reported. The breakdown fires
        # right after one of the two latency events, so we pair them up.
        self._pending_latency: Optional[float] = None
        self._pending_first_bot_speech = False

    @property
    def worker_name(self) -> Optional[str]:
        """Name of the worker this observer reports for, if known."""
        return self._worker_name

    @property
    def server(self) -> Optional[TailWebSocketServer]:
        """The built-in websocket server, if this observer owns one."""
        return self._server

    #
    # Lifecycle
    #

    async def setup(self, task_manager):
        """Set up child observers, the log sink and the built-in server."""
        await super().setup(task_manager)
        for child in self._children:
            await child.setup(task_manager)
        self._logger_id = logger.add(
            self._log_sink, level=self._log_level, filter=self._log_filter, enqueue=False
        )
        if self._server:
            await self._server.start()

    async def cleanup(self):
        """Tear down child observers, the log sink and the built-in server."""
        if self._logger_id is not None:
            logger.remove(self._logger_id)
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
        await super().cleanup()

    #
    # Frame hooks: run RTVI, then every child observer.
    #

    async def on_process_frame(self, data: FrameProcessed):
        """Forward the hook and record processors as the StartFrame reaches them."""
        if isinstance(data.frame, StartFrame) and self._worker is None:
            name = data.processor.name
            if name not in self._processors:
                self._processors.append(name)
        await super().on_process_frame(data)
        for child in self._children:
            await child.on_process_frame(data)

    async def on_push_frame(self, data: FramePushed):
        """Forward the hook and emit Tail-specific messages for some frames."""
        await super().on_push_frame(data)
        for child in self._children:
            await child.on_push_frame(data)

        frame = data.frame
        if isinstance(frame, MetricsFrame) and frame.id not in self._metrics_seen:
            if len(self._metrics_seen) > 10000:
                self._metrics_seen.clear()
            self._metrics_seen.add(frame.id)
            await self._handle_processing_metrics(frame)

    async def on_processor_setup(self, data: ProcessorSetUp):
        """Forward the hook to child observers."""
        await super().on_processor_setup(data)
        for child in self._children:
            await child.on_processor_setup(data)

    async def on_startup_warmup(self, data: StartupWarmup):
        """Forward the hook to child observers."""
        await super().on_startup_warmup(data)
        for child in self._children:
            await child.on_startup_warmup(data)

    async def on_pipeline_started(self):
        """Announce the pipeline and forward the hook to child observers."""
        await super().on_pipeline_started()
        self._pipeline_finished = False
        processors = self._processors
        if self._worker is not None:
            try:
                processors = _pipeline_processor_names(self._worker.pipeline)
            except Exception:
                processors = self._processors
        await self.send_tail_message(
            TailPipelineStartedMessage(data={"processors": list(processors)})
        )
        for child in self._children:
            await child.on_pipeline_started()

    #
    # Sending
    #

    async def send_rtvi_message(self, model: BaseModel, exclude_none: bool = True):
        """Deliver an RTVI message to the sink instead of the transport."""
        message = model.model_dump(exclude_none=exclude_none)
        if self._worker_name:
            message["worker"] = self._worker_name
        await self._sink.emit(message)

    async def send_tail_message(self, model: TailMessage):
        """Deliver a Tail message to the sink."""
        if self._worker_name and model.worker is None:
            model.worker = self._worker_name
        await self._sink.emit(model.model_dump(mode="json", exclude_none=True))

    async def _on_client_connected(self):
        await self.send_tail_message(TailReadyMessage())

    #
    # Logs
    #

    def _log_filter(self, record) -> bool:
        # Never forward our own logs: the sink would feed itself.
        return not record["name"].startswith("pipecat_tail")

    async def _log_sink(self, message):
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
        await self.send_tail_message(TailLogMessage(data=data))

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
        data = _dump(breakdown)
        data["latency_secs"] = (
            self._pending_latency if self._pending_latency is not None else data.get("total_secs")
        )
        data["first_bot_speech"] = self._pending_first_bot_speech
        self._pending_latency = None
        self._pending_first_bot_speech = False
        await self.send_tail_message(TailLatencyMessage(data=data))

    async def _on_error(self, _observer, event):
        await self.send_tail_message(TailErrorMessage(data=_dump(event)))

    async def _on_speech_event(self, _observer, event):
        await self.send_tail_message(TailSpeechMessage(data=_dump(event)))

    async def _on_startup_report(self, _observer, report):
        await self.send_tail_message(TailStartupMessage(data=_dump(report)))

    async def _on_transport_report(self, _observer, report):
        await self.send_tail_message(TailTransportTimingMessage(data=_dump(report)))

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
        await self.send_tail_message(TailFunctionCallMessage(data=_dump(event)))

    async def _on_service_latency(self, _observer, record):
        await self.send_tail_message(TailServiceLatencyMessage(data=_dump(record)))

    async def _on_service_usage(self, _observer, record):
        await self.send_tail_message(TailServiceUsageMessage(data=_dump(record)))

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
