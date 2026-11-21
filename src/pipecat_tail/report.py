#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The session as data: to keep, compare, share or check in CI.

- ``turn_record`` and ``session_summary`` describe a turn and a session as
  plain dicts.
- ``report`` and ``markdown`` turn the whole state into a JSON report or a
  readable one; ``save_report`` writes them (``s`` in the app, ``--report``
  on exit).
- ``JsonStream`` is ``pipecat tail --json``: no UI, one JSON object per line
  on stdout as the session happens, for scripts and tests.
"""

import asyncio
import datetime
import json
import sys
from pathlib import Path
from typing import Any, Optional, TextIO

from pipecat_tail.state import ErrorRecord, SessionState, Turn, WorkerSession, percentile
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.render import fmt_secs


def _round(value: Optional[float], digits: int = 3) -> Optional[float]:
    return round(value, digits) if isinstance(value, (int, float)) else None


def _iso(timestamp: Optional[float]) -> Optional[str]:
    if not isinstance(timestamp, (int, float)):
        return None
    return datetime.datetime.fromtimestamp(timestamp).isoformat(timespec="milliseconds")


def _stages(turn: Turn) -> list[dict[str, Any]]:
    record = turn.latency
    if record is None:
        return []
    return [
        {
            "stage": s.key,
            "name": palette.stage_name(s.key),
            "owner": s.owner or None,
            "secs": _round(s.secs),
        }
        for s in record.stages
    ]


def turn_record(turn: Turn) -> dict[str, Any]:
    """One turn as plain data: what was said, how long it took, what ran."""
    spoken, total = turn.spoken_word_counts()
    bot_text = " ".join(s.text.strip() for s in turn.segments if s.text.strip())
    pending = turn.pending_text.strip()
    if pending:
        bot_text = f"{bot_text} {pending}".strip()
    if not bot_text:
        bot_text = (turn.tts_text or turn.llm_text).strip()
    tone = turn.tone
    return {
        "turn": turn.number,
        "started_at": _iso(turn.restarted_at or turn.opened_at or turn.started_at),
        "user": {
            "text": turn.user_text or turn.user_interim,
            "input": turn.input_type,
            "tone": {"label": tone[0], "confidence": _round(tone[1], 2)} if tone else None,
            "speaker": turn.speaker,
        },
        "bot": {
            "text": bot_text,
            "spoken": " ".join(s.spoken_text.strip() for s in turn.segments if s.spoken_text),
            "interrupted": turn.interrupted,
            "words_spoken": spoken,
            "words_total": total,
        },
        "latency_secs": _round(turn.latency_secs),
        "grade": palette.latency_grade(turn.latency_secs) or None,
        "stages": _stages(turn),
        "tools": [
            {
                "name": call.function_name,
                "arguments": call.arguments,
                "result": call.result,
                "error": call.error,
                "secs": _round(call.duration_secs),
                "status": "failed" if call.failed else ("done" if call.settled else "running"),
            }
            for call in turn.function_calls
        ],
        "backchannels": [{"who": b.who, "text": b.text} for b in turn.backchannels],
        "early_ends": [
            {
                "after": e.heard,
                "resumed_after_secs": _round(e.gap_secs),
                "llm_started": e.llm_started,
            }
            for e in turn.early_ends
        ],
        "context_messages": (
            len(turn.context_items)
            if turn.context_items is not None
            else (turn.context or {}).get("messages")
        ),
        "duration_secs": _round(turn.duration_secs),
    }


def _error(error: ErrorRecord) -> dict[str, Any]:
    return {
        "at": _iso(error.timestamp),
        "processor": error.processor,
        "category": error.category,
        "message": error.message,
        "fatal": not error.processor_usable,
        "worker": error.worker,
    }


def session_summary(state: SessionState, session: WorkerSession) -> dict[str, Any]:
    """A session in numbers: latency, stages, usage, errors."""
    records = session.answers
    latencies = [r.latency_secs for r in records]
    target = palette.latency_target()
    stages: dict[str, list[float]] = {}
    for record in records:
        for stage in record.stages:
            stages.setdefault(stage.key, []).append(stage.secs)
    total = sum(sum(v) for v in stages.values()) or 1.0
    turns = [t for t in session.turns if t.has_content]
    last = session.events[-1][0] if session.events else None
    return {
        "worker": session.name,
        "runner": state.runner,
        "pipecat": state.info.get("pipecat"),
        "llm": session.llm_model,
        "processors": session.processors,
        "started_at": _iso(session.started_at),
        "duration_secs": _round(last - session.started_at, 1)
        if last and session.started_at
        else None,
        "finished": session.finished,
        "turn_count": len(turns),
        "latency": {
            "target_secs": target,
            "turns": len(latencies),
            "p50": _round(percentile(latencies, 0.5)),
            "p95": _round(percentile(latencies, 0.95)),
            "max": _round(max(latencies)) if latencies else None,
            "on_target": _round(
                sum(1 for v in latencies if palette.on_target(v)) / len(latencies), 2
            )
            if latencies
            else None,
            "greeting": _round(next((r.latency_secs for r in session.latencies), None))
            if session.latencies and session.latencies[0].first_bot_speech
            else None,
        },
        "stages": {
            key: {
                "name": palette.stage_name(key),
                "p50": _round(percentile(values, 0.5)),
                "p95": _round(percentile(values, 0.95)),
                "share": _round(sum(values) / total, 3),
            }
            for key, values in stages.items()
        },
        "interruptions": session.interruptions,
        "early_ends": session.early_end_count,
        "backchannels": session.backchannel_count,
        "errors": [_error(e) for e in state.errors_for(session)],
        "usage": {
            "prompt_tokens": session.prompt_tokens,
            "completion_tokens": session.completion_tokens,
            "cached_tokens": session.cached_tokens,
            "tts_characters": session.tts_characters,
            "stt_seconds": _round(session.stt_seconds, 1),
        },
    }


def _sessions(state: SessionState) -> list[WorkerSession]:
    return [s for s in state.sessions.values() if s.started_at is not None or s.turns]


def report(state: SessionState) -> dict[str, Any]:
    """The whole state as a JSON-ready report: every session with its turns."""
    from pipecat_tail.__version__ import version

    return {
        "tail": version,
        "generated_at": _iso(datetime.datetime.now().timestamp()),
        "sessions": [
            {
                **session_summary(state, session),
                "turns": [turn_record(t) for t in session.turns if t.has_content],
            }
            for session in _sessions(state)
        ],
    }


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    """A Markdown table, a line each for the header, the rule and every row."""

    def line(cells: list[Any]) -> str:
        return "| " + " | ".join(str(c) for c in cells) + " |"

    return [line(header), "|" + "---|" * len(header), *(line(row) for row in rows)]


def markdown(state: SessionState) -> str:
    """The report as Markdown, to read or paste into an issue."""
    data = report(state)
    out: list[str] = []
    for session in data["sessions"]:
        latency = session["latency"]
        title = session["worker"] or "session"
        out.append(f"# Tail report · {title}")
        out.append("")
        details = [
            f"pipecat {session['pipecat']}" if session["pipecat"] else None,
            session["llm"],
            f"{session['turn_count']} turns",
            f"{session['duration_secs']:.0f} s" if session["duration_secs"] else None,
            session["started_at"],
        ]
        out.append(" · ".join(d for d in details if d))
        out.append("")
        on_target = latency["on_target"]
        out += _table(
            ["p50", "p95", "max", f"under {fmt_secs(latency['target_secs'])}", "early ends"]
            + ["interruptions", "errors"],
            [
                [
                    fmt_secs(latency["p50"]),
                    fmt_secs(latency["p95"]),
                    fmt_secs(latency["max"]),
                    f"{on_target:.0%}" if on_target is not None else "—",
                    session["early_ends"],
                    session["interruptions"],
                    len(session["errors"]),
                ]
            ],
        )
        if session["stages"]:
            out += ["", "## Where the time goes", ""]
            out += _table(
                ["stage", "p50", "p95", "share"],
                [
                    [s["name"], fmt_secs(s["p50"]), fmt_secs(s["p95"]), f"{s['share']:.0%}"]
                    for s in session["stages"].values()
                ],
            )
        out += ["", "## Turns", ""]
        for turn in session["turns"]:
            head = f"### Turn {turn['turn']}"
            if turn["latency_secs"] is not None:
                head += f" · {fmt_secs(turn['latency_secs'])} ({turn['grade']})"
            out += [head, ""]
            user = turn["user"]
            if user["text"]:
                typed = " (typed)" if user["input"] == "chat" else ""
                out.append(f"**you{typed}:** {user['text']}  ")
            for early in turn["early_ends"]:
                out.append(
                    f"*▲ Pipecat ended the turn after “{early['after']}”; the user went on "
                    f"{fmt_secs(early['resumed_after_secs'])} later.*  "
                )
            for tool in turn["tools"]:
                outcome = tool["error"] or json.dumps(tool["result"], default=str)
                out.append(
                    f"**ƒ {tool['name']}**({json.dumps(tool['arguments'], default=str)}) → "
                    f"{outcome[:200]} · {fmt_secs(tool['secs'])}  "
                )
            bot = turn["bot"]
            if bot["text"]:
                cut = (
                    f" *(interrupted after {bot['words_spoken']} of {bot['words_total']} words)*"
                    if bot["interrupted"]
                    else ""
                )
                out.append(f"**bot:** {bot['text']}{cut}  ")
            if turn["stages"]:
                out.append(
                    "*"
                    + " · ".join(f"{s['name']} {fmt_secs(s['secs'])}" for s in turn["stages"])
                    + "*"
                )
            out.append("")
        if session["errors"]:
            out += ["## Errors", ""]
            for error in session["errors"]:
                out.append(
                    f"- {error['at']} **{error['processor']}** ({error['category']}): "
                    f"{error['message']}"
                )
            out.append("")
    return "\n".join(out) if out else "# Tail report\n\nNo sessions yet.\n"


def write_report(state: SessionState, path: Path) -> Path:
    """Write the report to ``path``: Markdown for ``.md``, JSON otherwise."""
    path = Path(path)
    if path.suffix.lower() in (".md", ".markdown"):
        path.write_text(markdown(state))
    else:
        path.write_text(json.dumps(report(state), indent=2, default=str))
    return path


def save_report(state: SessionState, directory: Path = Path(".")) -> tuple[Path, Path]:
    """Write the report as JSON and Markdown, named by the time, and return both paths."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(directory) / f"tail-report-{stamp}"
    return (
        write_report(state, base.with_suffix(".json")),
        write_report(state, base.with_suffix(".md")),
    )


