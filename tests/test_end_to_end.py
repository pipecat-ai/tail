"""Run a real pipeline on a WorkerRunner with TailServer and read the wire."""

import asyncio
import json
import socket

import pytest
import websockets
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    ErrorFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData, TTFBMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from pipecat_tail.observer import TailObserver
from pipecat_tail.server import TailServer
from pipecat_tail.sink import CallbackSink


class Passthrough(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def frames(processor_name: str):
    return [
        UserStartedSpeakingFrame(),
        TranscriptionFrame("hello there", "user", ""),
        UserStoppedSpeakingFrame(),
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hi!"),
        LLMFullResponseEndFrame(),
        TTSStartedFrame(),
        TTSTextFrame("Hi!", aggregated_by="sentence"),
        BotStartedSpeakingFrame(),
        BotStoppedSpeakingFrame(),
        TTSStoppedFrame(),
        MetricsFrame(
            data=[
                TTFBMetricsData(processor=processor_name, value=0.42),
                LLMUsageMetricsData(
                    processor=processor_name,
                    value=LLMTokenUsage(prompt_tokens=10, completion_tokens=2, total_tokens=12),
                ),
            ]
        ),
        ErrorFrame(error="boom"),
        EndFrame(),
    ]


async def collect(url: str, until: str, timeout: float = 15.0) -> list[dict]:
    messages: list[dict] = []

    async def reader():
        async with websockets.connect(url) as ws:
            async for raw in ws:
                message = json.loads(raw)
                messages.append(message)
                if message.get("type") == until:
                    return

    await asyncio.wait_for(reader(), timeout)
    return messages


@pytest.mark.asyncio
async def test_worker_runner_with_tail_server():
    logger.remove()
    port = free_port()
    processor = Passthrough(name="proc")
    worker = PipelineWorker(
        Pipeline([processor]),
        name="bot-1",
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=None,
    )
    tail = TailServer(host="localhost", port=port)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker, tail)
    worker.add_observer(tail.create_observer(worker))

    run_task = asyncio.create_task(runner.run())
    try:
        # Wait for the server to come up.
        for _ in range(50):
            if worker.started_at:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)

        reader = asyncio.create_task(collect(tail.url, until="tail-pipeline-finished"))
        await asyncio.sleep(0.3)
        await worker.queue_frames(frames("proc"))

        messages = await reader
        await asyncio.wait_for(run_task, 15.0)
    finally:
        if not run_task.done():
            await runner.cancel()
            await asyncio.wait_for(run_task, 5.0)

    types = [m["type"] for m in messages]
    assert types[0] == "tail-ready"
    assert messages[0]["data"]["runner"] == runner.name
    assert "tail-pipeline-started" in types
    assert "tail-startup" in types
    assert "user-transcription" in types
    assert "bot-llm-text" in types
    assert "bot-started-speaking" in types
    assert "tail-service-latency" in types
    assert "tail-service-usage" in types
    assert "tail-error" in types
    assert "tail-turn" in types
    assert "tail-speech" in types
    assert "tail-log" in types
    assert "tail-workers" in types
    assert "tail-bus" in types
    assert types[-1] == "tail-pipeline-finished"

    # Everything from the pipeline is tagged with the worker name.
    for message in messages:
        if message["type"] in ("user-transcription", "tail-error", "tail-turn"):
            assert message["worker"] == "bot-1"

    started = next(m for m in messages if m["type"] == "tail-pipeline-started")
    assert "proc" in started["data"]["processors"]
    error = next(m for m in messages if m["type"] == "tail-error")
    assert error["data"]["message"] == "boom"
    usage = next(m for m in messages if m["type"] == "tail-service-usage")
    assert usage["data"]["total_tokens"] == 12
    latency = next(m for m in messages if m["type"] == "tail-service-latency")
    assert latency["data"]["kind"] == "ttfb" and latency["data"]["seconds"] == 0.42


@pytest.mark.asyncio
async def test_standalone_observer_with_callback_sink():
    logger.remove()
    received: list[dict] = []

    async def on_message(message):
        received.append(message)

    processor = Passthrough(name="proc")
    worker = PipelineWorker(
        Pipeline([processor]),
        name="bot-2",
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=None,
        observers=[TailObserver(sink=CallbackSink(on_message))],
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    try:
        for _ in range(50):
            if worker.started_at:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
        await worker.queue_frames(frames("proc"))
        await asyncio.wait_for(run_task, 15.0)
    finally:
        if not run_task.done():
            await runner.cancel()
            await asyncio.wait_for(run_task, 5.0)

    types = [m["type"] for m in received]
    assert "tail-pipeline-started" in types
    assert "user-transcription" in types
    assert "tail-pipeline-finished" in types
    # Without a worker reference the processors are discovered from the StartFrame.
    started = next(m for m in received if m["type"] == "tail-pipeline-started")
    assert "proc" in started["data"]["processors"]
    # Messages are untagged when the observer does not know its worker.
    assert all("worker" not in m for m in received)


@pytest.mark.asyncio
async def test_tail_runner_feeds_the_app_queue(monkeypatch):
    """TailRunner attaches observers and feeds the app process through a queue.

    The Textual app needs a terminal, so the app thread is replaced by a
    reader that drains the queue until the runner sends the end marker.
    """
    from pipecat_tail.runner import TailRunner

    logger.remove()
    received: list[dict] = []

    def fake_app_thread(self):
        # Stand in for the user: read until the pipeline is over, then quit.
        while True:
            message = self._queue.get()
            if message is None:
                return
            received.append(message)
            if message.get("type") == "tail-pipeline-finished":
                return

    monkeypatch.setattr(TailRunner, "_app_thread", fake_app_thread)

    processor = Passthrough(name="proc")
    worker = PipelineWorker(
        Pipeline([processor]),
        name="bot-3",
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=None,
    )
    runner = TailRunner()
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    try:
        for _ in range(50):
            if worker.started_at:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
        await worker.queue_frames(frames("proc"))
        await asyncio.wait_for(run_task, 15.0)
    finally:
        if not run_task.done():
            await runner.cancel()
            await asyncio.wait_for(run_task, 5.0)
    logger.add(lambda _: None)

    types = [m["type"] for m in received]
    assert "tail-workers" in types
    assert "user-transcription" in types
    assert "tail-pipeline-finished" in types
    assert all(m["worker"] == "bot-3" for m in received if m["type"] == "user-transcription")
