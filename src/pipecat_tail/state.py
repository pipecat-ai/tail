#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""In-memory model of a Tail session.

``SessionState.apply()`` folds one wire message into the model and reports
which areas changed so the app can refresh only the widgets that show them.
The model has no Textual dependency, so it can be tested and reused on its
own.

Per-worker data (conversation, latency, metrics, speech, startup) lives in a
``WorkerSession``; runner-wide data (workers, jobs, bus traffic, logs, errors)
lives on ``SessionState`` itself.
"""

import datetime
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Iterable, Optional

MAX_TURNS = 300
MAX_LOGS = 5000
MAX_BUS = 2000
MAX_SPEECH_EVENTS = 2000
MAX_METRIC_POINTS = 500
MAX_LEVELS = 10

Area = str
CONVERSATION: Area = "conversation"
LATENCY: Area = "latency"
WORKERS: Area = "workers"
METRICS: Area = "metrics"
LOGS: Area = "logs"
STRIP: Area = "strip"
ERRORS: Area = "errors"
BUS: Area = "bus"
JOBS: Area = "jobs"
STARTUP: Area = "startup"
STATUS: Area = "status"


@dataclass
class FunctionCall:
    """One tool call the LLM asked for."""

    tool_call_id: str
    function_name: str
    kind: str = "function_call_started"
    arguments: Any = None
    result: Any = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    in_progress_at: Optional[float] = None
    settled_at: Optional[float] = None

    @property
    def settled(self) -> bool:
        """Whether the call has completed, failed, timed out or been cancelled."""
        return self.kind not in ("function_call_started", "function_call_in_progress")

    @property
    def duration_secs(self) -> Optional[float]:
        """Wall time from the call starting to it settling, if both are known."""
        start = self.in_progress_at or self.started_at
        if start is None or self.settled_at is None:
            return None
        return max(self.settled_at - start, 0.0)


@dataclass
class Segment:
    """A piece of bot output the TTS will speak, with its spoken progress."""

    segment_id: Any
    text: str
    will_be_spoken: bool = True
    spoken_status: Optional[str] = None
    accumulated: str = ""
    remaining: str = ""

    @property
    def spoken_text(self) -> str:
        """The part of the segment that has been spoken."""
        if self.spoken_status == "completed":
            return self.text
        if self.spoken_status == "in-progress":
            return self.accumulated
        return ""

    @property
    def unspoken_text(self) -> str:
        """The part of the segment that has not been spoken yet."""
        if self.spoken_status == "completed":
            return ""
        if self.spoken_status == "in-progress":
            return self.remaining
        return self.text


@dataclass
class Turn:
    """A conversation turn: what the user said and what the bot answered."""

    number: Optional[int]
    started_at: float
    user_text: str = ""
    user_interim: str = ""
    user_llm_text: Optional[str] = None
    llm_text: str = ""
    tts_text: str = ""
    segments: list[Segment] = field(default_factory=list)
    consumed: int = 0
    function_calls: list[FunctionCall] = field(default_factory=list)
    interrupted: bool = False
    interrupted_at: Optional[float] = None
    latency_secs: Optional[float] = None
    ended: bool = False
    duration_secs: Optional[float] = None
    provisional: bool = True

    @property
    def has_bot_content(self) -> bool:
        """Whether the bot has produced anything in this turn."""
        return bool(self.llm_text or self.tts_text or self.segments or self.function_calls)

    @property
    def has_content(self) -> bool:
        """Whether anything at all happened in this turn."""
        return bool(self.user_text or self.user_interim or self.has_bot_content)

    @property
    def pending_text(self) -> str:
        """LLM text that no spoken segment has claimed yet."""
        return self.llm_text[self.consumed :]

    def spoken_word_counts(self) -> tuple[int, int]:
        """Return ``(spoken, total)`` word counts of the bot's output."""
        spoken = sum(len(s.spoken_text.split()) for s in self.segments)
        total = sum(len(s.text.split()) for s in self.segments) + len(self.pending_text.split())
        return spoken, total

    def function_call(self, tool_call_id: str) -> Optional[FunctionCall]:
        """Find a function call by id."""
        for call in self.function_calls:
            if call.tool_call_id == tool_call_id:
                return call
        return None


