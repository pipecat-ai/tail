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

Per-worker data (conversation, latency, metrics, speech, flow, startup) lives
in a ``WorkerSession``; runner-wide data (workers, jobs, bus traffic, logs,
errors) lives on ``SessionState`` itself.

The model is built from RTVI first and from Tail's own messages on top, so it
works with whatever Pipecat release the bot runs:

- What the user said comes from ``user-input`` (Pipecat 1.13+) or
  ``user-transcription`` before that. Whichever arrives first for a worker
  wins, so a bot that sends both is not counted twice.
- The LLM's raw text comes from ``llm-raw-text`` or ``bot-llm-text``, the
  spoken text from ``bot-output``, with ``tts-raw-text`` or ``bot-tts-text``
  as the fallback.
- Speech lanes come from the RTVI speaking messages; raw VAD from
  ``vad-user-*`` (1.8+).
- Metrics and function calls come from Tail's observers when the bot's
  Pipecat has them (1.9+), and from RTVI ``metrics`` and
  ``llm-function-call-*`` otherwise.
- Latency breakdowns without ``contributions`` (before 1.9) are turned into
  contributions from their TTFB, aggregation and function call timings.
"""

import datetime
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Iterable, NamedTuple, Optional

MAX_TURNS = 300
MAX_LOGS = 5000
MAX_BUS = 2000
MAX_SPEECH_EVENTS = 2000
MAX_METRIC_POINTS = 500
MAX_WAVE = 1500
MAX_FLOW_HISTORY = 40
MAX_EVENTS = 6000
MAX_ERRORS = 500
MAX_JOBS = 200
BARGE_IN_GRACE_SECS = 0.5
"""An interruption this soon after the bot stopped speaking still cut it off."""
EARLY_END_SECS = 1.5
"""A user who goes on this soon after their turn was ended, before the bot said
anything, had not finished: the turn ended early."""

# Messages too frequent or too large to keep in the per-session event log.
UNLOGGED_EVENTS = frozenset(
    {"user-audio-level", "bot-audio-level", "tail-flow", "tail-log", "tail-bus", "tail-workers"}
)
WAITING_GIVE_UP_SECS = 30.0
UNHEARD_ANSWER_SECS = 2.0
"""How soon after a user turn ended with nothing heard the bot has to take it
up for Tail to count the wait for it: Pipecat answers such a turn right away
when it does (or a speech-to-speech service a moment later). The LLM starting
later is the bot speaking on its own, such as after the user went idle."""

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
VOICE: Area = "voice"
FLOW: Area = "flow"

LISTENING = "listening"
THINKING = "thinking"
SPEAKING = "speaking"
IDLE = "idle"


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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
    def failed(self) -> bool:
        """Whether the call failed, timed out or was cancelled."""
        return self.kind in (
            "function_call_failed",
            "function_call_timed_out",
            "function_call_cancelled",
        ) or bool(self.error)

    @property
    def duration_secs(self) -> Optional[float]:
        """Wall time from the call starting to it settling, if both are known."""
        start = self.in_progress_at or self.started_at
        if start is None or self.settled_at is None:
            return None
        return max(self.settled_at - start, 0.0)


@dataclass
class Segment:
    """A piece of bot output, with its spoken progress.

    ``deliveries`` records where the delivery changes, as ``(offset, tags)``
    pairs: from ``offset`` characters into the text, the words are spoken
    with ``tags`` (for example ``("warm",)``), until the next pair.
    """

    segment_id: Any
    text: str
    will_be_spoken: bool = True
    spoken_status: Optional[str] = None
    accumulated: str = ""
    remaining: str = ""
    output_type: Optional[str] = None
    deliveries: list[tuple[int, tuple[str, ...]]] = field(default_factory=list)

    @property
    def spoken_text(self) -> str:
        """The part of the segment that has been spoken."""
        if not self.will_be_spoken:
            return self.text
        if self.spoken_status == "completed":
            return self.text
        if self.spoken_status == "in-progress":
            return self.accumulated
        return ""

    @property
    def unspoken_text(self) -> str:
        """The part of the segment that has not been spoken yet."""
        if not self.will_be_spoken or self.spoken_status == "completed":
            return ""
        if self.spoken_status == "in-progress":
            return self.remaining
        return self.text

    def set_delivery(self, offset: int, tags: tuple[str, ...]) -> None:
        """Record the delivery of the words from ``offset`` on."""
        current = self.deliveries[-1][1] if self.deliveries else ()
        if tags == current:
            return
        if self.deliveries and self.deliveries[-1][0] >= offset:
            self.deliveries[-1] = (self.deliveries[-1][0], tags)
            if len(self.deliveries) > 1 and self.deliveries[-2][1] == tags:
                self.deliveries.pop()
            return
        self.deliveries.append((offset, tags))


@dataclass
class EarlyEnd:
    """Pipecat ended the user's turn, and the user went on before the bot spoke.

    The LLM usually starts on what it heard, gets cancelled when the user
    goes on, and then gets what came next as another message.
    """

    ruled_at: float
    """When Pipecat ended the turn."""
    resumed_at: float
    """When the user started speaking again, as near as Tail can tell."""
    heard: str
    """What Pipecat had of the turn when it ended it."""
    llm_started: bool
    """Whether the LLM had started on it."""

    @property
    def gap_secs(self) -> float:
        """How long after the turn ended the user went on."""
        return max(0.0, self.resumed_at - self.ruled_at)


@dataclass
class Backchannel:
    """A short acknowledgment said while the other side talks, such as "mhm"."""

    who: str
    text: str
    timestamp: float


@dataclass
class Turn:
    """A conversation turn: what the user said and what the bot answered."""

    number: Optional[int]
    started_at: float
    user_text: str = ""
    user_interim: str = ""
    user_llm_texts: list[str] = field(default_factory=list)
    """Each user message the LLM got during the turn, in order."""
    input_type: str = "transcription"
    perception: list[dict[str, Any]] = field(default_factory=list)
    speaker: Optional[str] = None
    llm_text: str = ""
    tts_text: str = ""
    segments: list[Segment] = field(default_factory=list)
    consumed: int = 0
    function_calls: list[FunctionCall] = field(default_factory=list)
    backchannels: list[Backchannel] = field(default_factory=list)
    early_ends: list[EarlyEnd] = field(default_factory=list)
    marker: Optional[dict[str, Any]] = None
    context: Optional[dict[str, Any]] = None
    interrupted: bool = False
    latency_secs: Optional[float] = None
    latency: Optional["LatencyRecord"] = None
    bot_started_at: Optional[float] = None
    ended: bool = False
    duration_secs: Optional[float] = None
    provisional: bool = True
    opened_at: Optional[float] = None
    context_items: Optional[list[dict[str, Any]]] = None
    context_system: Optional[str] = None
    context_tools: Optional[list[dict[str, Any]]] = None
    context_new_from: Optional[int] = None
    restarted_at: Optional[float] = None
    """When the user started the turn over, on the bot's clock: its first part
    ended with nothing heard, and the bot didn't take it up."""

    def restart(self, timestamp: float) -> None:
        """Start the turn over from ``timestamp``: nothing before it counts."""
        self.started_at = time.time()
        self.restarted_at = timestamp

    @property
    def has_bot_content(self) -> bool:
        """Whether the bot has produced anything in this turn."""
        return bool(self.llm_text or self.tts_text or self.segments or self.function_calls)

    @property
    def has_user_content(self) -> bool:
        """Whether any words from the user reached Tail in this turn."""
        return bool(self.user_text or self.user_interim or self.user_llm_texts)

    @property
    def has_content(self) -> bool:
        """Whether anything at all happened in this turn."""
        return bool(self.user_text or self.user_interim or self.has_bot_content)

    @property
    def user_llm_parts(self) -> list[str]:
        """What the LLM got from the user, when it isn't simply what was heard.

        Empty when the LLM got the transcription as it was. More than one
        part means Pipecat ended the user's turn at a pause, the LLM started
        on what had been said so far, and the user went on: the rest reached
        it as another message.
        """
        parts = self.user_llm_texts
        if len(parts) == 1 and parts[0].strip() == self.user_text.strip():
            return []
        return parts

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

    @property
    def tone(self) -> Optional[tuple[str, float]]:
        """The likeliest tone the user spoke in, with its confidence, if read."""
        for signal in self.perception:
            labels = signal.get("labels") or []
            if labels and isinstance(labels[0], dict):
                label = labels[0].get("label")
                confidence = _number(labels[0].get("confidence")) or 0.0
                if isinstance(label, str):
                    return label, confidence
        return None


