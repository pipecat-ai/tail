#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Runner integration for launching the Tail app alongside a pipeline.

``TailRunner`` is a ``WorkerRunner`` that runs the Tail app in a separate
process and feeds it from a ``TailServer`` worker through a multiprocessing
queue. Every ``PipelineWorker`` added to the runner gets a ``TailObserver``
automatically::

    runner = TailRunner()
    await runner.add_workers(bot)
    await runner.run()

The pipeline process keeps running until the pipeline finishes or the app is
closed. When the pipeline finishes first, the app stays open so the session
can still be read; quitting it returns from ``run()``.
"""

import asyncio
import os
import sys
from multiprocessing import Process, Queue
from typing import Optional

from loguru import logger
from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.base_worker import BaseWorker
from pipecat.workers.runner import WorkerRunner

from pipecat_tail.server import TailServer
from pipecat_tail.sink import QueueSink


class TailAppProcess:
    """Runs the Tail app in a separate process fed by a queue.

    Nothing in this class may hold references that cannot be pickled before
    the process starts.
    """

    def __init__(self, queue: Queue):
        """Initialize the process wrapper.

        Args:
            queue: Queue the app reads messages from. ``None`` ends the app.
        """
        self._queue = queue
        self._app = None
        self._reader_task: Optional[asyncio.Task] = None

    def run(self):
        """Launch the app in a separate process and wait for it to exit."""
        process = Process(target=self._app_process)
        process.start()
        process.join()

    def _app_process(self):
        # Make sure our standard file descriptors are those of the parent.
        sys.__stdin__ = os.fdopen(0, "r", buffering=1)
        sys.__stdout__ = os.fdopen(1, "w", buffering=1)
        sys.__stderr__ = os.fdopen(2, "w", buffering=1)

        from pipecat_tail.app import TailApp

        self._app = TailApp(on_mount=self._on_mount, on_shutdown=self._on_shutdown)
        asyncio.run(self._app.run_async())

    async def _on_mount(self):
        self._reader_task = asyncio.create_task(self._read_queue())

    async def _on_shutdown(self):
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        self._reader_task = None

    async def _read_queue(self):
        loop = asyncio.get_running_loop()
        while True:
            message = await loop.run_in_executor(None, self._queue.get)
            if message is None:
                break
            if self._app:
                await self._app.handle_message(message)


class TailRunner(WorkerRunner):
    """Worker runner that shows the Tail app while the pipeline runs.

    Args:
        name: Optional runner name.
        handle_sigint: Whether the runner handles SIGINT. Off by default: the
            app owns the terminal and Ctrl-C is handled there.
        handle_sigterm: Whether the runner handles SIGTERM.
        **kwargs: Passed to ``WorkerRunner``.
    """

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        handle_sigint: bool = False,
        handle_sigterm: bool = False,
        **kwargs,
    ):
        """Initialize the runner. See the class docstring for the arguments."""
        super().__init__(
            name=name, handle_sigint=handle_sigint, handle_sigterm=handle_sigterm, **kwargs
        )
        self._queue: Queue = Queue()
        self._server = TailServer(sink=QueueSink(self._queue), auto_stop=True)
        self._server_added = False

    @property
    def server(self) -> TailServer:
        """The Tail server worker feeding the app."""
        return self._server

    async def add_workers(self, *workers: BaseWorker) -> None:
        """Register workers, attaching a Tail observer to each pipeline worker."""
        for worker in workers:
            if isinstance(worker, PipelineWorker):
                worker.add_observer(self._server.create_observer(worker))
        await super().add_workers(*workers)

    async def run(self, worker: Optional[BaseWorker] = None, *, auto_end: bool = True) -> None:
        """Run the workers and the Tail app until either finishes.

        Args:
            worker: Deprecated. A worker to add before running; prefer
                ``add_workers()``.
            auto_end: End when every root worker has finished.
        """
        if worker is not None:
            await self.add_workers(worker)
        if not self._server_added:
            self._server_added = True
            await self.add_workers(self._server)

        # The app owns the terminal, so stop writing logs to it.
        logger.remove()

        app_task = asyncio.create_task(asyncio.to_thread(self._app_thread))
        run_task = asyncio.create_task(super().run(auto_end=auto_end))
        try:
            _, pending = await asyncio.wait(
                [app_task, run_task], return_when=asyncio.FIRST_COMPLETED
            )
            if run_task in pending:
                await self.cancel(reason="Tail closed")
                await run_task
            if app_task in pending:
                # Let the user finish reading the session.
                await app_task
        finally:
            self._queue.put(None)
            logger.add(sys.stderr)

    def _app_thread(self):
        TailAppProcess(self._queue).run()
