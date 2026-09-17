#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Websocket client with automatic reconnection.

``TailClient`` connects to a Tail server, decodes messages and hands them to a
callback. When the connection drops, or cannot be made, it retries with
exponential backoff so restarting the bot never requires restarting Tail.
"""

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]
StatusHandler = Callable[[str, Optional[str]], Awaitable[None]]


class TailClient:
    """Connects to a Tail server and keeps the connection alive.

    Status callbacks receive ``connecting``, ``connected``, ``disconnected``
    or ``error`` plus an optional detail string.

    Args:
        url: Websocket URL of the Tail server.
        on_message: Called with every decoded message.
        on_status: Called when the connection state changes.
        reconnect: Whether to reconnect after a drop or a failed attempt.
        min_backoff_secs: First retry delay.
        max_backoff_secs: Longest retry delay.
    """

    def __init__(
        self,
        url: str,
        *,
        on_message: MessageHandler,
        on_status: Optional[StatusHandler] = None,
        reconnect: bool = True,
        min_backoff_secs: float = 0.5,
        max_backoff_secs: float = 10.0,
    ):
        """Initialize the client. See the class docstring for the arguments."""
        self._url = url
        self._on_message = on_message
        self._on_status = on_status
        self._reconnect = reconnect
        self._min_backoff = min_backoff_secs
        self._max_backoff = max_backoff_secs
        self._task: Optional[asyncio.Task] = None
        self._ws = None
        self._connected = False
        self._wake = asyncio.Event()

    @property
    def url(self) -> str:
        """The server URL."""
        return self._url

    @property
    def connected(self) -> bool:
        """Whether the client is currently connected."""
        return self._connected

    def start(self) -> None:
        """Start connecting in the background."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def reconnect_now(self) -> None:
        """Skip the current backoff wait and try to connect immediately."""
        self._wake.set()
        self.start()

    async def stop(self) -> None:
        """Disconnect and stop reconnecting."""
        self._reconnect = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._close()

    async def _close(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._connected:
            self._connected = False
            await self._status("disconnected", None)

    async def _status(self, status: str, detail: Optional[str]) -> None:
        if self._on_status:
            await self._on_status(status, detail)

    async def _run(self) -> None:
        import websockets

        backoff = self._min_backoff
        while True:
            await self._status("connecting", self._url)
            try:
                async with websockets.connect(self._url, max_size=None) as ws:
                    self._ws = ws
                    self._connected = True
                    backoff = self._min_backoff
                    await self._status("connected", self._url)
                    async for raw in ws:
                        try:
                            message = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("ᓚᘏᗢ Tail: dropping undecodable message")
                            continue
                        if isinstance(message, dict):
                            await self._on_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                if self._connected:
                    self._connected = False
                    await self._status("disconnected", detail)
                else:
                    await self._status("error", detail)
            else:
                self._connected = False
                await self._status("disconnected", None)
            finally:
                self._ws = None

            if not self._reconnect:
                return

            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, self._max_backoff)