class Stage(NamedTuple):
    """A stage of an answer that took time, from Pipecat's breakdown."""

    key: str
    secs: float
    owner: str
    start: Optional[float]


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

    @property
    def stages(self) -> list[Stage]:
        """The stages that took time, in order."""
        stages = []
        for c in self.contributions:
            secs = c.get("duration_secs")
            if isinstance(secs, (int, float)) and secs > 0:
                start = c.get("start_time")
                stages.append(
                    Stage(
                        key=str(c.get("key", "service")),
                        secs=float(secs),
                        owner=str(c.get("owner") or ""),
                        start=start if isinstance(start, (int, float)) else None,
                    )
                )
        return stages


@dataclass
class SpeechEvent:
    """A raw VAD, turn ruling, bot speech or interruption event."""

    kind: str
    timestamp: float
    started_at: Optional[float] = None
    """When it really happened, if earlier: for raw VAD, when the speech began
    or ended; for a user turn end, when the user went silent."""
    turn: Optional["Turn"] = None
    """The turn going on when it happened."""
    heard: bool = True
    """For a user turn end: whether any of the user's words had been heard."""
    answered: bool = False
    """For a user turn end with nothing heard: whether the bot took it up anyway."""


@dataclass
class Wait:
    """A wait for the bot, from the user going silent to the bot's first words."""

    started_at: float
    """When the user went silent, on the bot's clock."""
    ruled_at: float
    """When Pipecat ended the user's turn."""
    ended_at: Optional[float] = None
    """When the bot started speaking: the wait was answered."""
    marks: list[tuple[float, str]] = field(default_factory=list)
    """Where the bot got to while the user waited, as ``(time, phase)``."""


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
class FlowStats:
    """Frames a processor pushed and received, as rates per second."""

    out_rates: dict[str, float] = field(default_factory=dict)
    in_rates: dict[str, float] = field(default_factory=dict)
    history: Deque[float] = field(default_factory=lambda: deque(maxlen=MAX_FLOW_HISTORY))

    @property
    def out_total(self) -> float:
        """Frames per second pushed, of every kind."""
        return sum(self.out_rates.values())

    @property
    def in_total(self) -> float:
        """Frames per second received, of every kind."""
        return sum(self.in_rates.values())


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

    def as_log(self) -> "LogRecord":
        """The error as a line of the logs."""
        when = datetime.datetime.fromtimestamp(self.timestamp).astimezone()
        return LogRecord(
            time=when.isoformat(),
            level="ERROR",
            name=self.processor,
            function=self.category,
            line="",
            message=f"{self.category}: {self.message}",
            worker=self.worker,
            exception=self.exception_type,
        )


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
        # One a turn, kept as long as the turns are.
        self.latencies: Deque[LatencyRecord] = deque(maxlen=MAX_TURNS)
        self.speech: Deque[SpeechEvent] = deque(maxlen=MAX_SPEECH_EVENTS)
        self.metrics: dict[tuple[str, str], Deque[MetricPoint]] = {}
        self.usage: dict[tuple[str, str], UsageTotals] = {}
        self.startup: Optional[dict[str, Any]] = None
        self.transport_timing: Optional[dict[str, Any]] = None
        self.capabilities: set[str] = set()
        self.processors: list[str] = []
        self.flow: dict[str, FlowStats] = {}
        self.context: Optional[dict[str, Any]] = None
        self.context_items: list[dict[str, Any]] = []
        self.system_instruction: Optional[str] = None
        self.tool_schemas: list[dict[str, Any]] = []
        self.events: Deque[tuple[float, str, dict[str, Any]]] = deque(maxlen=MAX_EVENTS)
        self.user_speaking = False
        self.bot_speaking = False
        self.llm_active = False
        self.tts_active = False
        # The latest wait for the bot, kept once answered until the next one.
        self.wait: Optional[Wait] = None
        # How long the VAD waits in silence before saying the user stopped
        # (its stop_secs), learned from Pipecat's measured breakdowns.
        self.endpointing_secs: Optional[float] = None
        self.user_level = 0.0
        self.bot_level = 0.0
        self.user_wave: Deque[tuple[float, float]] = deque(maxlen=MAX_WAVE)
        self.bot_wave: Deque[tuple[float, float]] = deque(maxlen=MAX_WAVE)
        self.started_at: Optional[float] = None
        self.finished = False
        self.next_turn_number = 1
        self.interruptions = 0
        # Which message families this worker sends, so that a bot sending both
        # an old and a new message for the same thing is counted once.
        self.has_user_input = False
        self.has_llm_raw = False
        self.has_tail_vad = False
        self.has_tts_raw = False
        self.has_observer_metrics = False
        self.has_observer_function_calls = False

    # State

    def bot_state_at(self, now: float) -> str:
        """What the bot is doing at ``now``, on the bot's clock."""
        if self.finished:
            return IDLE
        if self.bot_speaking:
            return SPEAKING
        if self.llm_active or self.is_waiting(now):
            return THINKING
        return LISTENING

    @property
    def waiting_since(self) -> Optional[float]:
        """When the user went silent, while the bot hasn't answered yet."""
        wait = self.wait
        return wait.started_at if wait is not None and wait.ended_at is None else None

    def is_waiting(self, now: float) -> bool:
        """Whether the user is waiting for the bot at ``now``.

        A wait the bot never answers ends after ``WAITING_GIVE_UP_SECS``.
        """
        since = self.waiting_since
        return since is not None and now - since <= WAITING_GIVE_UP_SECS

    def is_active(self, now: float) -> bool:
        """Whether anything is happening that the screen should follow closely.

        Someone speaking or about to, the bot thinking, audio in the last
        moments or a tool call running.
        """
        if self.finished:
            return False
        if self.user_speaking or self.bot_speaking or self.llm_active or self.tts_active:
            return True
        if self.is_waiting(now):
            return True
        if self.user_level > 0.01 or self.bot_level > 0.01:
            return True
        for wave in (self.user_wave, self.bot_wave):
            for t, level in reversed(wave):
                if now - t > 2.0:
                    break
                if level > 0.01:
                    return True
        turn = self.current_turn
        return bool(turn and any(not c.settled for c in turn.function_calls))

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

    def end_turn(
        self,
        number: int,
        duration: Optional[float],
        was_interrupted: bool,
        timestamp: Optional[float] = None,
    ) -> None:
        """Handle a turn-ended event from the turn tracking observer."""
        for turn in reversed(self.turns):
            if turn.number == number:
                turn.ended = True
                if turn.restarted_at is not None and timestamp is not None:
                    # Pipecat counts from the part with nothing heard.
                    duration = max(0.0, timestamp - turn.restarted_at)
                turn.duration_secs = duration
                if was_interrupted and turn.has_bot_content:
                    turn.interrupted = True
                return

    def turn_by_number(self, number: Optional[int]) -> Optional[Turn]:
        """Find a turn by its number."""
        for turn in reversed(self.turns):
            if turn.number == number:
                return turn
        return None

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
    def cached_tokens(self) -> int:
        """Prompt tokens read from the provider's cache, by every LLM service."""
        return sum(u.cache_read_input_tokens for u in self.usage.values() if u.kind == "llm")

    @property
    def tts_characters(self) -> int:
        """Characters synthesized by every TTS service."""
        return sum(u.characters for u in self.usage.values() if u.kind == "tts")

    @property
    def stt_seconds(self) -> float:
        """Audio seconds transcribed by every STT service."""
        return sum(u.audio_seconds for u in self.usage.values() if u.kind == "stt")

    @property
    def llm_model(self) -> Optional[str]:
        """The model of the most used LLM service, if reported."""
        llms = [u for u in self.usage.values() if u.kind == "llm" and u.model]
        if llms:
            return max(llms, key=lambda u: u.count).model
        for (processor, kind), series in self.metrics.items():
            if "LLM" in processor and series and series[-1].model:
                return series[-1].model
        return None

    def speech_spans(self, start: str, stop: str, now: float) -> list[tuple[float, float]]:
        """When each ``start`` event kind and the ``stop`` after it happened.

        One still open ends at ``now``. A stop with no start before it (the
        start was dropped with the oldest events) starts where it says the
        speech really ended, if it says.
        """
        spans: list[tuple[float, float]] = []
        opened: Optional[float] = None
        for event in self.speech:
            if event.kind == start:
                opened = event.timestamp
            elif event.kind == stop:
                begin = opened if opened is not None else (event.started_at or event.timestamp)
                spans.append((begin, event.timestamp))
                opened = None
        if opened is not None:
            spans.append((opened, now))
        return spans

    @property
    def answers(self) -> list[LatencyRecord]:
        """The measured answers to the user, leaving out the greeting."""
        return [r for r in self.latencies if not r.first_bot_speech]

    @property
    def last_latency(self) -> Optional[LatencyRecord]:
        """The most recent latency record."""
        return self.latencies[-1] if self.latencies else None

    @property
    def backchannel_count(self) -> int:
        """Backchannels the user said in this session."""
        return sum(1 for turn in self.turns for b in turn.backchannels if b.who == "user")

    @property
    def early_end_count(self) -> int:
        """User turns Pipecat ended before the user had finished."""
        return sum(len(turn.early_ends) for turn in self.turns)

    def reset_activity(self) -> None:
        """Forget what is happening right now, for example after a disconnect."""
        self.user_speaking = False
        self.bot_speaking = False
        self.llm_active = False
        self.tts_active = False
        self.drop_wait()
        self.user_level = 0.0
        self.bot_level = 0.0
        # Rates are the last interval's counts, and no more counts are coming.
        for stats in self.flow.values():
            stats.out_rates = {}
            stats.in_rates = {}

    # The wait for the bot

    def start_wait(self, stopped: float, ruled: float) -> None:
        """Start waiting for the bot: the user went silent and their turn ended."""
        self.wait = Wait(started_at=stopped, ruled_at=ruled)
        if self.llm_active:
            self.wait.marks.append((ruled, "llm"))

    def drop_wait(self) -> None:
        """Forget a wait the bot hasn't answered: the user went on, or the bot was cut off."""
        if self.wait is not None and self.wait.ended_at is None:
            self.wait = None

    def take_up_unheard(self, timestamp: float) -> None:
        """Start the wait for a turn that ended with nothing heard, now the bot answers it.

        Pipecat leaves such a turn unanswered unless it cut the bot off, so
        the wait only starts once the bot shows it's answering: the LLM
        starts, words turn up late, or the bot speaks. It counts from when the
        user went silent, like any other.
        """
        ruling = self.ruling()
        if ruling is None or not self.is_unheard(ruling) or self.is_waiting(timestamp):
            return
        ruling.answered = True
        if timestamp - ruling.timestamp <= UNHEARD_ANSWER_SECS:
            self.start_wait(ruling.started_at or ruling.timestamp, ruling.timestamp)

    # What the speech events say

    def last_speech(self, kind: str) -> Optional[SpeechEvent]:
        """The latest speech event of a kind, if any."""
        return next((e for e in reversed(self.speech) if e.kind == kind), None)

    def ruling(self) -> Optional[SpeechEvent]:
        """The end of the user's latest turn, while they haven't started another."""
        for event in reversed(self.speech):
            if event.kind == "user_turn_started":
                return None
            if event.kind == "user_turn_stopped":
                return event
        return None

    @staticmethod
    def is_unheard(ruling: SpeechEvent) -> bool:
        """Whether a turn ended with nothing heard, and the bot hasn't taken it up."""
        return not ruling.heard and not ruling.answered

    def bot_spoke_after(self, event: SpeechEvent) -> bool:
        """Whether the bot started speaking after a speech event."""
        for later in reversed(self.speech):
            if later is event:
                return False
            if later.kind == "bot_speech_started":
                return True
        return False

    def mark_wait(self, timestamp: float, phase: str, *, once: bool = False) -> None:
        """Note that the bot moved to a phase while the user waits for it.

        Args:
            timestamp: When the phase started, on the bot's clock.
            phase: Where the wait got to, such as ``llm`` or ``tts``.
            once: Ignore it if the phase was already reached in this wait.
        """
        wait = self.wait
        if wait is None or not self.is_waiting(timestamp):
            return
        if once and any(name == phase for _, name in wait.marks):
            return
        wait.marks.append((timestamp, phase))

    def wait_phases(self, now: float) -> list[tuple[str, float, float]]:
        """Where the latest wait for the bot went, as ``(phase, start, end)``.

        From the user going silent until now while the bot thinks, or until
        its first words once it speaks. Empty when the user went on before
        the bot answered, or when there was no wait yet. Phases come from
        what Pipecat reports starting: the end of the turn, the LLM, tool
        calls, the first sentence and speech synthesis.
        """
        wait = self.wait
        if wait is None or (wait.ended_at is None and not self.is_waiting(now)):
            return []
        start = wait.started_at
        end = wait.ended_at if wait.ended_at is not None else max(now, start)
        marks = list(wait.marks)
        turn = self.current_turn
        for call in turn.function_calls if turn else []:
            if call.started_at is not None and call.started_at >= start:
                marks.append((call.started_at, "tool"))
                if call.settled_at is not None:
                    marks.append((call.settled_at, "llm"))
        marks.sort(key=lambda mark: mark[0])
        phases: list[tuple[str, float, float]] = []

        def add(phase: str, a: float, b: float) -> None:
            if b <= a:
                return
            if phases and phases[-1][0] == phase:
                phases[-1] = (phase, phases[-1][1], b)
            else:
                phases.append((phase, a, b))

        ruled = min(max(wait.ruled_at, start), end)
        add("end of turn", start, ruled)
        cursor, phase = ruled, "waiting"
        for t, name in marks:
            t = min(max(t, cursor), end)
            add(phase, cursor, t)
            cursor, phase = t, name
        add(phase, cursor, end)
        return phases


