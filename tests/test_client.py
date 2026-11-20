#
# Copyright (c) 2025-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio

import pytest
from loguru import logger

from pipecat_tail.client import TailClient
from pipecat_tail.sink import TailWebSocketServer


@pytest.mark.asyncio
async def test_a_message_the_handler_fails_on_keeps_the_connection():
    """One bad message is skipped: it used to end the connection, and with
    it `pipecat tail --json`."""
    logger.remove()
    received: list[str] = []
    statuses: list[str] = []

    async def on_message(message):
        if message["type"] == "tail-broken":
            raise TypeError("'NoneType' object is not iterable")
        received.append(message["type"])

    async def on_status(status, detail=None):
        statuses.append(status)

    server = TailWebSocketServer(host="127.0.0.1", port=0)
    await server.start()
    client = TailClient(server.url, on_message=on_message, on_status=on_status)
    client.start()
    try:
        await server.emit({"type": "tail-broken"})
        await server.emit({"type": "tail-ready"})
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.05)
        assert received == ["tail-ready"]
        assert "disconnected" not in statuses
    finally:
        await client.stop()
        await server.stop()
        logger.add(lambda _: None)


async def connect_and_read(url: str, *, count: int) -> list[dict]:
    """Connect a plain websocket client and read ``count`` messages."""
    import json

    import websockets

    async with websockets.connect(url) as client:
        return [json.loads(await asyncio.wait_for(client.recv(), 5.0)) for _ in range(count)]


@pytest.mark.asyncio
async def test_every_client_gets_the_state_that_fell_out_of_the_buffer():
    """A client that connects late still sees how the pipeline started and
    its LLM context, before the latest messages, in order."""
    logger.remove()
    server = TailWebSocketServer(host="127.0.0.1", port=0, buffer_size=3)
    await server.start()
    try:
        await server.emit({"type": "tail-pipeline-started", "worker": "bot"})
        await server.emit({"type": "tail-llm-context", "worker": "bot", "data": {"items": []}})
        await server.emit({"type": "tail-llm-context", "worker": "bot", "data": {"items": [1]}})
        for n in range(5):
            await server.emit({"type": "tail-log", "data": {"message": str(n)}})
        for _ in range(2):
            # The first client, and one after it, get the same.
            messages = await connect_and_read(server.url, count=5)
            assert [m["type"] for m in messages] == [
                "tail-pipeline-started",
                "tail-llm-context",
                "tail-log",
                "tail-log",
                "tail-log",
            ]
            # Only the latest context is kept.
            assert messages[1]["data"]["items"] == [1]
            assert [m["data"]["message"] for m in messages[2:]] == ["2", "3", "4"]
            await asyncio.sleep(0.1)
    finally:
        await server.stop()
        logger.add(lambda _: None)


@pytest.mark.asyncio
async def test_a_client_coming_back_skips_what_it_has():
    """Reconnecting to the same bot doesn't repeat turns; a restarted bot
    is a new run, and everything from it counts."""
    logger.remove()
    received: list[str] = []

    async def on_message(message):
        received.append(message["data"]["n"])

    async def wait_for(count: int):
        for _ in range(100):
            if len(received) >= count:
                return
            await asyncio.sleep(0.05)

    server = TailWebSocketServer(host="127.0.0.1", port=0)
    await server.start()
    client = TailClient(server.url, on_message=on_message, min_backoff_secs=0.05)
    client.start()
    try:
        for n in ("a", "b"):
            await server.emit({"type": "tail-log", "data": {"n": n}})
        await wait_for(2)
        # The connection drops, and the client comes back to the same bot.
        await server._client.close()
        await asyncio.sleep(0.3)
        await server.emit({"type": "tail-log", "data": {"n": "c"}})
        await wait_for(3)
        assert received == ["a", "b", "c"]
    finally:
        await client.stop()
        await server.stop()

    # The bot restarted: a new server, a new run, on the same port.
    restarted = TailWebSocketServer(host="127.0.0.1", port=int(server.url.rsplit(":", 1)[1]))
    await restarted.start()
    client = TailClient(restarted.url, on_message=on_message, min_backoff_secs=0.05)
    client._server_run, client._seq = server._run, 99
    client.start()
    try:
        await restarted.emit({"type": "tail-log", "data": {"n": "d"}})
        await wait_for(4)
        assert received == ["a", "b", "c", "d"]
    finally:
        await client.stop()
        await restarted.stop()
        logger.add(lambda _: None)
