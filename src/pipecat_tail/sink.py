#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Sinks that deliver Tail messages to the Tail app.

A sink is anything with an ``async emit(message: dict)`` method. Tail ships
three:

- ``TailWebSocketServer`` serves one Tail client over a websocket. Messages
  produced before a client connects are buffered and replayed to the first
  client so it sees the session from the start.
- ``QueueSink`` puts messages on a ``multiprocessing.Queue``. ``TailRunner``
  uses it to feed the app running in a separate process.
- ``CallbackSink`` calls a coroutine. Tests and embedders use it.
"""

import asyncio
import json
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Optional, Protocol

from loguru import logger

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9292
DEFAULT_BUFFER_SIZE = 5000


class TailSink(Protocol):
    """Destination for Tail messages."""

    async def emit(self, message: dict[str, Any]) -> None:
        """Deliver one message.

        Args:
            message: JSON-compatible message.
        """
        ...


class CallbackSink:
    """Sink that hands every message to a coroutine."""

    def __init__(self, callback: Callable[[dict[str, Any]], Awaitable[None]]):
        """Initialize the sink.

        Args:
            callback: Coroutine function called with each message.
        """
        self._callback = callback

    async def emit(self, message: dict[str, Any]) -> None:
        """Deliver one message to the callback."""
        await self._callback(message)


class QueueSink:
    """Sink that puts messages on a ``multiprocessing.Queue``."""

    def __init__(self, queue):
        """Initialize the sink.

        Args:
            queue: A ``multiprocessing.Queue`` shared with the app process.
        """
        self._queue = queue

    async def emit(self, message: dict[str, Any]) -> None:
        """Put one message on the queue."""
        self._queue.put(message)


class TailWebSocketServer:
    """Websocket server that streams Tail messages to a single client.

    The server is started with ``start()`` and stopped with ``stop()``. It can
    be embedded in an observer (standalone mode) or in a worker (runner mode).
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        on_client_connected: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """Initialize the server.

        Args:
            host: Address to bind to.
            port: Port to bind to.
            buffer_size: How many messages to keep for a client that connects
                late. The oldest messages are dropped first.
            on_client_connected: Called after the buffered messages have been
                replayed to a new client.
        """
        self._host = host
        self._port = port
        self._buffer: Deque[str] = deque(maxlen=buffer_size)
        self._on_client_connected = on_client_connected
        self._client = None
        self._server = None
        self._send_queue: Optional[asyncio.Queue] = None
        self._send_task: Optional[asyncio.Task] = None
        self._first_client_seen = False

    @property
    def host(self) -> str:
        """Address the server binds to."""
        return self._host

    @property
    def port(self) -> int:
        """Port the server binds to."""
        return self._port

    @property
    def url(self) -> str:
        """URL a Tail client should connect to."""
        return f"ws://{self._host}:{self._port}"

    @property
    def connected(self) -> bool:
        """Whether a client is connected."""
        return self._client is not None

    async def start(self) -> None:
        """Start serving."""
        if self._server:
            return
        from websockets.asyncio.server import serve

        self._send_queue = asyncio.Queue()
        self._send_task = asyncio.create_task(self._send_task_handler())
        self._server = await serve(self._client_handler, self._host, self._port)
        logger.debug(f"ᓚᘏᗢ Tail running at {self.url}")

    async def stop(self) -> None:
        """Stop serving and disconnect the client.

        Messages already queued for a connected client are sent first.
        """
        if self._client and self._send_queue and not self._send_queue.empty():
            try:
                await asyncio.wait_for(self._send_queue.join(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.debug("ᓚᘏᗢ Tail: gave up flushing queued messages")
        if self._client:
            try:
                await self._client.close(reason="Tail shutting down")
            except Exception:
                pass
            self._client = None
        if self._send_task:
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
            self._send_task = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def emit(self, message: dict[str, Any]) -> None:
        """Queue one message for the client, buffering it if none is connected."""
        encoded = json.dumps(message, separators=(",", ":"), default=str)
        if not self._first_client_seen:
            self._buffer.append(encoded)
        if self._client and self._send_queue:
            self._send_queue.put_nowait(encoded)

    async def _send_task_handler(self) -> None:
        assert self._send_queue
        while True:
            encoded = await self._send_queue.get()
            try:
                client = self._client
                if not client:
                    continue
                try:
                    await client.send(encoded)
                except Exception as e:
                    logger.debug(f"ᓚᘏᗢ Tail: send failed, client gone: {e}")
                    self._client = None
            finally:
                self._send_queue.task_done()

    async def _client_handler(self, client) -> None:
        if self._client:
            logger.warning("ᓚᘏᗢ Tail: a client is already connected, only one client allowed")
            await client.close(reason="Only one Tail client allowed")
            return

        logger.debug(f"ᓚᘏᗢ Tail: client connected {client.remote_address}")
        try:
            self._client = client
            # The ready message goes first, then whatever the first client
            # missed, all through the send queue so the order holds.
            if self._on_client_connected:
                await self._on_client_connected()
            if not self._first_client_seen:
                self._first_client_seen = True
                assert self._send_queue
                for encoded in self._buffer:
                    self._send_queue.put_nowait(encoded)
                self._buffer.clear()
            async for _ in client:
                pass
        except Exception as e:
            logger.debug(f"ᓚᘏᗢ Tail: client closed: {e}")
        finally:
            logger.debug("ᓚᘏᗢ Tail: client disconnected")
            if self._client is client:
                self._client = None
