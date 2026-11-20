#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Tail app in the bot's own terminal.

The app runs in a process of its own, fed through a queue, so drawing the
screen never holds up the bot's event loop. A process has one app, shared by
every ``TailWorker`` that shows the dashboard here: a development runner
starts the bot again for every call, and the calls take turns in the one app,
which stays open until it's closed.
"""

import asyncio
import os
import signal
import sys
import tempfile
import threading
from multiprocessing import Process, Queue
from typing import IO, Optional

QUIT = "quit"
"""Put on the app's queue to close the app."""

APP_QUIT_TIMEOUT_SECS = 5.0
"""How long the app gets to close when asked before its process is terminated."""


class _TerminalOutput:
    """Keeps this process's output off the terminal while the app draws on it.

    The app runs in its own process, so anything this one writes to the
    terminal (logs, prints, warnings) would land on top of it. Its stdout and
    stderr go to a file meanwhile, and when the app closes they point back at
    the terminal, which then shows what was written, as if the app had been a
    window over it: a call that ended while the app was open still shows the
    bot shutting down. Log handlers stay as they were, and output going to a
    file or a pipe is left alone.
    """

    def __init__(self):
        self._saved: list[tuple[int, int]] = []
        self._kept: Optional[IO[bytes]] = None

    def hide(self):
        """Send stdout and stderr to a file while they are a terminal."""
        self._flush()
        self._kept = tempfile.TemporaryFile()
        for fd in (1, 2):
            if os.isatty(fd):
                self._saved.append((fd, os.dup(fd)))
                os.dup2(self._kept.fileno(), fd)

    def restore(self):
        """Point stdout and stderr back at the terminal, and show what they wrote."""
        if not self._saved:
            return
        self._flush()
        for fd, saved in self._saved:
            os.dup2(saved, fd)
            os.close(saved)
        terminal = self._saved[-1][0]
        self._saved = []
        if self._kept is not None:
            self._kept.seek(0)
            while chunk := self._kept.read(65536):
                os.write(terminal, chunk)
            self._kept.close()
            self._kept = None

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
            queue: Queue the app reads messages from. ``QUIT`` closes the app.
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
            if message == QUIT:
                return

    async def _handle_messages(self, inbox: asyncio.Queue):
        while True:
            message = await inbox.get()
            if message == QUIT:
                if self._app:
                    self._app.exit()
                return
            if self._app:
                await self._app.handle_message(message)


class _Dashboard:
    """The app of this process, shared by the workers that show it here.

    A development runner starts the bot again for every call, each time with
    a ``TailWorker`` of its own. They all report to the one app, which stays
    open from call to call until it's closed: two apps would fight over the
    terminal.
    """

    def __init__(self):
        self.queue: Optional[Queue] = None
        self.process: Optional[Process] = None
        self.closed: Optional[asyncio.Task] = None
        self.output = _TerminalOutput()

    def open(self) -> asyncio.Task:
        """Open the app unless it is, and return what ends when it closes."""
        if self.closed is None or self.closed.done():
            self.queue = Queue()
            self.process = self.start_app(self.queue)
            self.output.hide()
            self.closed = asyncio.create_task(asyncio.to_thread(self.process.join))
            # The terminal is the bot's again: show what it wrote meanwhile.
            self.closed.add_done_callback(lambda _: self.output.restore())
            # Messages the app will never read must not hold up our exit.
            self.queue.cancel_join_thread()
        return self.closed

    def start_app(self, queue: Queue) -> Process:
        """Start the app's process."""
        return TailAppProcess(queue).start()

    async def close(self) -> None:
        """Ask the app to close, and end its process if it doesn't."""
        if self.closed is None or self.queue is None:
            return
        self.queue.put(QUIT)
        await asyncio.wait([self.closed], timeout=APP_QUIT_TIMEOUT_SECS)
        if not self.closed.done() and self.process is not None:
            self.process.terminate()
            await asyncio.wait([self.closed])

    async def emit(self, message: dict) -> None:
        """Deliver a message to the app that is open."""
        if self.queue is not None:
            self.queue.put(message)


DASHBOARD = _Dashboard()
"""This process's app."""


async def to_dashboard(message: dict) -> None:
    """Deliver a message to whichever app this process has open."""
    await DASHBOARD.emit(message)