def _key(session: WorkerSession, turn: Turn) -> tuple:
    # Not id(turn): the session drops old turns, and their ids get reused.
    return (session.name, turn.number, turn.started_at)


class JsonStream:
    """``pipecat tail --json``: the session as JSON lines, no UI.

    Events, one per line: ``connected``, ``session_started``, ``turn`` (when
    a turn ends), ``error`` and ``session_finished`` (with the summary).
    It ends when the bot goes away after having been seen, printing the
    summary of any session that didn't finish.
    """

    def __init__(self, url: str, *, out: TextIO = sys.stdout, report_path: Optional[Path] = None):
        """Create the stream.

        Args:
            url: Tail server URL.
            out: Where to write the lines.
            report_path: Where to write the full report at the end, if anywhere.
        """
        self.url = url
        self.out = out
        self.report_path = report_path
        self.state = SessionState()
        self._emitted: set[tuple] = set()
        self._finished: set[Optional[str]] = set()
        self._errors = 0
        self._done = asyncio.Event()

    def emit(self, event: str, **data: Any) -> None:
        """Write one event line."""
        self.out.write(json.dumps({"event": event, **data}, default=str) + "\n")
        self.out.flush()

    async def run(self) -> None:
        """Connect and stream until the bot goes away."""
        from pipecat_tail.client import TailClient

        client = TailClient(self.url, on_message=self.on_message, on_status=self.on_status)
        client.start()
        try:
            await self._done.wait()
        finally:
            await client.stop()
            self.finish()

    async def on_message(self, message: dict[str, Any]) -> None:
        """Fold a message into the state and emit what it completed."""
        self.state.apply(message)
        msg_type = message.get("type")
        worker = message.get("worker")
        raw = message.get("data")
        data: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if msg_type == "tail-ready":
            self.emit(
                "connected",
                pipecat=data.get("pipecat"),
                runner=data.get("runner"),
                capabilities=data.get("capabilities"),
            )
        elif msg_type == "tail-pipeline-started":
            self.emit("session_started", worker=worker, processors=data.get("processors"))
        # Errors are counted as they come: the oldest are dropped from the list.
        new = self.state.errors_seen - self._errors
        for error in list(self.state.errors)[-new:] if new else []:
            self.emit("error", **_error(error))
        self._errors = self.state.errors_seen
        for session in self.state.sessions.values():
            for turn in session.turns:
                if turn.ended and turn.has_content and _key(session, turn) not in self._emitted:
                    self._emit_turn(session, turn)
        if msg_type == "tail-pipeline-finished":
            self._finish_session(self.state.session(worker if isinstance(worker, str) else None))

    async def on_status(self, status: str, detail: Optional[str]) -> None:
        """End when the bot goes away after having been seen."""
        if status in ("disconnected", "error") and self.state.has_bot:
            self._done.set()

    def stop(self) -> None:
        """End the stream, as when the bot goes away."""
        self._done.set()

    def _emit_turn(self, session: WorkerSession, turn: Turn) -> None:
        self._emitted.add(_key(session, turn))
        self.emit("turn", worker=session.name, **turn_record(turn))

    def _finish_session(self, session: WorkerSession) -> None:
        if session.name in self._finished:
            return
        self._finished.add(session.name)
        for turn in session.turns:
            if turn.has_content and _key(session, turn) not in self._emitted:
                self._emit_turn(session, turn)
        self.emit("session_finished", **session_summary(self.state, session))

    def finish(self) -> None:
        """Flush what's left and write the report."""
        for session in _sessions(self.state):
            self._finish_session(session)
        if self.report_path is not None:
            write_report(self.state, self.report_path)
