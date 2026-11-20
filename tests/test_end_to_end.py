"""Run a real pipeline with Tail attached and read what it sends.

These tests run on every Pipecat 1.x release. Pipecat 1.0 to 1.2 run
pipelines with ``PipelineTask`` and ``PipelineRunner``; 1.3 and later with
``PipelineWorker`` and ``WorkerRunner``, where ``TailWorker`` also reports
workers and the bus.
"""

import asyncio
import contextlib
import dataclasses
import json
import math
import socket
import struct

import pytest
import websockets
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    ErrorFrame,
    InputAudioRawFrame,
    LLMContextFrame,
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
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.llm_service import LLMService

from pipecat_tail import compat
from pipecat_tail.observer import TailObserver, audio_level
from pipecat_tail.sink import CallbackSink
from pipecat_tail.state import SessionState

if compat.HAS_WORKERS:
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.workers.runner import WorkerRunner
else:
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask

needs_workers = pytest.mark.skipif(not compat.HAS_WORKERS, reason="needs Pipecat 1.3+")


class FakeLLM(LLMService):
    """Stands in for an LLM: Tail reports the contexts that reach one."""

    async def process_frame(self, frame, direction):
        await FrameProcessor.process_frame(self, frame, direction)
        await self.push_frame(frame, direction)


def speech(seconds: float = 0.02, amplitude: float = 0.3) -> bytes:
    rate = 16000
    samples = [
        int(32767 * amplitude * math.sin(2 * math.pi * 220 * i / rate))
        for i in range(int(rate * seconds))
    ]
    return struct.pack(f"<{len(samples)}h", *samples)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def tts_text(text: str) -> TTSTextFrame:
    names = {f.name for f in dataclasses.fields(TTSTextFrame)}
    if "text_type" in names:
        return TTSTextFrame(text, text_type="sentence")
    return TTSTextFrame(text, aggregated_by="sentence")


def frames(processor_name: str):
    context = LLMContext(
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hello there"},
        ]
    )
    return [
        VADUserStartedSpeakingFrame(start_secs=0.2),
        UserStartedSpeakingFrame(),
        InputAudioRawFrame(audio=speech(), sample_rate=16000, num_channels=1),
        TranscriptionFrame("hello there", "user", ""),
        VADUserStoppedSpeakingFrame(stop_secs=0.3),
        UserStoppedSpeakingFrame(),
        LLMContextFrame(context=context),
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hi!"),
        LLMFullResponseEndFrame(),
        TTSStartedFrame(),
        tts_text("Hi!"),
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


async def run_pipeline(name: str, observer_factory, *, tail=None):
    """Run ``frames()`` through a one-processor pipeline on this Pipecat.

    ``tail(worker)`` makes a worker to run beside the pipeline's, such as a
    ``TailWorker`` reporting on it.
    """
    processor = FakeLLM(name="proc")
    params = PipelineParams(enable_metrics=True, enable_usage_metrics=True)
    if compat.HAS_WORKERS:
        worker = PipelineWorker(
            Pipeline([processor]), name=name, params=params, idle_timeout_secs=None
        )
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker, *([tail(worker)] if tail else []))
    else:
        worker = PipelineTask(Pipeline([processor]), params=params, idle_timeout_secs=None)
        runner = PipelineRunner(handle_sigint=False)
    observer = observer_factory(worker)
    if observer is not None:
        worker.add_observer(observer)

    run_task = asyncio.create_task(runner.run(worker) if not compat.HAS_WORKERS else runner.run())
    try:
        await asyncio.sleep(0.8)
        await worker.queue_frames(frames("proc"))
        await asyncio.wait_for(run_task, 15.0)
    finally:
        if not run_task.done():
            await runner.cancel()
            await asyncio.wait_for(run_task, 5.0)
    return worker, runner


def assert_conversation(types: list[str]):
    if compat.HAS_USER_INPUT:
        assert "user-input" in types
        assert "llm-raw-text" in types
        # The deprecated duplicates are not forwarded.
        assert "user-transcription" not in types
        assert "bot-llm-text" not in types
    else:
        assert "user-transcription" in types
        assert "bot-llm-text" in types
    assert "bot-transcription" not in types
    assert "user-started-speaking" in types
    assert "bot-started-speaking" in types