class SessionState:
    """The whole model the Tail app renders."""

    def __init__(self):
        """Create an empty state."""
        self.info: dict[str, Any] = {}
        self.runner: Optional[str] = None
        self.status: str = "disconnected"
        self.status_detail: Optional[str] = None
        self.connected_at: Optional[float] = None
        self.disconnected_at: Optional[float] = None
        self.workers: dict[str, WorkerInfo] = {}
        self.sessions: dict[Optional[str], WorkerSession] = {}
        self.selected_worker: Optional[str] = None
        self.logs: Deque[LogRecord] = deque(maxlen=MAX_LOGS)
        self.errors: Deque[ErrorRecord] = deque(maxlen=MAX_ERRORS)
        # Every error so far, those dropped from ``errors`` included.
        self.errors_seen = 0
        self.bus: Deque[BusRecord] = deque(maxlen=MAX_BUS)
        self.jobs: dict[str, Job] = {}
        self.last_message_at: Optional[float] = None
        self.clock_offset: Optional[float] = None

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
    def has_bot(self) -> bool:
        """Whether any pipeline has reported anything yet."""
        return any(s.started_at is not None or s.turns for s in self.sessions.values())

    @property
    def call_on(self) -> bool:
        """Whether any pipeline's call is still on."""
        return any(self.call_end(self.sessions[name]) is None for name in self.pipeline_workers)

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

    def set_status(self, status: str, detail: Optional[str] = None) -> set[Area]:
        """Update the connection status."""
        if status == "connected" and self.status != "connected":
            self.connected_at = time.time()
        elif status != "connected" and self.status == "connected":
            self.disconnected_at = time.time()
        self.status = status
        self.status_detail = detail
        if status != "connected":
            for session in self.sessions.values():
                session.reset_activity()
        return {STATUS, STRIP, VOICE, FLOW}

    def call_end(self, session: WorkerSession) -> Optional[float]:
        """When the session's call ended, on the bot's clock, or None while it's on.

        A call is over once its pipeline finished or the bot went away. It
        ended with the last thing Tail heard about it.
        """
        if not self.has_bot or (self.status == "connected" and not session.finished):
            return None
        if session.events:
            return session.events[-1][0]
        return self.last_message_at if self.last_message_at is not None else self.now()

    def now(self) -> float:
        """The current time on the bot's clock.

        Message timestamps come from the bot's machine, which may not agree
        with this one. The offset follows the smallest delay seen, which is
        the clock difference plus the shortest network trip.
        """
        return time.time() - (self.clock_offset or 0.0)

    def _track_clock(self, timestamp: float) -> None:
        delay = time.time() - timestamp
        if self.clock_offset is None or delay < self.clock_offset:
            self.clock_offset = delay
        else:
            # Let the offset drift up slowly in case the clocks moved apart.
            self.clock_offset += 0.0002

    @property
    def capabilities(self) -> set[str]:
        """What the bot's Tail observer can report, empty when unknown."""
        caps = self.info.get("capabilities")
        selected = self.selected.capabilities
        if selected:
            return selected
        return set(caps) if isinstance(caps, list) else set()

    def _add_error(self, error: ErrorRecord) -> None:
        self.errors.append(error)
        self.errors_seen += 1

    def errors_for(self, session: WorkerSession) -> list[ErrorRecord]:
        """The errors in a session's worker, and those not tied to any worker."""
        return [e for e in self.errors if e.worker in (None, session.name)]

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
        msg_type = message.get("type")
        if not isinstance(msg_type, str):
            return set()
        worker = message.get("worker")
        worker = worker if isinstance(worker, str) else None
        data = message.get("data")
        data = data if isinstance(data, dict) else {}
        timestamp = _number(message.get("timestamp"))
        if timestamp is None:
            timestamp = self.now()
        else:
            self._track_clock(timestamp)
        self.last_message_at = timestamp
        label = message.get("label")
        session = self.sessions.get(worker)
        before = session.current_turn if session is not None else None
        if label == "tail":
            areas = self._apply_tail(msg_type, worker, data, timestamp)
        elif label == "rtvi-ai":
            areas = self._apply_rtvi(msg_type, worker, data, timestamp)
        else:
            return set()
        session = self.sessions.get(worker)
        if session is not None and msg_type not in UNLOGGED_EVENTS:
            turn = session.current_turn
            if turn is not None and turn is not before and turn.opened_at is None:
                turn.opened_at = timestamp
            if msg_type == "tail-llm-context":
                # The context itself is kept on the session and the turn.
                logged = {
                    k: v for k, v in data.items() if k not in ("items", "system", "tool_schemas")
                }
                logged["new_messages"] = len(data.get("items") or [])
                session.events.append((timestamp, msg_type, logged))
            else:
                session.events.append((timestamp, msg_type, data))
        return areas

    def _apply_tail(
        self, msg_type: str, worker: Optional[str], data: dict[str, Any], timestamp: float
    ) -> set[Area]:
        match msg_type:
            case "tail-ready":
                self.info = dict(data)
                runner = data.get("runner")
                self.runner = runner if isinstance(runner, str) else self.runner
                if self.status != "connected":
                    self.connected_at = time.time()
                self.status = "connected"
                self.status_detail = None
                return {STATUS, STRIP, WORKERS}
            case "tail-pipeline-started":
                session = self.sessions.get(worker)
                if session is not None and session.started_at not in (None, timestamp):
                    # The worker started again: the bot restarted under the
                    # same names. Its last run doesn't belong to this one.
                    self.sessions[worker] = WorkerSession(worker)
                    if worker is not None:
                        self.workers.pop(worker, None)
                session = self.session(worker)
                if self.selected is not session and self.selected.finished:
                    # A new call takes over from one that is over.
                    self.selected_worker = worker
                session.finished = False
                session.started_at = timestamp
                processors = data.get("processors")
                if isinstance(processors, list):
                    session.processors = [str(p) for p in processors]
                caps = data.get("capabilities")
                if isinstance(caps, list):
                    session.capabilities = {str(c) for c in caps}
                if worker is not None:
                    info = self.workers.setdefault(worker, WorkerInfo(name=worker))
                    info.status = "running"
                    if info.started_at is None:
                        info.started_at = timestamp
                    if isinstance(processors, list):
                        info.processors = [str(p) for p in processors]
                return {WORKERS, STRIP, FLOW}
            case "tail-pipeline-finished":
                session = self.session(worker)
                session.finished = True
                session.reset_activity()
                if worker is not None and worker in self.workers:
                    self.workers[worker].status = "finished"
                return {WORKERS, STRIP, VOICE, FLOW}
            case "tail-turn":
                session = self.session(worker)
                number = data.get("turn")
                if not isinstance(number, int):
                    return set()
                if data.get("kind") == "started":
                    session.start_turn(number)
                else:
                    session.end_turn(
                        number,
                        _number(data.get("duration_secs")),
                        bool(data.get("was_interrupted")),
                        timestamp,
                    )
                return {CONVERSATION, STRIP}
            case "tail-vad":
                session = self.session(worker)
                session.has_tail_vad = True
                kind = "user_speech_started" if data.get("speaking") else "user_speech_stopped"
                at = _number(data.get("at"))
                last = session.speech[-1] if session.speech else None
                if last is not None and last.kind == kind and abs(last.timestamp - timestamp) < 0.5:
                    # RTVI's copy of the same event got here first: correct it.
                    last.started_at = at
                else:
                    self._speech(session, kind, timestamp, at)
                return {VOICE, LATENCY}
            case "tail-latency":
                return self._apply_latency(self.session(worker), data, timestamp)
            case "tail-startup":
                self.session(worker).startup = dict(data)
                return {STARTUP}
            case "tail-transport-timing":
                self.session(worker).transport_timing = dict(data)
                return {STARTUP}
            case "tail-error":
                error = ErrorRecord(
                    message=str(data.get("message", "")),
                    category=str(data.get("category", "unknown")),
                    processor=str(data.get("processor", "?")),
                    processor_usable=bool(data.get("processor_usable", True)),
                    timestamp=_number(data.get("timestamp")) or timestamp,
                    worker=worker,
                    exception_type=data.get("exception_type"),
                )
                self._add_error(error)
                # Pipecat logs a processor's error as a warning, if at all: the
                # logs show it as the error it is.
                self.logs.append(error.as_log())
                return {ERRORS, LOGS, STRIP, FLOW}
            case "tail-function-call":
                self.session(worker).has_observer_function_calls = True
                return self._apply_function_call(worker, data, timestamp)
            case "tail-service-latency":
                session = self.session(worker)
                session.has_observer_metrics = True
                seconds = _number(data.get("seconds"))
                if seconds is None:
                    return set()
                extra = {
                    k: v
                    for k, v in data.items()
                    if k in ("ttfb_secs", "leading_silence_secs", "thinking_time_secs")
                }
                session.add_metric(
                    str(data.get("processor", "?")),
                    str(data.get("kind", "?")),
                    MetricPoint(
                        timestamp=_number(data.get("timestamp")) or timestamp,
                        seconds=seconds,
                        model=data.get("model"),
                        extra=extra,
                    ),
                )
                return {METRICS, FLOW}
            case "tail-service-usage":
                session = self.session(worker)
                session.has_observer_metrics = True
                session.add_usage(data)
                return {METRICS, STRIP}
            case "tail-flow":
                return self._apply_flow(self.session(worker), data, timestamp)
            case "tail-llm-context":
                return self._apply_llm_context(self.session(worker), data)
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
                    started = _number(entry.get("started_at"))
                    info.started_at = started if started is not None else info.started_at
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
                started = _number(data.get("started_at"))
                if started is not None:
                    info.started_at = started
                runner = data.get("runner")
                if isinstance(runner, str) and not self.runner:
                    self.runner = runner
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
                payload = data.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                self.bus.append(
                    BusRecord(
                        timestamp=timestamp,
                        name=str(data.get("name", "")),
                        message_type=str(data.get("message_type", "")),
                        category=str(data.get("category", "other")),
                        source=data.get("source"),
                        target=data.get("target"),
                        payload=payload,
                    )
                )
                return {BUS}
        return set()

    # Speech

    def _speech(
        self, session: WorkerSession, kind: str, timestamp: float, started_at: Optional[float]
    ) -> None:
        turn = session.current_turn
        # The user's latest turn end, from before this event.
        ruling = session.ruling()
        event = SpeechEvent(kind=kind, timestamp=timestamp, started_at=started_at, turn=turn)
        session.speech.append(event)
        if kind == "user_turn_stopped":
            self._turn_stopped(session, event)
        elif kind == "user_turn_started":
            self._turn_started(session, ruling, timestamp)
        elif kind == "bot_speech_started":
            session.take_up_unheard(timestamp)
            if session.is_waiting(timestamp) and session.wait is not None:
                session.wait.ended_at = timestamp
            # A wait still open was given up on: an answer this late doesn't count.
            session.drop_wait()
            if turn is not None and turn.bot_started_at is None:
                turn.bot_started_at = timestamp
        elif kind == "interruption":
            session.interruptions += 1
            session.drop_wait()
            if turn is not None and turn.has_bot_content and not turn.interrupted:
                turn.interrupted = True

    def _turn_stopped(self, session: WorkerSession, event: SpeechEvent) -> None:
        """The user's turn ended: wait for the bot, if anything was heard."""
        # Latency counts from when the user actually went silent: the VAD says
        # so after its stop_secs of silence, and the turn is ruled over after
        # that. Pipecat measures from the silence too, so the live clock and
        # the measured answer agree.
        stopped = event.timestamp
        vad_stop = session.last_speech("user_speech_stopped")
        silent = (vad_stop.started_at or vad_stop.timestamp) if vad_stop else None
        if silent is not None and 0 <= event.timestamp - silent < 5:
            stopped = silent
            if not session.has_tail_vad:
                # Without the VAD's own stop_secs, use the learned one.
                stopped -= session.endpointing_secs or 0.0
        event.started_at = stopped
        turn = event.turn
        event.heard = session.llm_active or (turn is not None and turn.has_user_content)
        if event.heard:
            session.start_wait(stopped, event.timestamp)
        # Otherwise nothing was heard, such as a murmur the transcription
        # dropped: Pipecat ends the turn after its stop timeout and doesn't
        # answer it, so the bot is still listening.

    def _turn_started(
        self, session: WorkerSession, ruling: Optional[SpeechEvent], timestamp: float
    ) -> None:
        """The user started a turn: maybe the last one ended too early, or was nothing."""
        self._check_early_end(session, ruling, timestamp)
        turn = session.current_turn
        if (
            ruling is not None
            and session.is_unheard(ruling)
            and turn is not None
            and turn is ruling.turn
            and not turn.has_content
        ):
            # The turn so far was nothing the bot heard or answered: it starts
            # over with what the user says now.
            turn.restart(timestamp)
        # The user went on before the bot answered.
        session.drop_wait()

    def _check_early_end(
        self, session: WorkerSession, ruling: Optional[SpeechEvent], timestamp: float
    ) -> None:
        """Note an early end when the user goes on right after their turn ended."""
        if ruling is None or ruling.turn is None or session.bot_spoke_after(ruling):
            return
        if session.is_unheard(ruling):
            # Nothing was heard and the bot didn't take the turn up: nothing
            # reached the LLM, so nothing ended early.
            return
        ruled, turn = ruling.timestamp, ruling.turn
        if not 0 <= timestamp - ruled <= EARLY_END_SECS:
            return
        # The VAD heard the user start again a little before the turn started:
        # use when the speech really began, if the VAD said so around then.
        resumed = timestamp
        for event in reversed(session.speech):
            if event.timestamp < ruled - 0.5:
                break
            if event.kind == "user_speech_started" and event.timestamp <= timestamp:
                resumed = event.started_at or event.timestamp
                break
        heard = turn.user_llm_texts[-1] if turn.user_llm_texts else turn.user_text
        marks = session.wait.marks if session.wait is not None else []
        llm_started = session.llm_active or any(n == "llm" for _, n in marks)
        turn.early_ends.append(
            EarlyEnd(
                ruled_at=ruled, resumed_at=resumed, heard=heard.strip(), llm_started=llm_started
            )
        )

    # Latency

    def _apply_latency(
        self, session: WorkerSession, data: dict[str, Any], timestamp: float
    ) -> set[Area]:
        latency = _number(data.get("latency_secs"))
        if latency is None:
            latency = _number(data.get("total_secs")) or 0.0
        contributions = [c for c in data.get("contributions", []) if isinstance(c, dict)]
        if not contributions:
            contributions = legacy_contributions(data)
        total = _number(data.get("total_secs"))
        if total is None:
            total = sum(_number(c.get("duration_secs")) or 0.0 for c in contributions) or latency
        turn = session.current_turn
        record = LatencyRecord(
            turn=turn.number if turn else None,
            latency_secs=latency,
            total_secs=total,
            contributions=contributions,
            first_bot_speech=bool(data.get("first_bot_speech")),
            timestamp=timestamp,
            ttfb=[t for t in data.get("ttfb", []) if isinstance(t, dict)],
            function_calls=[f for f in data.get("function_calls", []) if isinstance(f, dict)],
        )
        session.latencies.append(record)
        if turn is not None:
            turn.latency_secs = record.latency_secs
            turn.latency = record
        for c in contributions:
            secs = _number(c.get("duration_secs"))
            if c.get("key") == "endpointing_wait" and secs is not None and 0 < secs < 3:
                session.endpointing_secs = secs
        return {LATENCY, CONVERSATION, STRIP}

    # Function calls

    def _apply_function_call(
        self, worker: Optional[str], data: dict[str, Any], timestamp: float
    ) -> set[Area]:
        session = self.session(worker)
        tool_call_id = str(data.get("tool_call_id", ""))
        kind = str(data.get("kind", "function_call_started"))
        # A call settling after its turn ended still belongs to the turn it
        # started in; only a new call opens a turn for the bot's output.
        call = next(
            (c for t in reversed(session.turns) if (c := t.function_call(tool_call_id))),
            None,
        )
        if call is None:
            turn = session.turn_for_output()
            call = FunctionCall(
                tool_call_id=tool_call_id,
                function_name=str(data.get("function_name") or "?"),
            )
            turn.function_calls.append(call)
        call.kind = kind
        if data.get("function_name"):
            call.function_name = str(data["function_name"])
        if "arguments" in data and data["arguments"] is not None:
            call.arguments = data["arguments"]
        if "result" in data:
            call.result = data["result"]
        if data.get("error"):
            call.error = str(data["error"])
        for name in ("started_at", "in_progress_at"):
            value = _number(data.get(name))
            if value is not None:
                setattr(call, name, value)
        if call.started_at is None and kind == "function_call_started":
            call.started_at = timestamp
        if call.in_progress_at is None and kind == "function_call_in_progress":
            call.in_progress_at = timestamp
        if call.settled:
            stamp = _number(data.get("timestamp"))
            call.settled_at = stamp if stamp is not None else timestamp
        return {CONVERSATION}

    def _apply_rtvi_function_call(
        self, session: WorkerSession, msg_type: str, data: dict[str, Any], timestamp: float
    ) -> set[Area]:
        # Pipecat before 1.9 has no FunctionCallObserver, so Tail asks RTVI
        # for full function call reports and reads them here.
        if session.has_observer_function_calls:
            return set()
        event: dict[str, Any] = {
            "tool_call_id": data.get("tool_call_id"),
            "function_name": data.get("function_name"),
        }
        if msg_type == "llm-function-call-started":
            event["kind"] = "function_call_started"
        elif msg_type == "llm-function-call-in-progress":
            event["kind"] = "function_call_in_progress"
            event["arguments"] = data.get("arguments")
        else:
            cancelled = bool(data.get("cancelled"))
            event["kind"] = "function_call_cancelled" if cancelled else "function_call_completed"
            if "result" in data:
                event["result"] = data.get("result")
        if not event["tool_call_id"]:
            return set()
        return self._apply_function_call(session.name, event, timestamp)

    # Metrics from RTVI

    def _apply_rtvi_metrics(
        self, session: WorkerSession, data: dict[str, Any], timestamp: float
    ) -> set[Area]:
        # Pipecat before 1.9 has no ServiceMetricsObserver, so Tail asks RTVI
        # for metrics and reads them here.
        if session.has_observer_metrics:
            return set()
        for kind in ("ttfb", "ttfa", "ttfat", "processing"):
            for entry in data.get(kind, []) or []:
                if not isinstance(entry, dict):
                    continue
                value = _number(entry.get("value"))
                if value is None:
                    continue
                session.add_metric(
                    str(entry.get("processor", "?")),
                    kind,
                    MetricPoint(timestamp=timestamp, seconds=value, model=entry.get("model")),
                )
        for entry in data.get("tokens", []) or []:
            if isinstance(entry, dict):
                record = dict(entry)
                record["kind"] = "llm"
                record.setdefault("processor", entry.get("processor", "?"))
                session.add_usage(record)
        for entry in data.get("characters", []) or []:
            if isinstance(entry, dict):
                value = _number(entry.get("value"))
                session.add_usage(
                    {
                        "kind": "tts",
                        "processor": entry.get("processor", "?"),
                        "model": entry.get("model"),
                        "characters": int(value or 0),
                    }
                )
        return {METRICS, STRIP, FLOW}

    # Flow

    def _apply_llm_context(self, session: WorkerSession, data: dict[str, Any]) -> set[Area]:
        summary = {k: v for k, v in data.items() if k not in ("items", "system", "tool_schemas")}
        session.context = summary
        items = data.get("items")
        if isinstance(items, list):
            session.context_items = [i for i in items if isinstance(i, dict)]
        if "system" in data:
            system = data.get("system")
            session.system_instruction = system if isinstance(system, str) and system else None
        if isinstance(data.get("tool_schemas"), list):
            session.tool_schemas = [t for t in data["tool_schemas"] if isinstance(t, dict)]
        turn = session.current_turn
        if turn is not None and not turn.ended:
            if turn.context_new_from is None:
                previous = next(
                    (
                        t
                        for t in reversed(session.turns)
                        if t is not turn and t.context_items is not None
                    ),
                    None,
                )
                turn.context_new_from = len(previous.context_items or []) if previous else 0
            turn.context = summary
            turn.context_items = list(session.context_items)
            turn.context_system = session.system_instruction
            turn.context_tools = session.tool_schemas
        return {CONVERSATION}

    def _apply_flow(
        self, session: WorkerSession, data: dict[str, Any], timestamp: float
    ) -> set[Area]:
        interval = _number(data.get("interval_secs")) or 0.25
        interval = max(interval, 0.05)
        processors = data.get("processors")
        if not isinstance(processors, dict):
            return set()
        seen = set()
        for name, sides in processors.items():
            if not isinstance(sides, dict):
                continue
            stats = session.flow.setdefault(str(name), FlowStats())
            out_counts = sides.get("out")
            out_counts = out_counts if isinstance(out_counts, dict) else {}
            in_counts = sides.get("in")
            in_counts = in_counts if isinstance(in_counts, dict) else {}
            stats.out_rates = {k: (_number(v) or 0.0) / interval for k, v in out_counts.items()}
            stats.in_rates = {k: (_number(v) or 0.0) / interval for k, v in in_counts.items()}
            stats.history.append(stats.out_total)
            seen.add(str(name))
        # Processors that moved nothing in this interval are idle.
        for name, stats in session.flow.items():
            if name not in seen:
                stats.out_rates = {}
                stats.in_rates = {}
                stats.history.append(0.0)
        return {FLOW}

    # Jobs

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
            if len(self.jobs) > MAX_JOBS:
                # The oldest job goes first.
                del self.jobs[next(iter(self.jobs))]
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

    # RTVI

    def _apply_rtvi(
        self, msg_type: str, worker: Optional[str], data: dict[str, Any], timestamp: float
    ) -> set[Area]:
        session = self.session(worker)
        match msg_type:
            case "user-started-speaking":
                session.user_speaking = True
                session.turn_for_input()
                self._speech(session, "user_turn_started", timestamp, None)
                return {STRIP, CONVERSATION, VOICE, LATENCY}
            case "user-stopped-speaking":
                session.user_speaking = False
                self._speech(session, "user_turn_stopped", timestamp, None)
                return {STRIP, VOICE, LATENCY}
            case "vad-user-started-speaking":
                if not session.has_tail_vad:
                    self._speech(session, "user_speech_started", timestamp, None)
                return {VOICE, LATENCY}
            case "vad-user-stopped-speaking":
                if not session.has_tail_vad:
                    self._speech(session, "user_speech_stopped", timestamp, None)
                return {VOICE, LATENCY}
            case "bot-started-speaking":
                session.bot_speaking = True
                self._speech(session, "bot_speech_started", timestamp, None)
                return {STRIP, VOICE, LATENCY}
            case "bot-stopped-speaking":
                session.bot_speaking = False
                self._speech(session, "bot_speech_stopped", timestamp, None)
                return {STRIP, VOICE, LATENCY}
            case "bot-interrupted":
                # Pipecat interrupts at every user turn start; it only cuts
                # the bot off when the bot is speaking (or just was).
                last_stop = session.last_speech("bot_speech_stopped")
                stopped = last_stop.timestamp if last_stop is not None else None
                turn = session.current_turn
                speaking_words = bool(
                    turn and any(seg.spoken_status == "in-progress" for seg in turn.segments)
                )
                if (
                    session.bot_speaking
                    or speaking_words
                    or (stopped is not None and 0 <= timestamp - stopped < BARGE_IN_GRACE_SECS)
                ):
                    self._speech(session, "interruption", timestamp, None)
                return {CONVERSATION, LATENCY, VOICE, STRIP}
            case "bot-llm-started":
                session.take_up_unheard(timestamp)
                session.llm_active = True
                session.mark_wait(timestamp, "llm")
                return {STRIP, CONVERSATION, FLOW}
            case "bot-llm-stopped":
                session.llm_active = False
                return {STRIP, CONVERSATION, FLOW}
            case "bot-tts-started":
                session.tts_active = True
                session.mark_wait(timestamp, "tts", once=True)
                return {STRIP, FLOW}
            case "bot-tts-stopped":
                session.tts_active = False
                return {STRIP, FLOW}
            case "user-audio-level":
                level = _number(data.get("value")) or 0.0
                session.user_level = level
                session.user_wave.append((timestamp, level))
                return {STRIP, VOICE}
            case "bot-audio-level":
                level = _number(data.get("value")) or 0.0
                session.bot_level = level
                session.bot_wave.append((timestamp, level))
                return {STRIP, VOICE}
            case "user-input":
                session.has_user_input = True
                return self._apply_user_input(session, data, timestamp)
            case "user-transcription":
                if session.has_user_input:
                    return set()
                text = str(data.get("text", ""))
                turn = session.turn_for_input()
                if data.get("final", True):
                    turn.user_text = (turn.user_text + " " + text).strip()
                    turn.user_interim = ""
                    session.take_up_unheard(timestamp)
                else:
                    turn.user_interim = text
                return {CONVERSATION}
            case "user-llm-text":
                session.take_up_unheard(timestamp)
                # Sent each time the context goes to the LLM ending in a user
                # message. A turn holds several when Pipecat ended the user's
                # turn at a pause and the user went on before the bot spoke.
                turn = session.current_turn or session.turn_for_input()
                text = str(data.get("text", ""))
                if not turn.user_llm_texts or turn.user_llm_texts[-1] != text:
                    turn.user_llm_texts.append(text)
                return {CONVERSATION}
            case "llm-raw-text":
                session.has_llm_raw = True
                session.mark_wait(timestamp, "first sentence", once=True)
                turn = session.turn_for_output()
                turn.llm_text += str(data.get("text", ""))
                return {CONVERSATION}
            case "bot-llm-text":
                session.mark_wait(timestamp, "first sentence", once=True)
                if session.has_llm_raw:
                    return set()
                turn = session.turn_for_output()
                turn.llm_text += str(data.get("text", ""))
                return {CONVERSATION}
            case "tts-raw-text":
                session.has_tts_raw = True
                turn = session.turn_for_output()
                turn.tts_text = (turn.tts_text + " " + str(data.get("text", ""))).strip()
                return {CONVERSATION}
            case "bot-tts-text":
                if session.has_tts_raw:
                    return set()
                turn = session.turn_for_output()
                turn.tts_text = (turn.tts_text + " " + str(data.get("text", ""))).strip()
                return {CONVERSATION}
            case "bot-output":
                return self._apply_bot_output(session, data, timestamp)
            case "bot-llm-marker":
                turn = session.current_turn
                if turn is not None:
                    turn.marker = {
                        "text": str(data.get("text", "")),
                        "kind": data.get("kind"),
                    }
                return {CONVERSATION}
            case (
                "llm-function-call-started"
                | "llm-function-call-in-progress"
                | ("llm-function-call-stopped")
            ):
                return self._apply_rtvi_function_call(session, msg_type, data, timestamp)
            case "metrics":
                return self._apply_rtvi_metrics(session, data, timestamp)
            case "system-log":
                text = str(data.get("text", "")).rstrip()
                self.logs.append(_parse_loguru_line(text, worker))
                return {LOGS}
            case "error":
                self._add_error(
                    ErrorRecord(
                        message=str(data.get("error", "")),
                        category="unknown",
                        processor="?",
                        processor_usable=not bool(data.get("fatal")),
                        timestamp=timestamp,
                        worker=worker,
                    )
                )
                return {ERRORS, STRIP}
        return set()

    def _apply_user_input(
        self, session: WorkerSession, data: dict[str, Any], timestamp: float
    ) -> set[Area]:
        text = str(data.get("text", ""))
        input_type = str(data.get("input_type", "transcription"))
        if input_type == "backchannel":
            # Said while the bot talks; it doesn't start a turn.
            turn = session.current_turn or session.turn_for_input()
            turn.backchannels.append(Backchannel(who="user", text=text, timestamp=timestamp))
            return {CONVERSATION, STRIP, VOICE}
        turn = session.turn_for_input()
        if input_type == "chat":
            turn.input_type = "chat"
            turn.user_text = (turn.user_text + " " + text).strip()
            turn.user_interim = ""
            return {CONVERSATION}
        if data.get("final", True):
            turn.user_text = (turn.user_text + " " + text).strip()
            turn.user_interim = ""
            session.take_up_unheard(timestamp)
            perception = data.get("perception")
            if isinstance(perception, list):
                turn.perception = [p for p in perception if isinstance(p, dict)]
            speaker = data.get("speaker")
            if isinstance(speaker, dict):
                label = speaker.get("label") or speaker.get("id") or speaker.get("name")
                turn.speaker = str(label) if label is not None else turn.speaker
            elif isinstance(speaker, (str, int)):
                turn.speaker = str(speaker)
        else:
            turn.user_interim = text
        return {CONVERSATION}

    def _apply_bot_output(
        self, session: WorkerSession, data: dict[str, Any], timestamp: float
    ) -> set[Area]:
        text = str(data.get("text", ""))
        output_type = data.get("output_type") or data.get("text_type") or data.get("aggregated_by")
        if output_type == "backchannel":
            turn = session.current_turn or session.turn_for_output()
            if data.get("spoken_status") in (None, "new"):
                turn.backchannels.append(Backchannel(who="bot", text=text, timestamp=timestamp))
            return {CONVERSATION}
        turn = session.turn_for_output()
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
            segment = Segment(
                segment_id=segment_id,
                text=text,
                will_be_spoken=bool(will_be_spoken),
                output_type=str(output_type) if output_type else None,
            )
            turn.segments.append(segment)
            self._consume(turn, text)
        elif text and not segment.text:
            segment.text = text
        if status is not None:
            segment.spoken_status = status
        if isinstance(progress, dict):
            previous = len(segment.accumulated)
            segment.accumulated = str(progress.get("accumulated_text", segment.accumulated))
            segment.remaining = str(progress.get("remaining_text", segment.remaining))
        else:
            previous = len(segment.accumulated)
        delivery = data.get("delivery")
        if isinstance(delivery, list):
            # The delivery applies to the words just spoken.
            offset = previous if status == "in-progress" else 0
            segment.set_delivery(offset, tuple(str(d) for d in delivery))
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


