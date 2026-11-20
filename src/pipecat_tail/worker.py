#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail as a worker on a ``WorkerRunner``.

``TailWorker`` reports pipeline workers, and the runner they run in, to the
Tail app. It gives each pipeline worker it's handed a ``TailObserver``, so
each pipeline's conversation, metrics and logs arrive tagged with the worker
name, and it listens on the worker bus, so it sees every worker, job and bus
message in the runner::

    runner = WorkerRunner()
    await runner.add_workers(bot, TailWorker(bot))
    await runner.run()

The app opens in this terminal. With ``server=True`` it's served instead, for
``pipecat tail`` in another terminal or on another machine.
"""

import asyncio
import time
from typing import Any, Optional

from loguru import logger

try:
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.workers.base_worker import BaseWorker
except ImportError as e:  # Pipecat before 1.3
    raise ImportError(
        "TailWorker needs Pipecat 1.3 or later (WorkerRunner and the worker bus). "
        "With older Pipecat, add a TailObserver to the PipelineTask instead."
    ) from e

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

from pipecat_tail import compat, dashboard
from pipecat_tail.messages import (
    TailBusMessage,
    TailJobMessage,
    TailMessage,
    TailReadyMessage,
    TailWorkerErrorMessage,
    TailWorkerReadyMessage,
    TailWorkersMessage,
    tail_serializer,
)
from pipecat_tail.observer import TailObserver, forward_logs, stop_forwarding_logs
from pipecat_tail.sink import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    CallbackSink,
    TailSink,
    TailWebSocketServer,
)

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


def _name(message: BusMessage) -> str:
    """A bus message's name: Pipecat names them from 1.8, by class and count."""
    return getattr(message, "name", None) or type(message).__name__


