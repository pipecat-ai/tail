#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Runner integration for launching the Tail TUI alongside a Pipeline.

This module provides a ``TailRunner`` that runs a Pipecat ``PipelineTask`` and
the Tail Textual UI in parallel. Messages are passed through a ``multiprocessing
Queue`` to decouple the pipeline process from the UI process.
"""

import asyncio
import os
import sys
from multiprocessing import Process, Queue
from typing import Optional

from loguru import logger
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIObserverParams
from pydantic import BaseModel

from pipecat_tail.app import TailApp
from pipecat_tail.rtvi import RTVITailPipelineFinishedMessage, RTVITailReadyMessage


class TailRunnerObserver(RTVIObserver):
    """RTVI observer that forwards messages to a multiprocessing queue."""

    def __init__(self, queue: Queue):
        """Initialize the observer.

        Args:
            queue: The queue used to send serialized messages to the UI process.
        """
        # This will initialize the Tail logger which send system logs.
        super().__init__(
            params=RTVIObserverParams(
                user_audio_level_enabled=True,
                bot_audio_level_enabled=True,
                system_logs_enabled=True,
            )
        )
        self._queue = queue

    async def send_rtvi_message(self, model: BaseModel, exclude_none: bool = True):
        """Serialize and forward an RTVI message to the queue.

        Args:
            model: Pydantic model to serialize.
            exclude_none: Whether to exclude ``None`` fields during serialization.
        """
        message = model.model_dump(exclude_none=exclude_none)
        self._queue.put(message)


class TailAppProcess:
    """This class launches Tail dashboard in a separate process.

    We need to make sure this class doesn't contain anything that can't be
    pickled before starting the process.
    """

    def __init__(self, queue: Queue):
        """Initialize the process class.

        Args:
            queue: The queue to communicate with the process.
        """
        self._queue = queue
        self._app_process_task = None

    def run(self):
        """Launch the app in a separate process and wait for it to exit."""
        process = Process(target=self._app_process)
        process.start()
        process.join()

    def _app_process(self):
        """Create and run the app asynchronously."""
        # Make sure out standard file descriptors are those of the parent process.
        sys.__stdin__ = os.fdopen(0, "r", buffering=1)
        sys.__stdout__ = os.fdopen(1, "w", buffering=1)
        sys.__stderr__ = os.fdopen(2, "w", buffering=1)
        self._app = TailApp(on_mount=self._app_on_mount, on_shutdown=self._app_on_shutdown)
        asyncio.run(self._app.run_async())

    async def _app_on_mount(self):
        """Hook called when the app mounts to start queue processing."""
        self._app_process_task = asyncio.create_task(self._app_process_queue())

    async def _app_on_shutdown(self):
        """Hook called on app shutdown to stop the queue processor and join it."""
        # Tell the process to finish.
        self._queue.put(None)
        if self._app_process_task:
            await self._app_process_task
            self._app_process_task = None

    async def _app_process_queue(self):
        """Consume queue messages and forward them to the app."""
        loop = asyncio.get_running_loop()

        running = True
        while running:
            message = await loop.run_in_executor(None, self._queue.get)

            if message and self._app:
                await self._app.handle_message(message)

            running = bool(message)

        if self._app and self._app.is_running:
            self._app.exit()


class TailRunner(PipelineRunner):
    """Pipeline runner that launches Tail dashboard in a separate process."""

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        force_gc: bool = False,
        **kwargs,
    ):
        """Initialize the runner.

        Args:
            name: Optional runner name.
            force_gc: Whether to force garbage collection between iterations.
            **kwargs: Additional keyword args passed to ``PipelineRunner``.
        """
        super().__init__(name=name, handle_sigint=False, handle_sigterm=False, force_gc=force_gc)
        self._queue = Queue()
        self._app = None

    async def run(self, task: PipelineTask):
        """Run the pipeline and Tail dashboard concurrently.

        Sends an initial ``tail-ready`` message, attaches an observer that
        forwards RTVI messages to the Tail dashboard, and waits until either the
        app or pipeline completes, then performs a graceful shutdown.

        Args:
            task: The pipeline task to execute.
        """
        # Inform the client about our setup.
        await self._app_send_rtvi_message(RTVITailReadyMessage())

        # Remove default logger before adding the observer.
        logger.remove()

        # Add the observer. This will send RTVI messages (including system logs).
        task.add_observer(TailRunnerObserver(self._queue))

        app_task = asyncio.create_task(asyncio.to_thread(self._app_thread))
        pipeline_task = asyncio.create_task(super().run(task))
        _, pending = await asyncio.wait(
            [app_task, pipeline_task], return_when=asyncio.FIRST_COMPLETED
        )

        # It doesn't matter if we try to cancel a task that is already finished,
        # so we just always do it.
        await task.cancel()

        # Always let the user finish the app.
        if app_task in pending:
            await self._app_send_rtvi_message(RTVITailPipelineFinishedMessage())
            await app_task

        # Re-add default logger.
        logger.add(sys.stderr)

    def _app_thread(self):
        """Launch the app."""
        app = TailAppProcess(self._queue)
        app.run()

    async def _app_send_rtvi_message(self, message: BaseModel):
        """Send an RTVI message to the app.

        Args:
            message: RTVI message to send.
        """
        self._queue.put(message.model_dump(exclude_none=True))
