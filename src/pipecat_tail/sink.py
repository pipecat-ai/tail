#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Sinks that deliver Tail messages to the Tail app.

A sink is anything with an ``async emit(message: dict)`` method. Tail ships
two:

- ``TailWebSocketServer`` serves one Tail client over a websocket. Every
  client that connects, late or again, gets the latest messages and the
  state from before them, so it shows the session as it is.
- ``CallbackSink`` calls a coroutine. ``TailWorker`` feeds the app with it, and
  tests and embedders use it.
"""

import asyncio
import json
import uuid
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Optional, Protocol

from loguru import logger

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9292
DEFAULT_BUFFER_SIZE = 5000

PINNED_TYPES = frozenset(
    {
        "tail-ready",
        "tail-pipeline-started",
        "tail-pipeline-finished",
        "tail-startup",
        "tail-transport-timing",
        "tail-llm-context",
        "tail-workers",
    }
)
"""Messages that hold state: the latest of each, per worker, outlives the buffer."""


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


class TailWebSocketServer:
    """Websocket server that streams Tail messages to a single client.

    The server is started with ``start()`` and stopped with ``stop()``. It can
    be embedded in an observer (standalone mode) or in a worker (runner mode).

    A client that connects, the first, a late one or one coming back, gets
    the latest ``buffer_size`` messages, and before them the latest of each
    kind that holds state (``PINNED_TYPES``): how the pipeline started, the
    LLM context, the workers. Every message carries the server's ``run`` and
    a ``seq`` number, so a client coming back skips what it already has.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        on_client_connected: Optional[Callable[[], Awaitable[None]]] = None,
        keepalive: bool = True,
    ):
        """Initialize the server.

        Args:
            host: Address to bind to.
            port: Port to bind to; 0 takes a free one.
            buffer_size: How many of the latest messages a client gets when it
                connects. The oldest messages are dropped first.
            on_client_connected: Called after the buffered messages have been
                replayed to a new client.
            keepalive: Ping the client, and drop it when it doesn't answer.
        """
        self._host = host
        self._port = port
        self._recent: Deque[tuple[int, str]] = deque(maxlen=buffer_size)
        self._pinned: dict[tuple[Any, str], tuple[int, str]] = {}
        self._run = uuid.uuid4().hex[:12]
        self._seq = 0
        self._on_client_connected = on_client_connected
        self._keepalive = keepalive
        self._client = None
        self._server = None
        self._send_queue: Optional[asyncio.Queue] = None
        self._send_task: Optional[asyncio.Task] = None

    @property
    def url(self) -> str:
        """URL a Tail client should connect to."""
        return f"ws://{self._host}:{self._port}"

    async def start(self) -> None:
        """Start serving."""
        if self._server:
            return
        from websockets.asyncio.server import serve

        self._send_queue = asyncio.Queue()
        self._send_task = asyncio.create_task(self._send_task_handler())
        self._server = await serve(
            self._client_handler,
            self._host,
            self._port,
            # 20 s is websockets' own default.
            ping_interval=20.0 if self._keepalive else None,
        )
        if not self._port:
            self._port = list(self._server.sockets)[0].getsockname()[1]
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
        """Queue one message for the client, and keep it for the next."""
        self._seq += 1
        message = {**message, "run": self._run, "seq": self._seq}
        encoded = json.dumps(message, separators=(",", ":"), default=str)
        self._recent.append((self._seq, encoded))
        kind = message.get("type")
        if kind in PINNED_TYPES:
            self._pinned[(message.get("worker"), kind)] = (self._seq, encoded)
        if self._client and self._send_queue:
            self._send_queue.put_nowait(encoded)

    def _replay(self) -> list[str]:
        """The latest messages, after the state messages that fell out of them."""
        oldest = self._recent[0][0] if self._recent else self._seq + 1
        pinned = sorted(p for p in self._pinned.values() if p[0] < oldest)
        return [encoded for _, encoded in pinned + list(self._recent)]

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
            # What the client missed goes first, through the send queue so
            # the order holds, and what's new after it.
            assert self._send_queue
            for encoded in self._replay():
                self._send_queue.put_nowait(encoded)
            if self._on_client_connected:
                await self._on_client_connected()
            async for _ in client:
                pass
        except Exception as e:
            logger.debug(f"ᓚᘏᗢ Tail: client closed: {e}")
        finally:
            logger.debug("ᓚᘏᗢ Tail: client disconnected")
            if self._client is client:
                self._client = None
