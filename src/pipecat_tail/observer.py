#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail is a terminal user interface for the Pipecat.

This module provides an observer that you can easily pass to your Pipeline task
observers.

The Tail observer waits for a client connection (only one connection allowed at
a time) and sends RTVI messages to a Tail client.

With the Tail client you can see:

- The actual conversation.
- Audio levels for both user and agent.
- Different service metrics.
- System logs.
"""

import asyncio

from loguru import logger
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIObserverParams
from pydantic import BaseModel
from websockets import ConnectionClosedOK, serve

from pipecat_tail.rtvi import RTVITailReadyMessage


class TailObserver(RTVIObserver):
    """An RTVI websocket-based observer.

    The Tail client connects to this observer in order to provide useful
    information for the terminal user interface.

    """

    def __init__(self, *, host: str = "localhost", port: int = 9292):
        """Initialize the Tail observer.

        Args:
            host: Host address to bind the WebSocket server to. Defaults to "localhost".
            port: Port number to bind the WebSocket server to. Defaults to 9292.
        """
        super().__init__(
            params=RTVIObserverParams(
                user_audio_level_enabled=True,
                bot_audio_level_enabled=True,
                system_logs_enabled=True,
            )
        )
        self._host = host
        self._port = port

        self._client = None
        self._server_future = asyncio.get_running_loop().create_future()
        self._server_task = asyncio.create_task(self._start_task_handler())

    async def cleanup(self):
        """Clean up resources and close the Tail server."""
        await super().cleanup()

        if self._client:
            await self._client.close(reason="Tail shutting down")

        if not self._server_future.done():
            self._server_future.set_result(None)

        if self._server_task:
            await self._server_task

    async def send_rtvi_message(self, model: BaseModel, exclude_none: bool = True):
        """Send an RTVI message to the Tail client.

        Args:
            model: Pydantic model to serialize and send.
            exclude_none: Whether to exclude ``None`` fields during serialization.

        Returns:
            None
        """
        message = model.model_dump_json(exclude_none=exclude_none)
        await self._send(message)

    async def _start_task_handler(self):
        """Start the Tail server and handle incoming connections.

        This method runs in a separate task and manages the websocket server lifecycle.
        """
        async with serve(self._server_handler, self._host, self._port):
            logger.debug(f"ᓚᘏᗢ Tail running at ws://{self._host}:{self._port}")
            await self._server_future

    async def _server_handler(self, client):
        """Handle a new Tail client connection.

        Args:
            client: The websocket client connection.
        """
        if self._client:
            logger.warning("ᓚᘏᗢ Tail: a client is already connected, only one client allowed")
            return

        logger.debug(f"ᓚᘏᗢ Tail: client connected {client.remote_address}")

        self._client = client
        try:
            # Inform the client about our setup.
            await self.send_rtvi_message(RTVITailReadyMessage())

            # Keep alive
            async for _ in self._client:
                pass
        except ConnectionClosedOK:
            pass
        except Exception as e:
            logger.warning(f"ᓚᘏᗢ Tail: client closed with error: {e}")
        finally:
            logger.debug("ᓚᘏᗢ Tail: client disconnected")
            await self._reset_client()

    async def _reset_client(self):
        """Reset internal client reference after disconnection."""
        self._client = None

    async def _send(self, msg: str):
        """Send a message to the connected client.

        Args:
            msg: The message to send as a JSON-encoded string.
        """
        try:
            if self._client:
                await self._client.send(msg)
        except ConnectionClosedOK:
            pass
        except Exception as e:
            logger.warning(f"ᓚᘏᗢ Tail: client closed with error: {e}")