@dataclass
class LatencyRecord:
    """User-to-bot latency of one turn with its breakdown."""

    turn: Optional[int]
    latency_secs: float
    total_secs: float
    contributions: list[dict[str, Any]]
    first_bot_speech: bool
    timestamp: float
    ttfb: list[dict[str, Any]] = field(default_factory=list)
    function_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SpeechEvent:
    """A raw VAD, turn ruling, bot speech or interruption event."""

    kind: str
    timestamp: float
    started_at: Optional[float] = None


@dataclass
class MetricPoint:
    """One latency measurement of a service."""

    timestamp: float
    seconds: float
    model: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class UsageTotals:
    """Accumulated usage of one service."""

    kind: str
    processor: str
    model: Optional[str] = None
    count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    reasoning_tokens: int = 0
    input_audio_tokens: int = 0
    output_audio_tokens: int = 0
    characters: int = 0
    audio_seconds: float = 0.0

    def add(self, record: dict[str, Any]) -> None:
        """Fold a usage record into the totals."""
        self.count += 1
        self.model = record.get("model") or self.model
        for name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "reasoning_tokens",
            "input_audio_tokens",
            "output_audio_tokens",
            "characters",
        ):
            value = record.get(name)
            if isinstance(value, (int, float)):
                setattr(self, name, getattr(self, name) + int(value))
        audio = record.get("audio_seconds")
        if isinstance(audio, (int, float)):
            self.audio_seconds += float(audio)


@dataclass
class LogRecord:
    """A structured log line."""

    time: str
    level: str
    name: str
    function: str
    line: Any
    message: str
    worker: Optional[str] = None
    exception: Optional[str] = None

    @property
    def clock(self) -> str:
        """The wall-clock part of the timestamp, with milliseconds."""
        try:
            when = datetime.datetime.fromisoformat(self.time)
            return when.strftime("%H:%M:%S.%f")[:-3]
        except ValueError:
            return self.time[11:23] if len(self.time) >= 23 else self.time


@dataclass
class ErrorRecord:
    """An error reported by a processor."""

    message: str
    category: str
    processor: str
    processor_usable: bool
    timestamp: float
    worker: Optional[str] = None
    exception_type: Optional[str] = None
    dismissed: bool = False


@dataclass
class BusRecord:
    """A message seen on the worker bus."""

    timestamp: float
    name: str
    message_type: str
    category: str
    source: Optional[str]
    target: Optional[str]
    payload: dict[str, Any]


@dataclass
class Job:
    """A job requested over the bus."""

    job_id: str
    source: Optional[str]
    target: Optional[str]
    job_name: Optional[str] = None
    status: str = "running"
    requested_at: float = 0.0
    finished_at: Optional[float] = None
    updates: int = 0
    last: Optional[str] = None

    @property
    def elapsed_secs(self) -> float:
        """Seconds the job has run, or ran."""
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(end - self.requested_at, 0.0)


@dataclass
class WorkerInfo:
    """A worker known to the runner."""

    name: str
    parent: Optional[str] = None
    active: bool = False
    bridged: bool = False
    started_at: Optional[float] = None
    status: str = "known"
    error: Optional[str] = None
    processors: list[str] = field(default_factory=list)


