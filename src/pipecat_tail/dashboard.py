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
"""How long the app gets to quit once its connection ends before it's terminated."""


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
        terminals = [fd for fd in (1, 2) if os.isatty(fd)]
        if not terminals:
            return
        self._flush()
        self._kept = tempfile.TemporaryFile()
        for fd in terminals:
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
    open from call to call until the user quits it or the bot's process ends:
    two apps would fight over the terminal.
    """

    def __init__(self):
        self.server: Optional[TailWebSocketServer] = None
        self.closed: Optional[asyncio.Task] = None
        self.output = _TerminalOutput()

    def can_open(self) -> bool:
        """Whether there's a terminal to show the app in: it's open, or could be."""
        is_open = self.closed is not None and not self.closed.done()
        return is_open or (os.isatty(0) and os.isatty(1))

    def open(self, log_level: str) -> asyncio.Task:
        """Open the app unless it is, and return what ends when it closes.

        The app gets the process's logs from ``log_level`` up, once, however
        many workers report to it. What's sent before it connects waits for it.
        The task's result is whether the user quit it, rather than it crashing.
        """
        if self.closed is None or self.closed.done():
            self.server = TailWebSocketServer(host="127.0.0.1", port=0)
            self.closed = asyncio.create_task(self._show(self.server, log_level))
        return self.closed

    def start_app(self, url: str) -> subprocess.Popen:
        """Run ``pipecat tail`` in this terminal, connected to ``url``."""
        command = [sys.executable, "-m", "pipecat_tail.cli", "--url", url, "--exit-on-disconnect"]
        return subprocess.Popen(command)

    async def _show(self, server: TailWebSocketServer, log_level: str) -> bool:
        from pipecat_tail.observer import forward_logs, stop_forwarding_logs

        logs = forward_logs(self._send_log, log_level)
        process = None
        try:
            await server.start()
            process = self.start_app(server.url)
            self.output.hide()
            await asyncio.to_thread(process.wait)
        finally:
            # The app quits once its connection ends, as when the bot's
            # process shuts down while it's open.
            stop_forwarding_logs(logs)
            await server.stop()
            self.server = None
            if process is not None:
                await asyncio.to_thread(_end, process)
            # The terminal is the bot's again: show what it wrote meanwhile.
            self.output.restore()
        # Quitting exits with 0; a crash doesn't.
        return process is not None and process.returncode == 0

    async def emit(self, message: dict) -> None:
        """Deliver a message to the app that is open."""
        if self.server is not None:
            await self.server.emit(message)

    async def _send_log(self, model: "TailMessage") -> None:
        await self.emit(model.model_dump(mode="json", exclude_none=True))


def _end(process: subprocess.Popen) -> None:
    """Wait for the app to quit, and terminate it if it doesn't."""
    try:
        process.wait(APP_QUIT_TIMEOUT_SECS)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait()


DASHBOARD = _Dashboard()
"""This process's app."""


async def to_dashboard(message: dict) -> None:
    """Deliver a message to whichever app this process has open."""
    await DASHBOARD.emit(message)
