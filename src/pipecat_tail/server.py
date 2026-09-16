#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail as a worker on a ``WorkerRunner``.

``TailServer`` is a Pipecat worker that owns one connection to the Tail app.
It subscribes to the worker bus, so it sees every worker, job and bus message
in the runner, and it creates one ``TailObserver`` per pipeline worker so
each pipeline's conversation, metrics and logs arrive tagged with the worker
name.

Wire it in code::

    tail = TailServer()
    runner = WorkerRunner()
    await runner.add_workers(bot, tail)
    bot.add_observer(tail.create_observer(bot))

or, without touching the bot, through a setup file::

    PIPECAT_SETUP_FILES=$(python -m pipecat_tail.setup) python bot.py
"""

import asyncio
import time
from typing import Any, Optional

from loguru import logger
from pipecat.bus.messages import (
    BusActivateWorkerMessage,
    BusAddWorkerMessage,
    BusCancelMessage,
    BusCancelWorkerMessage,
    BusDeactivateWorkerMessage,
    BusEndMessage,
    BusEndWorkerMessage,
    BusFrameMessage,
    BusJobCancelMessage,
    BusJobRequestMessage,
    BusJobResponseMessage,
    BusJobResponseUrgentMessage,
    BusJobStreamDataMessage,
    BusJobStreamEndMessage,
    BusJobStreamStartMessage,
    BusJobUpdateMessage,
    BusJobUpdateRequestMessage,
    BusJobUpdateUrgentMessage,
    BusMessage,
    BusWorkerErrorMessage,
    BusWorkerLocalErrorMessage,
    BusWorkerReadyMessage,
    BusWorkerRegistryMessage,
)
from pipecat.frames.frames import (
    BotSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    UserSpeakingFrame,
)
from pipecat.workers.base_worker import BaseWorker

from pipecat_tail.messages import (
    TailBusMessage,
    TailJobMessage,
    TailReadyMessage,
    TailWorkerErrorMessage,
    TailWorkerReadyMessage,
    TailWorkersMessage,
    tail_serializer,
)
from pipecat_tail.observer import TailObserver
from pipecat_tail.sink import DEFAULT_HOST, DEFAULT_PORT, TailSink, TailWebSocketServer

DEFAULT_EXCLUDE_BUS_FRAMES: tuple[type[Frame], ...] = (
    InputAudioRawFrame,
    OutputAudioRawFrame,
    UserSpeakingFrame,
    BotSpeakingFrame,
)
"""Frames whose bus messages are too frequent to be worth showing."""

_LIFECYCLE_MESSAGES = (
    BusActivateWorkerMessage,
    BusDeactivateWorkerMessage,
    BusEndWorkerMessage,
    BusCancelWorkerMessage,
    BusWorkerReadyMessage,
    BusWorkerRegistryMessage,
    BusAddWorkerMessage,
    BusWorkerErrorMessage,
    BusWorkerLocalErrorMessage,
    BusEndMessage,
    BusCancelMessage,
)

_JOB_MESSAGES = (
    BusJobRequestMessage,
    BusJobResponseMessage,
    BusJobResponseUrgentMessage,
    BusJobUpdateMessage,
    BusJobUpdateRequestMessage,
    BusJobUpdateUrgentMessage,
    BusJobCancelMessage,
    BusJobStreamStartMessage,
    BusJobStreamDataMessage,
    BusJobStreamEndMessage,
)


def bus_message_category(message: BusMessage) -> str:
    """Classify a bus message as ``frame``, ``job``, ``lifecycle`` or ``other``."""
    if isinstance(message, BusFrameMessage):
        return "frame"
    if isinstance(message, _JOB_MESSAGES):
        return "job"
    if isinstance(message, _LIFECYCLE_MESSAGES):
        return "lifecycle"
    return "other"


def _bus_payload(message: BusMessage) -> dict[str, Any]:
    payload = tail_serializer(message)
    if not isinstance(payload, dict):
        return {}
    for key in ("id", "name", "source", "target"):
        payload.pop(key, None)
    if isinstance(message, BusFrameMessage):
        # The frame is the interesting part. Keep its name and a compact dump.
        payload["frame"] = message.frame.name
        payload["frame_type"] = type(message.frame).__name__
    if isinstance(message, BusAddWorkerMessage):
        payload["worker"] = message.worker.name
    return payload


class TailServer(BaseWorker):
    """Worker that streams a runner's activity to the Tail app.

    Args:
        name: Worker name. Defaults to ``tail``.
        host: Address for the websocket server.
        port: Port for the websocket server.
        sink: Where to deliver messages. When omitted the worker serves its
            own websocket on ``host`` and ``port``.
        exclude_bus_frames: Frame types whose bus messages are not forwarded.
        auto_stop: Stop this worker, and so let the runner end, once every
            pipeline worker it observes has finished.
    """

    def __init__(
        self,
        name: str = "tail",
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        sink: Optional[TailSink] = None,
        exclude_bus_frames: tuple[type[Frame], ...] = DEFAULT_EXCLUDE_BUS_FRAMES,
        auto_stop: bool = True,
    ):
        """Initialize the server. See the class docstring for the arguments."""
        super().__init__(name)
        self._server: Optional[TailWebSocketServer] = None
        if sink is None:
            self._server = TailWebSocketServer(
                host=host, port=port, on_client_connected=self._on_client_connected
            )
            sink = self._server
        self._sink: TailSink = sink
        self._exclude_bus_frames = exclude_bus_frames
        self._auto_stop = auto_stop
        self._observed: dict[str, BaseWorker] = {}
        self._pipeline_done: dict[str, asyncio.Event] = {}
        self._registry_snapshot: Optional[dict[str, Any]] = None
        self._watch_task: Optional[asyncio.Task] = None

    @property
    def url(self) -> Optional[str]:
        """URL of the built-in websocket server, if any."""
        return self._server.url if self._server else None

    def create_observer(self, worker, **kwargs) -> TailObserver:
        """Create the observer for a pipeline worker.

        Args:
            worker: The ``PipelineWorker`` to observe.
            **kwargs: Passed to ``TailObserver``.

        Returns:
            An observer whose messages flow through this server.
        """
        self._observed[worker.name] = worker
        self._pipeline_done[worker.name] = asyncio.Event()
        self._maybe_watch()
        return TailObserver(sink=self, worker=worker, **kwargs)

    async def emit(self, message: dict[str, Any]) -> None:
        """Deliver one message to the app."""
        await self._sink.emit(message)
        if message.get("type") == "tail-pipeline-finished":
            done = self._pipeline_done.get(message.get("worker"))
            if done is not None:
                done.set()

    #
    # Worker lifecycle
    #

    async def start(self) -> None:
        """Start the worker and its websocket server."""
        await super().start()
        if self._server:
            try:
                await self._server.start()
            except OSError as e:
                logger.error(f"ᓚᘏᗢ Tail: unable to start server at {self._server.url}: {e}")
        self._maybe_watch()

    async def stop(self) -> None:
        """Stop the websocket server and the worker."""
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None
        if self._server:
            await self._server.stop()
        await super().stop()

    def _maybe_watch(self) -> None:
        if not self._auto_stop or self._watch_task or not self._observed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._watch_task = loop.create_task(self._watch_observed())

    async def _watch_observed(self) -> None:
        # Wait until every observed pipeline worker has finished. New workers
        # observed while waiting extend the wait.
        seen: set[str] = set()
        while True:
            pending = [w for n, w in self._observed.items() if n not in seen]
            if not pending:
                break
            for worker in pending:
                seen.add(worker.name)
                await worker.wait()
                # The worker finishes before its observers are cleaned up, so
                # give the observer a moment to report the pipeline finished.
                done = self._pipeline_done.get(worker.name)
                if done is not None:
                    try:
                        await asyncio.wait_for(done.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        pass
        logger.debug("ᓚᘏᗢ Tail: all observed pipelines finished, stopping")
        self._watch_task = None
        await self.stop()

    #
    # Bus
    #

    def accepts_bus_message(self, message: BusMessage) -> bool:
        """Accept every message: Tail observes, it never opts out."""
        return True

    async def on_bus_message(self, message: BusMessage) -> None:
        """Forward the bus message to the app, then let the base class handle it."""
        try:
            await self._forward_bus_message(message)
        except Exception as e:
            logger.warning(f"ᓚᘏᗢ Tail: unable to forward bus message {message.name}: {e}")
        await super().on_bus_message(message)

    async def _forward_bus_message(self, message: BusMessage) -> None:
        if message.source == self.name:
            return
        if isinstance(message, BusFrameMessage) and isinstance(
            message.frame, self._exclude_bus_frames
        ):
            return

        source = getattr(message, "source", None)
        target = getattr(message, "target", None)
        now = time.time()

        await self.emit(
            TailBusMessage(
                timestamp=now,
                data={
                    "name": message.name,
                    "message_type": type(message).__name__,
                    "category": bus_message_category(message),
                    "source": source,
                    "target": target,
                    "payload": _bus_payload(message),
                },
            ).model_dump(mode="json", exclude_none=True)
        )

        if isinstance(message, BusWorkerRegistryMessage):
            self._registry_snapshot = {
                "runner": message.runner,
                "workers": [tail_serializer(entry) for entry in message.workers],
            }
            await self.emit(
                TailWorkersMessage(timestamp=now, data=self._registry_snapshot).model_dump(
                    mode="json", exclude_none=True
                )
            )
        elif isinstance(message, BusWorkerReadyMessage):
            await self.emit(
                TailWorkerReadyMessage(
                    timestamp=now,
                    worker=source,
                    data={
                        "runner": message.runner,
                        "parent": message.parent,
                        "active": message.active,
                        "bridged": message.bridged,
                        "started_at": message.started_at,
                    },
                ).model_dump(mode="json", exclude_none=True)
            )
        elif isinstance(message, (BusWorkerErrorMessage, BusWorkerLocalErrorMessage)):
            await self.emit(
                TailWorkerErrorMessage(
                    timestamp=now, worker=source, data={"error": message.error}
                ).model_dump(mode="json", exclude_none=True)
            )
        elif isinstance(message, _JOB_MESSAGES):
            await self.emit(
                TailJobMessage(timestamp=now, data=self._job_data(message)).model_dump(
                    mode="json", exclude_none=True
                )
            )

    def _job_data(self, message: BusMessage) -> dict[str, Any]:
        data: dict[str, Any] = {
            "job_id": getattr(message, "job_id", None),
            "source": getattr(message, "source", None),
            "target": getattr(message, "target", None),
        }
        if isinstance(message, BusJobRequestMessage):
            data["kind"] = "requested"
            data["job_name"] = message.job_name
            data["payload"] = tail_serializer(message.payload)
        elif isinstance(message, (BusJobResponseMessage, BusJobResponseUrgentMessage)):
            data["kind"] = "responded"
            data["status"] = str(message.status)
            data["response"] = tail_serializer(message.response)
        elif isinstance(message, (BusJobUpdateMessage, BusJobUpdateUrgentMessage)):
            data["kind"] = "updated"
            data["update"] = tail_serializer(message.update)
        elif isinstance(message, BusJobUpdateRequestMessage):
            data["kind"] = "update_requested"
        elif isinstance(message, BusJobCancelMessage):
            data["kind"] = "cancelled"
            data["reason"] = message.reason
        elif isinstance(message, BusJobStreamStartMessage):
            data["kind"] = "stream_start"
            data["data"] = tail_serializer(message.data)
        elif isinstance(message, BusJobStreamDataMessage):
            data["kind"] = "stream_data"
            data["data"] = tail_serializer(message.data)
        elif isinstance(message, BusJobStreamEndMessage):
            data["kind"] = "stream_end"
            data["data"] = tail_serializer(message.data)
        return data

    #
    # Client
    #

    async def _on_client_connected(self) -> None:
        ready = TailReadyMessage()
        try:
            ready.data["runner"] = self.worker_runner.name
        except RuntimeError:
            pass
        await self.emit(ready.model_dump(mode="json", exclude_none=True))
        if self._registry_snapshot:
            await self.emit(
                TailWorkersMessage(data=self._registry_snapshot).model_dump(
                    mode="json", exclude_none=True
                )
            )
