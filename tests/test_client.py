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
