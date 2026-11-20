#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Tail app in the bot's own terminal.

It is ``pipecat tail``, started for you: the bot serves its messages on a
local port, as it would for a remote Tail, and runs ``pipecat tail`` in this
terminal connected to it, as a Python process of its own. A fork of the bot
would hold its sockets (its port among them) and signal handlers, and the
locks its threads held at that moment. The app quits when the connection
ends, because the bot is done with it or is gone.

A process has one app, shared by every ``TailWorker`` that shows the dashboard
here: a development runner starts the bot again for every call, and the calls
take turns in the one app, which stays open until it's closed.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
from typing import IO, TYPE_CHECKING, Optional

from pipecat_tail.sink import TailWebSocketServer

if TYPE_CHECKING:
    from pipecat_tail.messages import TailMessage

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


class _Dashboard:
    """The app of this process, shared by the workers that show it here.

    A development runner starts the bot again for every call, each time with
    a ``TailWorker`` of its own. They all report to the one app, which stays
    open from call to call until it's closed: two apps would fight over the
    terminal.
    """

    def __init__(self):
        self.server: Optional[TailWebSocketServer] = None
        self.process: Optional[subprocess.Popen] = None
        self.closed: Optional[asyncio.Task] = None
        self.output = _TerminalOutput()

    def open(self, log_level: str) -> asyncio.Task:
        """Open the app unless it is, and return what ends when it closes.

        The app gets the process's logs from ``log_level`` up, once, however
        many workers report to it. What's sent before it connects waits for it.
        """
        if self.closed is None or self.closed.done():
            self.server = TailWebSocketServer(host="127.0.0.1", port=0)
            self.process = None
            self.closed = asyncio.create_task(self._show(self.server, log_level))
        return self.closed

    def start_app(self, url: str) -> subprocess.Popen:
        """Run ``pipecat tail`` in this terminal, connected to ``url``."""
        command = [sys.executable, "-m", "pipecat_tail.cli", "--url", url, "--exit-on-disconnect"]
        return subprocess.Popen(command)

    async def _show(self, server: TailWebSocketServer, log_level: str) -> None:
        from pipecat_tail.observer import forward_logs, stop_forwarding_logs

        logs = forward_logs(self._send_log, log_level)
        try:
            await server.start()
            self.process = self.start_app(server.url)
            self.output.hide()
            await asyncio.to_thread(self.process.wait)
        finally:
            stop_forwarding_logs(logs)
            await server.stop()
            self.server = None
            # The terminal is the bot's again: show what it wrote meanwhile.
            self.output.restore()

    async def close(self) -> None:
        """Ask the app to close, and end its process if it doesn't."""
        if self.closed is None or self.closed.done():
            return
        if self.server is not None:
            # The app quits when its connection ends.
            await self.server.stop()
        await asyncio.wait([self.closed], timeout=APP_QUIT_TIMEOUT_SECS)
        if not self.closed.done() and self.process is not None:
            self.process.terminate()
            await asyncio.wait([self.closed])

    async def emit(self, message: dict) -> None:
        """Deliver a message to the app that is open."""
        if self.server is not None:
            await self.server.emit(message)

    async def _send_log(self, model: "TailMessage") -> None:
        await self.emit(model.model_dump(mode="json", exclude_none=True))


DASHBOARD = _Dashboard()
"""This process's app."""


async def to_dashboard(message: dict) -> None:
    """Deliver a message to whichever app this process has open."""
    await DASHBOARD.emit(message)