class WorkerSession:
    """Everything Tail knows about one pipeline worker."""

    def __init__(self, name: Optional[str]):
        """Create an empty session for a worker.

        Args:
            name: Worker name, or ``None`` for untagged messages.
        """
        self.name = name
        self.turns: Deque[Turn] = deque(maxlen=MAX_TURNS)
        self.latencies: list[LatencyRecord] = []
        self.speech: Deque[SpeechEvent] = deque(maxlen=MAX_SPEECH_EVENTS)
        self.metrics: dict[tuple[str, str], Deque[MetricPoint]] = {}
        self.usage: dict[tuple[str, str], UsageTotals] = {}
        self.startup: Optional[dict[str, Any]] = None
        self.transport_timing: Optional[dict[str, Any]] = None
        self.user_speaking = False
        self.bot_speaking = False
        self.user_level = 0.0
        self.bot_level = 0.0
        self.user_levels: Deque[float] = deque([0.0] * MAX_LEVELS, maxlen=MAX_LEVELS)
        self.bot_levels: Deque[float] = deque([0.0] * MAX_LEVELS, maxlen=MAX_LEVELS)
        self.finished = False
        self.next_turn_number = 1

    # Turns

    @property
    def current_turn(self) -> Optional[Turn]:
        """The most recent turn, if any."""
        return self.turns[-1] if self.turns else None

    def turn_for_input(self) -> Turn:
        """Return the turn user input belongs to, starting a new one if needed."""
        turn = self.current_turn
        if turn is None or turn.ended or turn.has_bot_content:
            turn = self._new_turn(None)
        return turn

    def turn_for_output(self) -> Turn:
        """Return the turn bot output belongs to, starting a new one if needed."""
        turn = self.current_turn
        if turn is None or turn.ended:
            turn = self._new_turn(None)
        return turn

    def _new_turn(self, number: Optional[int]) -> Turn:
        if number is None:
            number = self.next_turn_number
        self.next_turn_number = max(self.next_turn_number, number + 1)
        turn = Turn(number=number, started_at=time.time(), provisional=True)
        self.turns.append(turn)
        return turn

    def start_turn(self, number: int) -> Turn:
        """Handle a turn-started event from the turn tracking observer."""
        turn = self.current_turn
        if turn is not None and turn.provisional and not turn.ended and not turn.has_bot_content:
            turn.number = number
            turn.provisional = False
            self.next_turn_number = max(self.next_turn_number, number + 1)
            return turn
        turn = self._new_turn(number)
        turn.provisional = False
        return turn

    def end_turn(self, number: int, duration: Optional[float], was_interrupted: bool) -> None:
        """Handle a turn-ended event from the turn tracking observer."""
        for turn in reversed(self.turns):
            if turn.number == number:
                turn.ended = True
                turn.duration_secs = duration
                if was_interrupted and turn.has_bot_content:
                    turn.interrupted = True
                return

    # Metrics

    def add_metric(self, processor: str, kind: str, point: MetricPoint) -> None:
        """Record a latency measurement."""
        series = self.metrics.setdefault((processor, kind), deque(maxlen=MAX_METRIC_POINTS))
        series.append(point)

    def add_usage(self, record: dict[str, Any]) -> None:
        """Record a usage report."""
        processor = str(record.get("processor", "?"))
        kind = str(record.get("kind", "?"))
        totals = self.usage.setdefault(
            (processor, kind), UsageTotals(kind=kind, processor=processor)
        )
        totals.add(record)

    # Totals for the strip

    @property
    def prompt_tokens(self) -> int:
        """Prompt tokens used by every LLM service."""
        return sum(u.prompt_tokens for u in self.usage.values() if u.kind == "llm")

    @property
    def completion_tokens(self) -> int:
        """Completion tokens used by every LLM service."""
        return sum(u.completion_tokens for u in self.usage.values() if u.kind == "llm")

    @property
    def tts_characters(self) -> int:
        """Characters synthesized by every TTS service."""
        return sum(u.characters for u in self.usage.values() if u.kind == "tts")

    @property
    def last_latency(self) -> Optional[LatencyRecord]:
        """The most recent latency record."""
        return self.latencies[-1] if self.latencies else None


