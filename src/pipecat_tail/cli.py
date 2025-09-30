#!/usr/bin/env python

#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail standalone application.

This is the standalone application for Tail. It connects to a Tail observer,
whether local or remote, to receive the current conversation, service metrics,
audio levels or system logs.
"""

import argparse
import asyncio
import json
import sys

import websockets
from loguru import logger

from pipecat_tail.app import TailApp

DEFAULT_URL = "ws://localhost:9292"


class PipecatTail:
    """Standalone Tail application.

    This is the standalone Tail application. It connects to an observer, whether
    local or remote, and updates the UI based on the received messages.
    """

    def __init__(self, *, url: str = DEFAULT_URL):
        """Initialize the Tail application.

        It will try to connect to the provided URL. If it can not connect, it is
        possible to connect later from the UI.

        Args:
            url: Observer URL.
        """
        self._url = url

        self._app = TailApp(
            on_mount=self._app_on_mount,
            on_shutdown=self._app_on_shutdown,
            action_connect=self._app_action_connect,
        )
        self._ws = None
        self._receiver_task = None

    async def run(self):
        """Run the application event loop asynchronously."""
        await self._app.run_async()

    async def _app_on_mount(self):
        """App lifecycle hook called when the UI is started."""
        logger.remove()
        self._logger_id = logger.add(self._logger_sink)
        await self._connect()

    async def _app_on_shutdown(self):
        """App lifecycle hook called when the UI is shutting down."""
        await self._disconnect()
        logger.remove(self._logger_id)
        logger.add(sys.stderr)

    async def _app_action_connect(self):
        """UI action handler to (re)connect to the observer."""
        await self._connect()

    async def _logger_sink(self, message: str):
        await self._app.handle_system_log(message)

    async def _connect(self):
        """Connect to the observer."""
        try:
            self._ws = await websockets.connect(self._url)
            self._receiver_task = asyncio.create_task(self._receiver_task_handler())
        except Exception as e:
            logger.error(f"Unable to connect to {self._url}: {e}")
            await self._app.handle_system_status("ERROR")

    async def _disconnect(self):
        """Disconnect from the observer."""
        if self._ws:
            await self._ws.close()
        if self._receiver_task:
            await self._receiver_task

    async def _receiver_task_handler(self):
        """Receive messages and forward to the app."""
        async for raw_message in self._ws:
            message = json.loads(raw_message)
            await self._app.handle_message(message)
        await self._app.clear()


async def run(url: str):
    """Asynchronous entrypoint for running the standalon app."""
    app = PipecatTail(url=url)
    await app.run()


def main():
    """Synchronous entrypoint for running the standalon app."""
    parser = argparse.ArgumentParser(description="Tail, a terminal dashboard for Pipecat.")
    parser.add_argument(
        "-u",
        "--url",
        type=str,
        default=DEFAULT_URL,
        help="URL for the Tail observer (default: %(default)s)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.url))


if __name__ == "__main__":
    main()