def assert_metrics(messages: list[dict]):
    types = [m["type"] for m in messages]
    if compat.ServiceMetricsObserver is not None:
        usage = next(m for m in messages if m["type"] == "tail-service-usage")
        assert usage["data"]["total_tokens"] == 12
        latency = next(m for m in messages if m["type"] == "tail-service-latency")
        assert latency["data"]["kind"] == "ttfb" and latency["data"]["seconds"] == 0.42
        assert "metrics" not in types
    else:
        metrics = [m for m in messages if m["type"] == "metrics"]
        assert any("ttfb" in m["data"] for m in metrics)
        assert any("tokens" in m["data"] for m in metrics)


@pytest.mark.asyncio
async def test_observer_with_callback_sink():
    logger.remove()
    received: list[dict] = []

    async def on_message(message):
        received.append(message)

    await run_pipeline(
        "bot-1",
        lambda worker: TailObserver(
            sink=CallbackSink(on_message), worker=worker, flow_interval_secs=0.0
        ),
    )

    types = [m["type"] for m in received]
    assert_conversation(types)
    assert_metrics(received)
    assert "tail-pipeline-started" in types
    assert "tail-turn" in types
    assert "tail-flow" in types
    assert types[-1] == "tail-pipeline-finished"

    # Each error is reported once, however many processors it crosses.
    errors = [m for m in received if m["type"] == "tail-error"]
    assert len(errors) == 1
    assert errors[0]["data"]["message"] == "boom"

    started = next(m for m in received if m["type"] == "tail-pipeline-started")
    assert "proc" in started["data"]["processors"]
    assert "conversation" in started["data"]["capabilities"]

    context = next(m for m in received if m["type"] == "tail-llm-context")
    assert context["data"]["messages"] == 2
    assert context["data"]["roles"] == {"system": 1, "user": 1}
    assert context["data"]["start"] == 0
    assert [i["text"] for i in context["data"]["items"]] == [
        "You are a helpful assistant.",
        "hello there",
    ]

    # Tail measures audio levels itself, and sends silence once.
    levels = [m["data"]["value"] for m in received if m["type"] == "user-audio-level"]
    assert levels and 0.3 < levels[0] < 1.0

    flow = [m for m in received if m["type"] == "tail-flow"]
    assert any("proc" in m["data"]["processors"] for m in flow)

    # RTVI messages carry the time the observer saw them.
    assert all(isinstance(m.get("timestamp"), float) for m in received if m["label"] == "rtvi-ai")

    # The VAD's own waits place speech and silence where they happened.
    vad = [m for m in received if m["type"] == "tail-vad"]
    assert [m["data"]["speaking"] for m in vad] == [True, False]
    assert [m["data"]["secs"] for m in vad] == [0.2, 0.3]
    assert all(abs(m["timestamp"] - m["data"]["secs"] - m["data"]["at"]) < 1e-6 for m in vad)


@pytest.mark.asyncio
async def test_observer_without_worker_discovers_processors():
    logger.remove()
    received: list[dict] = []

    async def on_message(message):
        received.append(message)

    worker, _ = await run_pipeline("bot-2", lambda _: TailObserver(sink=CallbackSink(on_message)))

    types = [m["type"] for m in received]
    assert "tail-pipeline-started" in types
    assert "tail-pipeline-finished" in types
    started = next(m for m in received if m["type"] == "tail-pipeline-started")
    assert "proc" in started["data"]["processors"]
    # The observer finds its worker as the pipeline starts, so everything
    # about the pipeline carries the worker's name, as it does when given one.
    name = "bot-2" if compat.HAS_WORKERS else worker.name
    assert started["worker"] == name
    first = types.index("tail-pipeline-started")
    assert all(m.get("worker") == name for m in received[first:] if m["type"] != "tail-log")