class SessionState:
    """The whole model the Tail app renders."""

    def __init__(self):
        """Create an empty state."""
        self.info: dict[str, Any] = {}
        self.runner: Optional[str] = None
        self.status: str = "disconnected"
        self.status_detail: Optional[str] = None
        self.workers: dict[str, WorkerInfo] = {}
        self.sessions: dict[Optional[str], WorkerSession] = {}
        self.selected_worker: Optional[str] = None
        self.logs: Deque[LogRecord] = deque(maxlen=MAX_LOGS)
        self.errors: list[ErrorRecord] = []
        self.bus: Deque[BusRecord] = deque(maxlen=MAX_BUS)
        self.jobs: dict[str, Job] = {}
        self.messages_seen = 0

    # Sessions and selection

    def session(self, worker: Optional[str]) -> WorkerSession:
        """Return the session for a worker, creating it if needed."""
        session = self.sessions.get(worker)
        if session is None:
            session = WorkerSession(worker)
            self.sessions[worker] = session
            if self.selected_worker is None and worker is not None:
                self.selected_worker = worker
        return session

    @property
    def selected(self) -> WorkerSession:
        """The session the views are scoped to."""
        if self.selected_worker in self.sessions:
            return self.sessions[self.selected_worker]
        if None in self.sessions:
            return self.sessions[None]
        if self.sessions:
            return next(iter(self.sessions.values()))
        return self.session(None)

    @property
    def pipeline_workers(self) -> list[str]:
        """Names of workers that have a session, in first-seen order."""
        return [name for name in self.sessions if name is not None]

    def select_next_worker(self) -> Optional[str]:
        """Select the next worker that has a session and return its name."""
        names = self.pipeline_workers
        if not names:
            return None
        if self.selected_worker not in names:
            self.selected_worker = names[0]
        else:
            index = names.index(self.selected_worker)
            self.selected_worker = names[(index + 1) % len(names)]
        return self.selected_worker

    def select_worker(self, name: Optional[str]) -> None:
        """Select a worker by name."""
        if name is None or name in self.sessions:
            self.selected_worker = name

    def set_status(self, status: str, detail: Optional[str] = None) -> set[Area]:
        """Update the connection status."""
        self.status = status
        self.status_detail = detail
        if status != "connected":
            for session in self.sessions.values():
                session.user_speaking = False
                session.bot_speaking = False
                session.user_level = 0.0
                session.bot_level = 0.0
        return {STATUS, STRIP}

    @property
    def active_errors(self) -> list[ErrorRecord]:
        """Errors that have not been dismissed."""
        return [e for e in self.errors if not e.dismissed]

    def dismiss_errors(self) -> None:
        """Dismiss every error shown in the banner."""
        for error in self.errors:
            error.dismissed = True

    # Message handling

    def apply(self, message: dict[str, Any]) -> set[Area]:
        """Fold one wire message into the model.

        Args:
            message: A decoded wire message.

        Returns:
            The areas that changed.
        """
        self.messages_seen += 1
        msg_type = message.get("type")
        if not isinstance(msg_type, str):
            return set()
        worker = message.get("worker")
        worker = worker if isinstance(worker, str) else None
        data = message.get("data")
        data = data if isinstance(data, dict) else {}
        label = message.get("label")
        if label == "tail":
            return self._apply_tail(msg_type, worker, data, message)
        if label == "rtvi-ai":
            return self._apply_rtvi(msg_type, worker, data)
        return set()

    def _apply_tail(
        self, msg_type: str, worker: Optional[str], data: dict[str, Any], message: dict[str, Any]
    ) -> set[Area]:
        timestamp = message.get("timestamp")
        timestamp = float(timestamp) if isinstance(timestamp, (int, float)) else time.time()

        match msg_type:
            case "tail-ready":
                self.info = dict(data)
                runner = data.get("runner")
                self.runner = runner if isinstance(runner, str) else self.runner
                self.status = "connected"
                self.status_detail = None
                return {STATUS, STRIP, WORKERS}
            case "tail-pipeline-started":
                session = self.session(worker)
                session.finished = False
                processors = data.get("processors")
                if worker is not None:
                    info = self.workers.setdefault(worker, WorkerInfo(name=worker))
                    info.status = "running"
                    if isinstance(processors, list):
                        info.processors = [str(p) for p in processors]
                return {WORKERS, STRIP}
            case "tail-pipeline-finished":
                session = self.session(worker)
                session.finished = True
                session.user_speaking = False
                session.bot_speaking = False
                session.user_level = 0.0
                session.bot_level = 0.0
                if worker is not None and worker in self.workers:
                    self.workers[worker].status = "finished"
                return {WORKERS, STRIP}
            case "tail-turn":
                session = self.session(worker)
                number = data.get("turn")
                if not isinstance(number, int):
                    return set()
                if data.get("kind") == "started":
                    session.start_turn(number)
                else:
                    duration = data.get("duration_secs")
                    session.end_turn(
                        number,
                        float(duration) if isinstance(duration, (int, float)) else None,
                        bool(data.get("was_interrupted")),
                    )
                return {CONVERSATION, STRIP}
            case "tail-latency":
                session = self.session(worker)
                latency = data.get("latency_secs", data.get("total_secs", 0.0))
                record = LatencyRecord(
                    turn=session.current_turn.number if session.current_turn else None,
                    latency_secs=float(latency) if isinstance(latency, (int, float)) else 0.0,
                    total_secs=float(data.get("total_secs", 0.0) or 0.0),
                    contributions=[c for c in data.get("contributions", []) if isinstance(c, dict)],
                    first_bot_speech=bool(data.get("first_bot_speech")),
                    timestamp=timestamp,
                    ttfb=[t for t in data.get("ttfb", []) if isinstance(t, dict)],
                    function_calls=[
                        f for f in data.get("function_calls", []) if isinstance(f, dict)
                    ],
                )
                session.latencies.append(record)
                if session.current_turn is not None:
                    session.current_turn.latency_secs = record.latency_secs
                return {LATENCY, CONVERSATION, STRIP}
            case "tail-startup":
                self.session(worker).startup = dict(data)
                return {STARTUP}
            case "tail-transport-timing":
                self.session(worker).transport_timing = dict(data)
                return {STARTUP}
            case "tail-error":
                self.errors.append(
                    ErrorRecord(
                        message=str(data.get("message", "")),
                        category=str(data.get("category", "unknown")),
                        processor=str(data.get("processor", "?")),
                        processor_usable=bool(data.get("processor_usable", True)),
                        timestamp=float(data.get("timestamp", timestamp) or timestamp),
                        worker=worker,
                        exception_type=data.get("exception_type"),
                    )
                )
                return {ERRORS, LOGS}
            case "tail-speech":
                session = self.session(worker)
                kind = str(data.get("kind", ""))
                started_at = data.get("started_at")
                session.speech.append(
                    SpeechEvent(
                        kind=kind,
                        timestamp=float(data.get("timestamp", timestamp) or timestamp),
                        started_at=float(started_at)
                        if isinstance(started_at, (int, float))
                        else None,
                    )
                )
                if kind == "interruption":
                    turn = session.current_turn
                    if turn is not None and turn.has_bot_content and not turn.interrupted:
                        turn.interrupted = True
                        turn.interrupted_at = timestamp
                        return {LATENCY, CONVERSATION}
                return {LATENCY}
            case "tail-function-call":
                return self._apply_function_call(worker, data)
            case "tail-service-latency":
                session = self.session(worker)
                processor = str(data.get("processor", "?"))
                kind = str(data.get("kind", "?"))
                seconds = data.get("seconds")
                if not isinstance(seconds, (int, float)):
                    return set()
                extra = {
                    k: v
                    for k, v in data.items()
                    if k in ("ttfb_secs", "leading_silence_secs", "thinking_time_secs")
                }
                session.add_metric(
                    processor,
                    kind,
                    MetricPoint(
                        timestamp=float(data.get("timestamp", timestamp) or timestamp),
                        seconds=float(seconds),
                        model=data.get("model"),
                        extra=extra,
                    ),
                )
                return {METRICS}
            case "tail-service-usage":
                self.session(worker).add_usage(data)
                return {METRICS, STRIP}
            case "tail-log":
                self.logs.append(
                    LogRecord(
                        time=str(data.get("time", "")),
                        level=str(data.get("level", "INFO")),
                        name=str(data.get("name", "")),
                        function=str(data.get("function", "")),
                        line=data.get("line", ""),
                        message=str(data.get("message", "")),
                        worker=worker,
                        exception=data.get("exception"),
                    )
                )
                return {LOGS}
            case "tail-workers":
                runner = data.get("runner")
                if isinstance(runner, str):
                    self.runner = runner
                for entry in data.get("workers", []):
                    if not isinstance(entry, dict) or "name" not in entry:
                        continue
                    name = str(entry["name"])
                    info = self.workers.setdefault(name, WorkerInfo(name=name))
                    info.parent = entry.get("parent")
                    info.active = bool(entry.get("active", info.active))
                    info.bridged = bool(entry.get("bridged", info.bridged))
                    started = entry.get("started_at")
                    info.started_at = float(started) if isinstance(started, (int, float)) else None
                    if info.status == "known":
                        info.status = "ready"
                return {WORKERS, STRIP}
            case "tail-worker-ready":
                if worker is None:
                    return set()
                info = self.workers.setdefault(worker, WorkerInfo(name=worker))
                info.parent = data.get("parent", info.parent)
                info.active = bool(data.get("active", info.active))
                info.bridged = bool(data.get("bridged", info.bridged))
                started = data.get("started_at")
                if isinstance(started, (int, float)):
                    info.started_at = float(started)
                if info.status in ("known", "ready"):
                    info.status = "ready"
                return {WORKERS, STRIP}
            case "tail-worker-error":
                if worker is None:
                    return set()
                info = self.workers.setdefault(worker, WorkerInfo(name=worker))
                info.status = "error"
                info.error = str(data.get("error", ""))
                return {WORKERS, STRIP}
            case "tail-job":
                return self._apply_job(data, timestamp)
            case "tail-bus":
                self.bus.append(
                    BusRecord(
                        timestamp=timestamp,
                        name=str(data.get("name", "")),
                        message_type=str(data.get("message_type", "")),
                        category=str(data.get("category", "other")),
                        source=data.get("source"),
                        target=data.get("target"),
                        payload=data.get("payload")
                        if isinstance(data.get("payload"), dict)
                        else {},
                    )
                )
                return {BUS}
        return set()

    def _apply_function_call(self, worker: Optional[str], data: dict[str, Any]) -> set[Area]:
        session = self.session(worker)
        tool_call_id = str(data.get("tool_call_id", ""))
        kind = str(data.get("kind", "function_call_started"))
        turn = session.turn_for_output()
        call = turn.function_call(tool_call_id)
        if call is None:
            # A call settling in a later turn than it started still belongs
            # to the turn it started in.
            for previous in reversed(session.turns):
                call = previous.function_call(tool_call_id)
                if call is not None:
                    break
        if call is None:
            call = FunctionCall(
                tool_call_id=tool_call_id,
                function_name=str(data.get("function_name", "?")),
            )
            turn.function_calls.append(call)
        call.kind = kind
        if data.get("function_name"):
            call.function_name = str(data["function_name"])
        if "arguments" in data:
            call.arguments = data["arguments"]
        if "result" in data:
            call.result = data["result"]
        if data.get("error"):
            call.error = str(data["error"])
        for name in ("started_at", "in_progress_at"):
            value = data.get(name)
            if isinstance(value, (int, float)):
                setattr(call, name, float(value))
        if call.settled:
            stamp = data.get("timestamp")
            call.settled_at = float(stamp) if isinstance(stamp, (int, float)) else time.time()
        return {CONVERSATION}

    def _apply_job(self, data: dict[str, Any], timestamp: float) -> set[Area]:
        job_id = data.get("job_id")
        if not isinstance(job_id, str):
            return set()
        kind = str(data.get("kind", ""))
        job = self.jobs.get(job_id)
        if job is None:
            job = Job(
                job_id=job_id,
                source=data.get("source"),
                target=data.get("target"),
                requested_at=timestamp,
            )
            self.jobs[job_id] = job
        match kind:
            case "requested":
                job.job_name = data.get("job_name")
                job.source = data.get("source", job.source)
                job.target = data.get("target", job.target)
                job.requested_at = timestamp
                job.status = "running"
            case "responded":
                job.status = str(data.get("status", "completed"))
                job.finished_at = timestamp
                job.last = _summarize(data.get("response"))
            case "updated":
                job.updates += 1
                job.last = _summarize(data.get("update"))
            case "cancelled":
                job.status = "cancelled"
                job.finished_at = timestamp
                job.last = data.get("reason")
            case "stream_start" | "stream_data" | "stream_end":
                job.updates += 1
                job.last = _summarize(data.get("data"))
        return {JOBS}

    def _apply_rtvi(self, msg_type: str, worker: Optional[str], data: dict[str, Any]) -> set[Area]:
        session = self.session(worker)
        match msg_type:
            case "user-started-speaking":
                session.user_speaking = True
                session.turn_for_input()
                return {STRIP, CONVERSATION}
            case "user-stopped-speaking":
                session.user_speaking = False
                return {STRIP}
            case "bot-started-speaking":
                session.bot_speaking = True
                return {STRIP}
            case "bot-stopped-speaking":
                session.bot_speaking = False
                session.bot_level = 0.0
                session.bot_levels.append(0.0)
                return {STRIP}
            case "user-audio-level":
                level = data.get("value", 0.0)
                session.user_level = float(level) if isinstance(level, (int, float)) else 0.0
                session.user_levels.append(session.user_level)
                return {STRIP}
            case "bot-audio-level":
                level = data.get("value", 0.0)
                session.bot_level = float(level) if isinstance(level, (int, float)) else 0.0
                session.bot_levels.append(session.bot_level)
                return {STRIP}
            case "user-transcription":
                text = str(data.get("text", ""))
                turn = session.turn_for_input()
                if data.get("final", True):
                    turn.user_text = (turn.user_text + " " + text).strip()
                    turn.user_interim = ""
                else:
                    turn.user_interim = text
                return {CONVERSATION}
            case "user-llm-text":
                turn = session.current_turn or session.turn_for_input()
                turn.user_llm_text = str(data.get("text", ""))
                return {CONVERSATION}
            case "bot-llm-text":
                turn = session.turn_for_output()
                turn.llm_text += str(data.get("text", ""))
                return {CONVERSATION}
            case "bot-tts-text":
                turn = session.turn_for_output()
                turn.tts_text = (turn.tts_text + " " + str(data.get("text", ""))).strip()
                return {CONVERSATION}
            case "bot-output":
                return self._apply_bot_output(session, data)
            case "bot-interrupted":
                turn = session.current_turn
                if turn is not None and turn.has_bot_content and not turn.interrupted:
                    turn.interrupted = True
                    turn.interrupted_at = time.time()
                    return {CONVERSATION, LATENCY}
                return set()
            case "system-log":
                text = str(data.get("text", "")).rstrip()
                self.logs.append(_parse_loguru_line(text, worker))
                return {LOGS}
            case "error":
                self.errors.append(
                    ErrorRecord(
                        message=str(data.get("error", "")),
                        category="unknown",
                        processor="?",
                        processor_usable=not bool(data.get("fatal")),
                        timestamp=time.time(),
                        worker=worker,
                    )
                )
                return {ERRORS}
        return set()

    def _apply_bot_output(self, session: WorkerSession, data: dict[str, Any]) -> set[Area]:
        turn = session.turn_for_output()
        text = str(data.get("text", ""))
        segment_id = data.get("segment_id")
        will_be_spoken = data.get("will_be_spoken")
        if will_be_spoken is None:
            will_be_spoken = data.get("spoken", True)
        status = data.get("spoken_status")
        progress = data.get("spoken_progress")
        segment = None
        if segment_id is not None:
            for candidate in turn.segments:
                if candidate.segment_id == segment_id:
                    segment = candidate
                    break
        if segment is None:
            segment = Segment(segment_id=segment_id, text=text, will_be_spoken=bool(will_be_spoken))
            turn.segments.append(segment)
            self._consume(turn, text)
        elif text and not segment.text:
            segment.text = text
        if status is not None:
            segment.spoken_status = status
        if isinstance(progress, dict):
            segment.accumulated = str(progress.get("accumulated_text", segment.accumulated))
            segment.remaining = str(progress.get("remaining_text", segment.remaining))
        return {CONVERSATION}

    @staticmethod
    def _consume(turn: Turn, text: str) -> None:
        # Advance the pointer into the raw LLM text past this segment so the
        # text still to be aggregated renders once, after the segments.
        needle = text.strip()
        if not needle:
            return
        index = turn.llm_text.find(needle, turn.consumed)
        if index >= 0:
            turn.consumed = index + len(needle)
        else:
            turn.consumed = min(len(turn.llm_text), turn.consumed + len(needle))


def _summarize(value: Any, limit: int = 80) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_loguru_line(text: str, worker: Optional[str]) -> LogRecord:
    # "2026-09-16 09:41:03.112 | INFO     | module:function:12 - message"
    parts = text.split(" | ", 2)
    if len(parts) == 3:
        when, level, rest = parts
        location, _, message = rest.partition(" - ")
        name, _, tail = location.partition(":")
        function, _, line = tail.partition(":")
        return LogRecord(
            time=when.strip().replace(" ", "T", 1),
            level=level.strip(),
            name=name.strip(),
            function=function.strip(),
            line=line.strip(),
            message=message,
            worker=worker,
        )
    return LogRecord(
        time=datetime.datetime.now().isoformat(),
        level="INFO",
        name="",
        function="",
        line="",
        message=text,
        worker=worker,
    )


def percentile(values: Iterable[float], fraction: float) -> Optional[float]:
    """Return the nearest-rank percentile of a set of values.

    Args:
        values: The measurements.
        fraction: The percentile as a fraction, for example ``0.95``.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, round(fraction * (len(ordered) - 1))))
    return ordered[index]
