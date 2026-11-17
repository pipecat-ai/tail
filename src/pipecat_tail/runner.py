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
can still be read; quitting it returns from ``run()``. When ``run()`` is
cancelled (for example by the development runner shutting down on Ctrl-C),
the pipeline is cancelled and the app closes.
"""

import asyncio
import os
import signal
import sys
import threading
from multiprocessing import Process, Queue
from typing import Optional

from loguru import logger
from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.base_worker import BaseWorker
from pipecat.workers.runner import WorkerRunner

from pipecat_tail.server import TailServer
from pipecat_tail.sink import QueueSink

QUIT = "quit"
"""Put on the app's queue to close the app. ``None`` only ends the messages."""

APP_QUIT_TIMEOUT_SECS = 5.0
"""How long the app gets to close when asked before its process is terminated."""


class _TerminalOutput:
    """Keeps this process's output off the terminal while the app draws on it.

    The app runs in its own process, so anything this one writes to the
    terminal (logs, prints, warnings) would land on top of it. Its stdout and
    stderr point at ``/dev/null`` meanwhile and are restored when the app
    closes, with every log handler as it was. Output going to a file or a
    pipe is left alone.
    """

    def __init__(self):
        self._saved: list[tuple[int, int]] = []

    def hide(self):
        """Point stdout and stderr at ``/dev/null`` if they are a terminal."""
        self._flush()
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            for fd in (1, 2):
                if os.isatty(fd):
                    self._saved.append((fd, os.dup(fd)))
                    os.dup2(devnull, fd)
        finally:
            os.close(devnull)

    def restore(self):
        """Point stdout and stderr back where they were."""
        if not self._saved:
            return
        self._flush()
        for fd, saved in self._saved:
            os.dup2(saved, fd)
            os.close(saved)
        self._saved = []

    def _flush(self):
        for stream in (sys.stdout, sys.stderr):
            try:
                if stream:
                    stream.flush()
            except (OSError, ValueError):
                pass


class TailAppProcess:
    """Runs the Tail app in a separate process fed by a queue.

    Nothing in this class may hold references that cannot be pickled before
    the process starts.
    """

    def __init__(self, queue: Queue, *, headless: bool = False):
        """Initialize the process wrapper.

        Args:
            queue: Queue the app reads messages from. ``None`` ends the
                messages and ``QUIT`` closes the app.
            headless: Run the app without a terminal (for tests).
        """
        self._queue = queue
        self._headless = headless
        self._app = None
        self._reader_task: Optional[asyncio.Task] = None

    def start(self) -> Process:
        """Launch the app in a separate process."""
        process = Process(target=self._app_process, name="tail-app")
        process.start()
        return process

    def run(self):
        """Launch the app in a separate process and wait for it to exit."""
        self.start().join()

    def _app_process(self):
        # Ctrl-C belongs to the pipeline process, which closes the app when
        # it shuts down. The app's terminal is in raw mode anyway, so this
        # only matters while it starts or exits.
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        # Make sure our standard file descriptors are those of the parent.
        sys.__stdin__ = os.fdopen(0, "r", buffering=1)
        sys.__stdout__ = os.fdopen(1, "w", buffering=1)
        sys.__stderr__ = os.fdopen(2, "w", buffering=1)

        self.run_app()

    def run_app(self):
        """Run the app in this process until it exits."""
        from pipecat_tail.app import TailApp

        self._app = TailApp(
            on_mount=self._on_mount,
            on_shutdown=self._on_shutdown,
            url="this process",
            demo_hint=False,
        )
        asyncio.run(self._app.run_async(headless=self._headless))

    async def _on_mount(self):
        loop = asyncio.get_running_loop()
        inbox: asyncio.Queue = asyncio.Queue()
        # A daemon thread rather than the loop's executor: ``asyncio.run()``
        # waits for executor threads when the app exits, and a read blocked
        # on a queue nobody writes to anymore would keep it from exiting.
        threading.Thread(
            target=self._read_queue, args=(loop, inbox), name="tail-queue", daemon=True
        ).start()
        self._reader_task = asyncio.create_task(self._handle_messages(inbox))

    async def _on_shutdown(self):
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        self._reader_task = None

    def _read_queue(self, loop: asyncio.AbstractEventLoop, inbox: asyncio.Queue):
        while True:
            try:
                message = self._queue.get()
            except (EOFError, OSError):
                # The pipeline process is gone.
                message = QUIT
            try:
                loop.call_soon_threadsafe(inbox.put_nowait, message)
            except RuntimeError:
                # The app has exited.
                return
            if message is None or message == QUIT:
                return

    async def _handle_messages(self, inbox: asyncio.Queue):
        while True:
            message = await inbox.get()
            if message is None:
                return
            if message == QUIT:
                if self._app:
                    self._app.exit()
                return
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

        output = _TerminalOutput()
        process = self._start_app()
        output.hide()
        app_task = asyncio.create_task(asyncio.to_thread(process.join))
        run_task = asyncio.create_task(super().run(auto_end=auto_end))
        try:
            await asyncio.wait([app_task, run_task], return_when=asyncio.FIRST_COMPLETED)
            if not run_task.done():
                # The app was closed: the terminal is ours again, so the
                # pipeline stopping shows up in it.
                output.restore()
                logger.info("ᓚᘏᗢ Tail: closed, stopping the bot")
                await self.cancel(reason="Tail closed")
                await run_task
            # No more messages. The app stays open so the session can be read.
            self._queue.put(None)
            await app_task
            output.restore()
            run_task.result()
        except asyncio.CancelledError:
            # The bot is being shut down from outside: close the app, then end
            # the pipeline in view, so nothing is left waiting on the other.
            await self._close_app(process, app_task)
            output.restore()
            if not run_task.done():
                await self.cancel(reason="Shutting down")
            await asyncio.wait([run_task])
            raise
        finally:
            output.restore()
            # Messages the app will never read must not hold up our exit.
            self._queue.cancel_join_thread()

    async def _close_app(self, process: Process, app_task: asyncio.Task):
        self._queue.put(QUIT)
        await asyncio.wait([app_task], timeout=APP_QUIT_TIMEOUT_SECS)
        if not app_task.done():
            process.terminate()
            await asyncio.wait([app_task])

    def _start_app(self) -> Process:
        return TailAppProcess(self._queue).start()