# Contribution keys by the kind of service a TTFB came from.
_TTFB_KEYS = (
    ("STT", "transcription", "transcription"),
    ("LLM", "llm_inference", "LLM inference"),
    ("TTS", "speech_synthesis", "speech synthesis"),
)


def legacy_contributions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Build latency contributions from a breakdown that has none (before Pipecat 1.9).

    Older ``LatencyBreakdown`` messages carry the user turn duration, TTFB per
    service, the text aggregation and function call timings. They become
    contributions in time order, keyed the way newer Pipecat keys them.

    Args:
        data: The fields of a ``LatencyBreakdown``.

    Returns:
        Contributions with ``key``, ``label``, ``owner``, ``start_time`` and
        ``duration_secs``.
    """
    parts: list[dict[str, Any]] = []
    start = _number(data.get("user_turn_start_time"))
    turn_secs = _number(data.get("user_turn_secs"))
    if turn_secs:
        parts.append(
            {
                "key": "endpointing_wait",
                "label": "endpointing wait",
                "owner": "turn",
                "start_time": start or 0.0,
                "duration_secs": turn_secs,
            }
        )
    for entry in data.get("ttfb", []) or []:
        if not isinstance(entry, dict):
            continue
        processor = str(entry.get("processor", "?"))
        key, label = "service", "service"
        for needle, k, lbl in _TTFB_KEYS:
            if needle in processor:
                key, label = k, lbl
                break
        parts.append(
            {
                "key": key,
                "label": label,
                "owner": processor,
                "start_time": _number(entry.get("start_time")) or 0.0,
                "duration_secs": _number(entry.get("duration_secs")) or 0.0,
            }
        )
    aggregation = data.get("text_aggregation")
    if isinstance(aggregation, dict):
        parts.append(
            {
                "key": "sentence_aggregation",
                "label": "sentence aggregation",
                "owner": aggregation.get("processor"),
                "start_time": _number(aggregation.get("start_time")) or 0.0,
                "duration_secs": _number(aggregation.get("duration_secs")) or 0.0,
            }
        )
    for call in data.get("function_calls", []) or []:
        if isinstance(call, dict):
            parts.append(
                {
                    "key": "function_handler",
                    "label": "function handler",
                    "owner": call.get("function_name"),
                    "start_time": _number(call.get("start_time")) or 0.0,
                    "duration_secs": _number(call.get("duration_secs")) or 0.0,
                }
            )
    parts.sort(key=lambda p: p["start_time"])
    return parts


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