@pytest.mark.skipif(
    not hasattr(BaseObserver, "on_processor_setup"), reason="setup hook from Pipecat 1.8"
)
@pytest.mark.asyncio
async def test_observer_finds_its_worker_as_the_pipeline_is_set_up():
    # A client can connect while the pipeline is set up, before the StartFrame
    # goes out, and its timing report must carry the worker's name.
    from pipecat.observers.base_observer import ProcessorSetUp

    async def ignore(_message):
        pass

    worker = PipelineWorker(Pipeline([FakeLLM(name="proc")]), name="bot-5")
    observer = TailObserver(sink=CallbackSink(ignore))
    pipeline = worker._pipeline
    # A pipeline's own source isn't the worker's.
    inner = Pipeline([FakeLLM(name="inner")])
    await observer.on_processor_setup(ProcessorSetUp(inner._source, 0, 0))
    assert observer.worker_name is None
    await observer.on_processor_setup(ProcessorSetUp(pipeline._source, 0, 0))
    assert observer.worker_name == "bot-5"


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
async def test_standalone_observer_serves_a_websocket():
    logger.remove()
    port = free_port()
    reader: asyncio.Task | None = None

    def factory(worker):
        return TailObserver(host="localhost", port=port, worker=worker)

    async def start_reader():
        nonlocal reader
        await asyncio.sleep(0.4)
        reader = asyncio.create_task(
            collect(f"ws://localhost:{port}", until="tail-pipeline-finished")
        )

    starter = asyncio.create_task(start_reader())
    await run_pipeline("bot-ws", factory)
    await starter
    assert reader is not None
    messages = await reader
    types = [m["type"] for m in messages]
    assert types[0] == "tail-ready"
    assert "capabilities" in messages[0]["data"]
    # On its own the observer can't report the runner, and says so.
    assert messages[0]["data"]["standalone"] is True
    assert_conversation(types)
    assert types[-1] == "tail-pipeline-finished"


@needs_workers
@pytest.mark.asyncio
async def test_worker_runner_with_a_tail_worker_serving_the_app():
    from pipecat_tail.worker import TailWorker

    logger.remove()
    port = free_port()
    tails: list = []
    reader: asyncio.Task | None = None

    def make_tail(worker):
        tails.append(TailWorker(worker, server=True, host="localhost", port=port))
        return tails[0]

    async def start_reader():
        nonlocal reader
        await asyncio.sleep(0.4)
        url = f"ws://localhost:{port}"
        reader = asyncio.create_task(collect(url, until="tail-pipeline-finished"))

    starter = asyncio.create_task(start_reader())
    worker, runner = await run_pipeline("bot-1", lambda _: None, tail=make_tail)
    tail = tails[0]
    await starter
    assert reader is not None
    messages = await reader

    types = [m["type"] for m in messages]
    assert types[0] == "tail-ready"
    if hasattr(tail, "worker_runner"):
        assert messages[0]["data"]["runner"] == runner.name
    assert "workers" in messages[0]["data"]["capabilities"]
    assert "standalone" not in messages[0]["data"]
    assert_conversation(types)
    assert_metrics(messages)
    assert "tail-pipeline-started" in types
    assert "tail-startup" in types
    assert "tail-error" in types
    assert "tail-turn" in types
    assert "tail-log" in types
    # The runner's bus reaches the app on every Pipecat with workers.
    assert "tail-bus" in types
    if hasattr(tail, "worker_runner"):
        # Pipecat 1.8+ broadcasts the registry and worker lifecycle on the bus.
        assert "tail-workers" in types or "tail-worker-ready" in types
    assert types[-1] == "tail-pipeline-finished"

    # Everything from the pipeline is tagged with the worker name.
    for message in messages:
        if message["type"] in ("user-input", "user-transcription", "tail-error", "tail-turn"):
            assert message["worker"] == "bot-1"


