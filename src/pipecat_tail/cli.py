#!/usr/bin/env python

#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail standalone application.

Connects to a Tail server, local or remote, and shows the session as it
happens. Reconnects on its own when the bot restarts. ``--demo`` plays a
scripted session instead, to try Tail without a bot.

Registered as the ``tail`` command of the ``pipecat`` CLI.
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer
from loguru import logger

from pipecat_tail.app import TailApp
from pipecat_tail.client import TailClient

DEFAULT_URL = "ws://localhost:9292"


class PipecatTail:
    """Standalone Tail application driven by a websocket connection."""

    def __init__(self, *, url: str = DEFAULT_URL, report_path: Optional[Path] = None):
        """Initialize the application.

        Args:
            url: Tail server URL.
            report_path: Where to write a report of the session on quit.
        """
        self._url = url
        self._app = TailApp(
            on_mount=self._on_mount,
            on_shutdown=self._on_shutdown,
            action_connect=self._on_connect,
            url=url,
            report_path=report_path,
        )
        self._client = TailClient(
            url, on_message=self._app.handle_message, on_status=self._app.handle_status
        )

    async def run(self) -> None:
        """Run the app until it quits."""
        await self._app.run_async()

    async def _on_mount(self) -> None:
        # The app owns the terminal; keep our own logs out of it.
        logger.remove()
        self._client.start()

    async def _on_shutdown(self) -> None:
        await self._client.stop()
        logger.add(sys.stderr)

    async def _on_connect(self) -> None:
        self._client.reconnect_now()


class PipecatTailDemo:
    """Standalone Tail application playing the scripted demo session."""

    def __init__(self, *, speed: float = 1.0):
        """Initialize the application.

        Args:
            speed: Playback speed.
        """
        self._speed = speed
        self._app = TailApp(on_mount=self._on_mount, on_shutdown=self._on_shutdown, url="demo")
        self._task: Optional[asyncio.Task] = None

    async def run(self) -> None:
        """Run the app until it quits."""
        await self._app.run_async()

    async def _on_mount(self) -> None:
        logger.remove()
        await self._app.handle_status("connecting")
        self._task = asyncio.create_task(self._play())

    async def _on_shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.add(sys.stderr)

    async def _play(self) -> None:
        from pipecat_tail import demo

        # Let the waiting screen show for a moment, like a bot starting up.
        await asyncio.sleep(1.5)
        await self._app.handle_status("connected", "demo")
        async for message in demo.play(loop=True, speed=self._speed):
            await self._app.handle_message(message)


def version_callback(value: bool):
    """Print the version and exit."""
    if value:
        from pipecat_tail.__version__ import version

        typer.echo(f"ᓚᘏᗢ Pipecat Tail Version: {typer.style(version, fg=typer.colors.GREEN)}")
        raise typer.Exit()


entrypoint_cli_typer = typer.Typer(
    no_args_is_help=False,
    add_completion=False,
    invoke_without_command=True,
    rich_markup_mode="markdown",
    short_help="Monitor Pipecat sessions in real-time",
    help="ᓚᘏᗢ Pipecat Tail. See website at https://github.com/pipecat-ai/tail",
)


@entrypoint_cli_typer.callback()
def cli(
    ctx: typer.Context,
    _version: bool = typer.Option(None, "--version", callback=version_callback, help="CLI version"),
    url: str = typer.Option(DEFAULT_URL, "-u", "--url", help="URL of the Tail server"),
    demo: bool = typer.Option(
        False, "--demo", help="Play a scripted session instead of connecting to a bot"
    ),
    target: float = typer.Option(
        1.0,
        "--target",
        help="Response time to aim for, in seconds: green under it, amber up to 1.6 times it",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="No UI: print the session as JSON lines (turns, errors, a summary) and exit "
        "when the bot goes away",
    ),
    report: Optional[Path] = typer.Option(
        None,
        "--report",
        help="Write a report of the session here on exit: Markdown for .md, JSON otherwise",
    ),
):
    """Pipecat Tail command."""
    if ctx.invoked_subcommand is not None:
        return
    from pipecat_tail.widgets import palette

    palette.set_latency_target(target)
    if as_json:
        from pipecat_tail.report import JsonStream

        # Keep stdout for the JSON lines.
        logger.remove()
        logger.add(sys.stderr, level="WARNING")
        stream = JsonStream(url, report_path=report)
        try:
            asyncio.run(stream.run())
        except KeyboardInterrupt:
            pass
        return
    app = PipecatTailDemo() if demo else PipecatTail(url=url, report_path=report)
    asyncio.run(app.run())


@entrypoint_cli_typer.command("setup-file")
def setup_file():
    """Print the path to pass in PIPECAT_SETUP_FILES."""
    import pipecat_tail.setup

    typer.echo(pipecat_tail.setup.__file__)


if __name__ == "__main__":
    entrypoint_cli_typer()