class TailWorker(BaseWorker):
    """Worker that reports pipeline workers and their runner to the Tail app.

    It stops once every pipeline worker it reports on has finished. With the
    app in this terminal it stays until the app is closed, so the call can
    still be read, and closing the app stops the bots it shows.

    Args:
        *workers: The pipeline workers to report on. They get their
            ``TailObserver`` here, before they start; ``create_observer``
            adds more later.
        server: Serve the app on ``host`` and ``port``, for ``pipecat tail``,
            instead of opening it in this terminal.
        host: Address the server listens on.
        port: Port the server listens on.
        name: Worker name. Defaults to ``tail``.
        sink: Deliver the messages here instead, for tests and embedding.
        exclude_bus_frames: Frame types whose bus messages are not forwarded.
        log_level: Minimum loguru level forwarded as log messages.
    """

    def __init__(
        self,
        *workers: PipelineWorker,
        server: bool = False,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        name: str = "tail",
        sink: Optional[TailSink] = None,
        exclude_bus_frames: tuple[type[Frame], ...] = DEFAULT_EXCLUDE_BUS_FRAMES,
        log_level: str = "DEBUG",
    ):
        """Initialize the worker. See the class docstring for the arguments."""
        super().__init__(name)
        self._server: Optional[TailWebSocketServer] = None
        # The app in this terminal, shared by the process's workers.
        self._here = sink is None and not server
        if sink is None and server:
            self._server = TailWebSocketServer(
                host=host, port=port, on_client_connected=self._on_client_connected
            )
            sink = self._server
        self._sink: TailSink = sink if sink is not None else CallbackSink(dashboard.to_dashboard)
        self._exclude_bus_frames = exclude_bus_frames
        self._observed: dict[str, PipelineWorker] = {}
        self._pipeline_done: dict[str, asyncio.Event] = {}
        self._registry_snapshot: Optional[dict[str, Any]] = None
        self._runner_name: Optional[str] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._log_level = log_level
        self._logger_id: Optional[int] = None
        self._app_closed: Optional[asyncio.Task] = None
        self._closing_task: Optional[asyncio.Task] = None
        for worker in workers:
            worker.add_observer(self.create_observer(worker))

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
            An observer whose messages flow through this worker.
        """
        self._observed[worker.name] = worker
        self._pipeline_done[worker.name] = asyncio.Event()
        self._maybe_watch()
        # This worker forwards the process's logs, once for all its pipelines.
        kwargs.setdefault("log_level", None)
        return TailObserver(sink=self, worker=worker, **kwargs)

    async def emit(self, message: dict[str, Any]) -> None:
        """Deliver one message to the app."""
        await self._sink.emit(message)
        if message.get("type") == "tail-pipeline-finished":
            worker = message.get("worker")
            done = self._pipeline_done.get(worker) if isinstance(worker, str) else None
            if done is not None:
                done.set()

    #
    # Worker lifecycle
    #

    async def start(self) -> None:
        """Start the worker, and the app here or its server."""
        await super().start()
        if self._here:
            # The app here is the process's, and so are the logs it gets.
            self._app_closed = dashboard.DASHBOARD.open(self._log_level)
            self._closing_task = asyncio.create_task(self._stop_bots_when_closed())
        else:
            self._logger_id = forward_logs(self._send, self._log_level)
        if self._server:
            try:
                await self._server.start()
            except OSError as e:
                logger.error(f"ᓚᘏᗢ Tail: unable to start server at {self._server.url}: {e}")
        else:
            # The app here, or a sink, gets the messages directly: there is no
            # client to wait for, so the app hears it is connected right away.
            await self._on_client_connected()
        self._maybe_watch()

    async def _stop_bots_when_closed(self) -> None:
        if self._app_closed is None:
            return
        await self._app_closed
        running = [w for w in self._observed.values() if not w.has_finished()]
        if running:
            logger.info("ᓚᘏᗢ Tail: closed, stopping the bot")
        for worker in running:
            await worker.cancel(reason="Tail closed")

    async def stop(self) -> None:
        """Stop the worker, and its websocket server or the app here.

        It stops on its own once its pipelines have finished and the app here
        is closed. With the app still open, the runner is shutting it down
        from outside (as a development runner does on Ctrl-C): the app
        closes too, so nothing is left waiting on it.
        """
        if self._app_closed is not None and not self._app_closed.done():
            await dashboard.DASHBOARD.close()
        for task in (self._watch_task, self._closing_task):
            if task is not None and not task.done() and task is not asyncio.current_task():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._watch_task = self._closing_task = None
        stop_forwarding_logs(self._logger_id)
        self._logger_id = None
        if self._server:
            await self._server.stop()
        await super().stop()

    async def _send(self, model: TailMessage) -> None:
        await self.emit(model.model_dump(mode="json", exclude_none=True))

    def _maybe_watch(self) -> None:
        if self._watch_task or not self._observed:
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
        if self._app_closed is not None:
            # The call can still be read: stay until the app is closed.
            await self._app_closed
        logger.debug("ᓚᘏᗢ Tail: all observed pipelines finished, stopping")
        self._watch_task = None
        await self.stop()

    #
    # Bus
    #

    def accepts_bus_message(self, message: BusMessage) -> bool:
        """Accept every message: Tail observes, it never opts out (Pipecat 1.8+)."""
        return True

    async def on_bus_message(self, message: BusMessage) -> None:
        """Forward the bus message to the app, then let the base class handle it."""
        try:
            await self._forward_bus_message(message)
        except Exception as e:
            logger.warning(f"ᓚᘏᗢ Tail: unable to forward bus message {_name(message)}: {e}")
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
                    "name": _name(message),
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
            self._runner_name = getattr(message, "runner", None) or self._runner_name
            await self.emit(
                TailWorkerReadyMessage(
                    timestamp=now,
                    worker=source,
                    data={
                        "runner": getattr(message, "runner", None),
                        "parent": getattr(message, "parent", None),
                        "active": getattr(message, "active", None),
                        "bridged": getattr(message, "bridged", None),
                        "started_at": getattr(message, "started_at", None),
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
        ready.data["capabilities"] = compat.capabilities()
        if self._here:
            # Closing the app here stops the bots: the app says so before it quits.
            ready.data["quit_stops_bot"] = True
        try:
            ready.data["runner"] = self.worker_runner.name
        except (RuntimeError, AttributeError):
            # No runner yet, or Pipecat before 1.8.
            if self._runner_name:
                ready.data["runner"] = self._runner_name
        await self.emit(ready.model_dump(mode="json", exclude_none=True))
        if self._registry_snapshot:
            await self.emit(
                TailWorkersMessage(data=self._registry_snapshot).model_dump(
                    mode="json", exclude_none=True
                )
            )