class FakeAppProcess:
    """Stands in for the app process: reads the queue in ``join()``.

    Args:
        queue: The app's queue.
        received: Collects the messages read.
        until: ``"pipeline"`` quits once ``pipelines`` pipelines have
            finished, as a user would; ``"started"`` quits as soon as a
            pipeline starts, mid-call; ``"quit"`` waits until the runner
            closes the app.
        pipelines: How many pipelines finish before a ``"pipeline"`` app quits.
    """

    def __init__(self, queue, received, until, pipelines: int = 1):
        self._queue = queue
        self._received = received
        self._until = until
        self._pipelines = pipelines
        self.markers: list = []
        self.terminated = False

    def join(self):
        while True:
            message = self._queue.get()
            if isinstance(message, dict):
                self._received.append(message)
                if self._until == "started" and message["type"] == "tail-pipeline-started":
                    return
                if self._until == "pipeline" and message["type"] == "tail-pipeline-finished":
                    self._pipelines -= 1
                    if not self._pipelines:
                        return
                continue
            self.markers.append(message)
            return

    def terminate(self):
        self.terminated = True


def fresh_dashboard(monkeypatch, start_app):
    """A dashboard of its own for a test, whose app is ``start_app(queue)``."""
    from pipecat_tail import dashboard

    monkeypatch.setattr(dashboard, "DASHBOARD", dashboard._Dashboard())
    monkeypatch.setattr(dashboard._Dashboard, "start_app", lambda self, queue: start_app(queue))


@needs_workers
@pytest.mark.asyncio
async def test_tail_worker_feeds_the_app_here(monkeypatch):
    """TailWorker attaches observers and feeds the app process through a queue.

    The Textual app needs a terminal, so the app process is replaced by a
    reader that drains the queue until the pipeline is over.
    """
    from pipecat.pipeline.worker import PipelineWorker

    from pipecat_tail.worker import TailWorker

    logger.remove()
    lines: list[str] = []
    sink = logger.add(lambda line: lines.append(str(line)), level="INFO")
    received: list[dict] = []
    fresh_dashboard(monkeypatch, lambda queue: FakeAppProcess(queue, received, "pipeline"))

    processor = FakeLLM(name="proc")
    worker = PipelineWorker(
        Pipeline([processor]),
        name="bot-3",
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=None,
    )
    runner = WorkerRunner(handle_sigint=False)
    tail = TailWorker(worker)
    await runner.add_workers(worker, tail)
    run_task = asyncio.create_task(runner.run())
    try:
        await asyncio.sleep(0.8)
        await worker.queue_frames(frames("proc"))
        await asyncio.wait_for(run_task, 15.0)
    finally:
        if not run_task.done():
            await runner.cancel()
            await asyncio.wait_for(run_task, 5.0)
    # The bot's own log handlers are left as they were.
    logger.info("after the run")
    logger.remove(sink)
    logger.add(lambda _: None)
    assert any("after the run" in line for line in lines)

    types = [m["type"] for m in received]
    # No client connects to a queue: the server says it is ready as it starts,
    # or the app would show the bot as disconnected and its state pills off.
    ready = next(m for m in received if m["type"] == "tail-ready")
    if hasattr(tail, "worker_runner"):
        # Workers know their runner from Pipecat 1.8.
        assert ready["data"]["runner"] == runner.name
    state = SessionState()
    for message in received:
        state.apply(message)
    assert state.status == "connected"
    if hasattr(runner, "get_worker"):
        assert "tail-workers" in types or "tail-worker-ready" in types
    assert "tail-pipeline-finished" in types
    conversation = "user-input" if compat.HAS_USER_INPUT else "user-transcription"
    assert conversation in types
    assert all(m["worker"] == "bot-3" for m in received if m["type"] == conversation)


@needs_workers
@pytest.mark.asyncio
async def test_tail_worker_closes_the_app_when_the_runner_is_cancelled(monkeypatch):
    """Cancelling run() ends the pipeline and closes the app.

    The development runner cancels the bot this way when it shuts down; the
    pipeline and the app must not be left waiting on each other.
    """
    from pipecat.pipeline.worker import PipelineWorker

    from pipecat_tail.dashboard import QUIT
    from pipecat_tail.worker import TailWorker

    logger.remove()
    apps: list[FakeAppProcess] = []

    def start_app(queue):
        apps.append(FakeAppProcess(queue, [], "quit"))
        return apps[-1]

    fresh_dashboard(monkeypatch, start_app)

    worker = PipelineWorker(Pipeline([FakeLLM(name="proc")]), name="bot-4", idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker, TailWorker(worker))
    run_task = asyncio.create_task(runner.run())
    await asyncio.sleep(0.8)
    run_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(run_task, 10.0)
    logger.add(lambda _: None)

    assert worker.has_finished()
    assert apps[0].markers == [QUIT]
    assert not apps[0].terminated


