#!/usr/bin/env python

#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail standalone application.

Connects to a Tail server, local or remote, and shows the session as it
happens. Reconnects on its own when the bot restarts. Can also record the
session to a file, or replay a recorded one without a bot.

Registered as the ``tail`` command of the ``pipecat`` CLI.
"""

import asyncio
import sys
from typing import Optional

import typer
from loguru import logger

from pipecat_tail.app import TailApp
from pipecat_tail.client import TailClient
from pipecat_tail.session import replay_session

DEFAULT_URL = "ws://localhost:9292"


class PipecatTail:
    """Standalone Tail application driven by a websocket connection."""

    def __init__(self, *, url: str = DEFAULT_URL, save: Optional[str] = None):
        """Initialize the application.

        Args:
            url: Tail server URL.
            save: Session file to record to from the start.
        """
        self._url = url
        self._app = TailApp(
            on_mount=self._on_mount,
            on_shutdown=self._on_shutdown,
            action_connect=self._on_connect,
            save_path=save,
        )
        self._client = TailClient(
            url, on_message=self._app.handle_message, on_status=self._app.handle_status
        )
        self._logger_id: Optional[int] = None

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


class PipecatTailReplay:
    """Standalone Tail application driven by a recorded session."""

    def __init__(self, *, path: str, speed: float = 1.0):
        """Initialize the application.

        Args:
            path: Session file to replay.
            speed: Playback speed multiplier. ``0`` loads everything at once.
        """
        self._path = path
        self._speed = speed
        self._app = TailApp(on_mount=self._on_mount, on_shutdown=self._on_shutdown)
        self._task: Optional[asyncio.Task] = None

    async def run(self) -> None:
        """Run the app until it quits."""
        await self._app.run_async()

    async def _on_mount(self) -> None:
        logger.remove()
        await self._app.handle_status("connected", f"replay {self._path}")
        self._task = asyncio.create_task(self._replay())

    async def _on_shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.add(sys.stderr)

    async def _replay(self) -> None:
        try:
            async for message in replay_session(self._path, speed=self._speed):
                await self._app.handle_message(message)
        except FileNotFoundError:
            await self._app.handle_status("error", f"no such file: {self._path}")
            return
        await self._app.handle_status("disconnected", "replay finished")


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
    save: Optional[str] = typer.Option(
        None, "-s", "--save", help="Record the session to this JSON Lines file"
    ),
    replay: Optional[str] = typer.Option(
        None, "-r", "--replay", help="Replay a recorded session file instead of connecting"
    ),
    speed: float = typer.Option(
        1.0, "--speed", help="Replay speed multiplier (0 loads everything at once)"
    ),
):
    """Pipecat Tail command."""
    if ctx.invoked_subcommand is not None:
        return
    if replay:
        app = PipecatTailReplay(path=replay, speed=speed)
    else:
        app = PipecatTail(url=url, save=save)
    asyncio.run(app.run())


@entrypoint_cli_typer.command("setup-file")
def setup_file():
    """Print the path to pass in PIPECAT_SETUP_FILES."""
    import pipecat_tail.setup

    typer.echo(pipecat_tail.setup.__file__)


if __name__ == "__main__":
    entrypoint_cli_typer()