@needs_workers
@pytest.mark.asyncio
async def test_closing_the_app_here_stops_the_bot(monkeypatch):
    """q in the app ends the call: the bot stops and the runner ends."""
    from pipecat.pipeline.worker import PipelineWorker

    from pipecat_tail.worker import TailWorker

    logger.remove()
    fresh_dashboard(monkeypatch, lambda queue: FakeAppProcess(queue, [], "started"))
    worker = PipelineWorker(Pipeline([FakeLLM(name="proc")]), name="bot-8", idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker, TailWorker(worker))
    await asyncio.wait_for(runner.run(), 10.0)
    logger.add(lambda _: None)
    assert worker.has_finished()


@needs_workers
@pytest.mark.asyncio
async def test_tail_workers_in_one_process_share_one_app(monkeypatch):
    """A development runner starts the bot, and its runner, for every call.

    The calls report to the app already open, instead of starting another
    on the same terminal.
    """
    from pipecat.pipeline.worker import PipelineWorker

    from pipecat_tail.worker import TailWorker

    logger.remove()
    apps: list[FakeAppProcess] = []
    received: list[dict] = []

    def start_app(queue):
        apps.append(FakeAppProcess(queue, received, "pipeline", pipelines=2))
        return apps[-1]

    fresh_dashboard(monkeypatch, start_app)

    async def call(name: str) -> asyncio.Task:
        worker = PipelineWorker(Pipeline([FakeLLM(name="proc")]), name=name, idle_timeout_secs=None)
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker, TailWorker(worker))
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.8)
        await worker.queue_frames(frames("proc"))
        return task

    first = await call("call-1")
    for _ in range(100):
        if any(m["type"] == "tail-pipeline-finished" for m in received):
            break
        await asyncio.sleep(0.05)
    # The first call is over, and its dashboard stays open for the next one.
    assert not first.done()
    second = await call("call-2")
    await asyncio.wait_for(asyncio.gather(first, second), 15.0)
    logger.add(lambda _: None)

    assert len(apps) == 1
    started = [m["worker"] for m in received if m["type"] == "tail-pipeline-started"]
    assert started == ["call-1", "call-2"]


def test_audio_level_follows_loudness():
    assert audio_level(b"") == 0.0
    assert audio_level(b"\x00\x00" * 320) == 0.0
    loud = audio_level(speech(amplitude=0.5))
    quiet = audio_level(speech(amplitude=0.02))
    assert 0.0 < quiet < loud <= 1.0


async def test_observer_in_observers_keeps_the_bots_own_rtvi():
    # A pipeline that finds an RTVIObserver among its observers skips its own
    # RTVI, leaving the bot's client without it. Tail's observer is not one.
    observer = TailObserver(sink=CallbackSink(lambda m: asyncio.sleep(0)))
    if compat.HAS_WORKERS:
        worker = PipelineWorker(Pipeline([FakeLLM()]), observers=[observer])
    else:
        worker = PipelineTask(Pipeline([FakeLLM()]), observers=[observer])
    assert getattr(worker, "_rtvi", None) is not None


@needs_workers
@pytest.mark.asyncio
async def test_a_server_sends_each_log_line_once():
    # Logs belong to the process: with several observers reporting through
    # one server, each line still reaches the app once.
    from collections import Counter

    from pipecat_tail.worker import TailWorker

    logger.remove()
    received: list[dict] = []

    async def on_message(message):
        received.append(message)

    def make_tail(worker):
        tail = TailWorker(worker, sink=CallbackSink(on_message))
        worker.add_observer(tail.create_observer(worker))
        return tail

    await run_pipeline("bot-7", lambda _: None, tail=make_tail)
    logs = [m for m in received if m["type"] == "tail-log"]
    assert logs
    lines = Counter((m["data"]["time"], m["data"]["message"]) for m in logs)
    assert max(lines.values()) == 1
    assert all("worker" not in m for m in logs)
