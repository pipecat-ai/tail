#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The always-visible frame of the app: title line, HUD, error banner and key line.

The HUD answers the questions that matter on every view: what the bot is
doing (LISTENING, THINKING, SPEAKING), how long you have been waiting for it
or how long its last answer took (the voice-to-voice clock), where the time of
the current turn is going, and how the session is going.

Backgrounds are kept for key caps; everything else is plain text, so nothing
that is only a readout looks like a button.
"""

import math
import time
from typing import Optional

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widget import Widget

from pipecat_tail.state import (
    IDLE,
    LISTENING,
    SPEAKING,
    THINKING,
    LatencyRecord,
    SessionState,
    WorkerSession,
    percentile,
)
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.graphics import big_number, fit, pad, stacked
from pipecat_tail.widgets.latency import parts_of, stage_name, turn_bar
from pipecat_tail.widgets.render import fmt_elapsed, fmt_latency_digits, truncate

CAT = "ᓚᘏᗢ"

VIEWS = (
    ("live", "live"),
    ("latency", "latency"),
    ("pipeline", "pipeline"),
    ("metrics", "metrics"),
    ("logs", "logs"),
)

STATES = ((LISTENING, "user"), (THINKING, "llm"), (SPEAKING, "bot"))
"""The bot's states in the order of a turn, each in its color role."""

WIDE_HUD = 110
"""From this width the HUD is one band of three rows; below, it stacks into six."""

STATS_HUD = 132
"""From this width the HUD also shows how the session is going."""

PERCENTILE_SAMPLES = 20
"""Below this many answers, the worst one is shown instead of a p95."""


def _secs(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value < 1.0:
        return f"{value * 1000:.0f} ms"
    return f"{value:.2f} s"


def _phase_color(phase: str) -> str:
    p = palette.current()
    return {
        "end of turn": p.vad,
        "waiting": p.border,
        "llm": p.llm,
        "tool": p.tool,
        "first sentence": p.bot,
        "tts": p.tts,
    }.get(phase, p.dim)


def _phase_name(phase: str) -> str:
    return "before llm" if phase == "waiting" else phase


class TitleBar(Widget):
    """One line: brand, selected worker, what the bot runs on, and the call's status."""

    DEFAULT_CSS = """
    TitleBar { height: 1; }
    """

    def __init__(self, state: SessionState):
        """Create the bar."""
        super().__init__(id="title-bar")
        self.state = state

    def render(self):
        """Draw the bar."""
        p = palette.current()
        state = self.state
        width = self.size.width
        left = Text()
        left.append(f" {CAT} ", style=Style(color=p.accent, bold=True))
        left.append("tail ", style=Style(color=p.bright, bold=True))
        left.append(" ")
        session = state.selected
        workers = state.pipeline_workers
        worker = state.selected_worker or ("no bot" if not state.has_bot else "pipeline")
        left.append(worker, style=Style(color=p.bright, bold=True))
        if len(workers) > 1:
            index = (
                workers.index(state.selected_worker) + 1 if state.selected_worker in workers else 1
            )
            left.append(f" {index}/{len(workers)} ▾", style=Style(color=p.dim))
        left.append("  ")
        # Most telling first: when the line is short, the rest gives way.
        details = []
        model = session.llm_model
        if model:
            details.append(model)
        transport = next((n for n in session.processors if "Transport" in n), None)
        if transport:
            details.append(transport.replace("InputTransport", "Transport").split("#")[0])
        version = state.info.get("pipecat")
        if version:
            details.append(f"pipecat {version}")
        if state.runner:
            details.append(state.runner)
        right = self._status() + Text(" ")
        room = width - left.cell_len - right.cell_len - 1
        left.append_text(
            fit(
                [Text(d, style=Style(color=p.muted)) for d in details if d],
                room,
                Text(" · ", style=Style(color=p.muted)),
            )
        )
        gap = width - left.cell_len - right.cell_len
        if gap < 1:
            left.truncate(max(0, width - right.cell_len - 1), overflow="ellipsis")
            gap = width - left.cell_len - right.cell_len
        return left + Text(" " * max(gap, 0)) + right

    def _status(self) -> Text:
        """The call's status in words. The dot only changes when the connection does."""
        p = palette.current()
        state = self.state
        session = state.selected
        status = state.status
        text = Text()
        if status == "connected" and state.has_bot and not session.finished:
            text.append("● ", style=Style(color=p.ok))
            text.append("call ", style=Style(color=p.text))
            started = session.started_at or state.connected_at
            if started:
                text.append(fmt_elapsed(time.time() - started), style=Style(color=p.bright))
        elif status == "connected" and session.finished:
            turns = sum(1 for t in session.turns if t.number)
            text.append("■ ", style=Style(color=p.dim))
            text.append("call ended", style=Style(color=p.text))
            text.append(f" · {turns} turn{'s' if turns != 1 else ''}", style=Style(color=p.muted))
        elif status == "connected":
            text.append("● ", style=Style(color=p.ok))
            text.append("connected", style=Style(color=p.text))
        elif state.has_bot:
            # Tail lost the bot it was showing and keeps trying to get it back.
            text.append("○ ", style=Style(color=p.warn))
            text.append("bot gone", style=Style(color=p.warn, bold=True))
            if state.disconnected_at:
                text.append(
                    f" {fmt_elapsed(time.time() - state.disconnected_at)}",
                    style=Style(color=p.text),
                )
            text.append(" · showing the last call · retrying · ", style=Style(color=p.muted))
            text.append("c", style=Style(color=p.bright, bold=True))
            text.append(" now", style=Style(color=p.muted))
        elif status == "error":
            text.append("✕ ", style=Style(color=p.err))
            text.append(
                truncate(state.status_detail or "can't reach the bot", 48),
                style=Style(color=p.err),
            )
            text.append(" · retrying", style=Style(color=p.muted))
        else:
            text.append("○ ", style=Style(color=p.dim))
            text.append("waiting for a bot", style=Style(color=p.dim))
        return text


class Hud(Widget):
    """The bot's state, the voice-to-voice clock, the current turn and the session.

    Three rows on wide terminals. Below ``WIDE_HUD`` columns the current
    turn moves under the clock, in three more rows.
    """

    DEFAULT_CSS = """
    Hud { height: 3; }
    """

    def __init__(self, state: SessionState):
        """Create the HUD."""
        super().__init__(id="hud")
        self.state = state

    def render(self):
        """Draw the HUD."""
        state = self.state
        session = state.selected
        now = state.now()
        width = self.size.width
        connected = state.status == "connected" and state.has_bot
        bot_state = session.bot_state_at(now) if connected else IDLE

        states = self._states(bot_state)
        clock, caption = self._clock(session, now, bot_state)
        states_w, clock_w = 13, 19
        caption_w = 17 if width >= states_w + clock_w + 15 else 0
        head_w = states_w + clock_w + caption_w
        sep = Text(" │ ", style=Style(color=palette.current().border))

        if width < WIDE_HUD:
            turn = self._turn(session, now, width - 2)
            lines = []
            for r in range(3):
                row = pad(states[r], states_w) + pad(clock[r], clock_w)
                if caption_w:
                    row.append_text(pad(caption[r], caption_w))
                lines.append(pad(row, width))
            for r in range(3):
                lines.append(pad(Text(" ") + turn[r], width))
            return Group(*lines)

        stats_w = (40 if width >= 150 else 34) if width >= STATS_HUD else 0
        turn_w = width - head_w - 3 - (stats_w + 3 if stats_w else 0)
        turn = self._turn(session, now, turn_w)
        stats = self._stats(session, stats_w) if stats_w else None
        lines = []
        for r in range(3):
            row = pad(states[r], states_w) + pad(clock[r], clock_w) + pad(caption[r], caption_w)
            row.append_text(sep)
            row.append_text(pad(turn[r], turn_w))
            if stats:
                row.append_text(sep)
                row.append_text(pad(stats[r], stats_w))
            lines.append(pad(row, width))
        return Group(*lines)

    # The three states

    def _states(self, bot_state: str) -> list[Text]:
        p = palette.current()
        rows = []
        for name, role in STATES:
            color = getattr(p, role)
            if name == bot_state:
                row = Text(" ▸ ", style=Style(color=color))
                row.append(name.upper(), style=Style(color=color, bold=True))
            else:
                row = Text("   " + name.upper(), style=Style(color=p.muted))
            rows.append(row)
        return rows

    # The voice-to-voice clock

    def _clock(
        self, session: WorkerSession, now: float, bot_state: str
    ) -> tuple[list[Text], list[Text]]:
        """The large digits and their caption.

        While the bot thinks the digits count up in the LLM's color; once it
        speaks they stop, in the color of their grade, until the next wait.
        """
        p = palette.current()
        target = palette.latency_target()
        record = _last_record(session)
        seconds: Optional[float] = None
        greeting = False
        live = bot_state == THINKING and session.waiting_since is not None
        if live and session.waiting_since is not None:
            seconds = max(0.0, now - session.waiting_since)
        elif _frozen(session, record) and session.wait_ended_at and session.wait_started_at:
            seconds = session.wait_ended_at - session.wait_started_at
        elif record is not None:
            seconds = record.latency_secs
            greeting = record.first_bot_speech

        if seconds is None:
            digits = big_number("-.--", p.border, "s", p.border)
            caption = [
                Text("voice-to-voice", style=Style(color=p.muted)),
                Text(f"target {_secs(target)}", style=Style(color=p.muted)),
                Text(""),
            ]
            return [Text(" ") + d for d in digits], caption

        value, unit = fmt_latency_digits(seconds)
        color = p.llm if live else palette.latency_color(seconds)
        digits = big_number(value, color, unit, color)
        if greeting:
            caption = [
                Text("greeting", style=Style(color=p.muted)),
                Text("since joining", style=Style(color=p.muted)),
                Text(""),
            ]
            return [Text(" ") + d for d in digits], caption
        grade = palette.latency_grade(seconds)
        mark = {"on target": "✓", "slow": "▲"}.get(grade, "✕")
        delta = seconds - target
        sign = "+" if delta >= 0 else "-"
        caption = [
            Text("voice-to-voice", style=Style(color=p.muted)),
            Text("target ", style=Style(color=p.muted))
            + Text(_secs(target), style=Style(color=p.text)),
            Text(
                f"{mark} {sign}{abs(delta):.2f} s",
                style=Style(color=palette.latency_color(seconds), bold=True),
            ),
        ]
        return [Text(" ") + d for d in digits], caption

    # The current turn

    def _turn(self, session: WorkerSession, now: float, width: int) -> list[Text]:
        """Where the current wait is going, or the last turn's breakdown.

        While the bot thinks: the stages so far as they happen. Once it
        speaks: the same stages, stopped, until Pipecat's measured breakdown
        of the turn arrives and takes their place.
        """
        p = palette.current()
        record = _last_record(session)
        live = session.waiting_since is not None
        if live or _frozen(session, record):
            return self._wait(session, now, width, live)
        if record is None:
            return [
                Text("no answers yet", style=Style(color=p.muted)),
                Text(""),
                Text(""),
            ]
        return self._record(record, width)

    def _wait(self, session: WorkerSession, now: float, width: int, live: bool) -> list[Text]:
        p = palette.current()
        phases = session.wait_phases(now)
        total = sum(b - a for _, a, b in phases)
        turn = session.current_turn
        number = turn.number if turn and turn.number else None
        label = Text(f"turn {number}" if number else "this turn", style=Style(color=p.text))
        if live:
            label.append(" so far", style=Style(color=p.muted))
        else:
            label.append(f"  {_secs(total)}", style=Style(color=palette.latency_color(total)))
        scale = max(2.0, float(math.ceil(total)))
        bar_w = max(8, width - 2)
        head = self._target_line(label, scale, bar_w)
        bar = stacked([(b - a, _phase_color(name)) for name, a, b in phases], scale / bar_w, bar_w)
        if live and phases:
            at = min(bar_w - 1, math.ceil(total / scale * bar_w))
            bar = pad(bar, at)
            bar.append("▸", style=Style(color=_phase_color(phases[-1][0])))
        items: list[Text] = []
        note: Optional[Text] = None
        for index, (name, a, b) in enumerate(phases):
            secs = b - a
            if name == "waiting" and secs < 0.1:
                continue
            item = Text(_phase_name(name), style=Style(color=_phase_color(name)))
            if name == "tool":
                call = _running_call(session) if live and index == len(phases) - 1 else None
                if call:
                    item.append(f" {call}", style=Style(color=p.dim))
            item.append(f" {_secs(secs)}", style=Style(color=p.bright, bold=True))
            if live and index == len(phases) - 1 and name == "llm":
                note = Text(", no first token yet", style=Style(color=p.muted))
            items.append(item)
        sep = Text(" · ", style=Style(color=p.muted))
        # While the bot thinks, what it is doing now matters most.
        legend = fit(items, width, sep, keep_last=live)
        # The note goes first when space is short; the figures stay.
        if note is not None and len(legend.plain) == len(sep.join(items).plain):
            if legend.cell_len + note.cell_len <= width:
                legend.append_text(note)
        return [head, bar, legend]

    def _record(self, record: LatencyRecord, width: int) -> list[Text]:
        p = palette.current()
        color = palette.latency_color(record.latency_secs)
        if record.first_bot_speech:
            label = Text("greeting", style=Style(color=p.text))
        else:
            label = Text(f"turn {record.turn or '?'}", style=Style(color=p.text))
        label.append(f"  {_secs(record.latency_secs)}", style=Style(color=color, bold=True))
        scale = max(2.0, float(math.ceil(record.latency_secs)))
        bar_w = max(8, width - 2)
        head = self._target_line(label, scale, bar_w)
        bar = turn_bar(record, scale / bar_w, bar_w)
        items = [
            Text(stage_name(key), style=Style(color=c))
            + Text(f" {_secs(secs)}", style=Style(color=p.bright, bold=True))
            for secs, c, key, _ in parts_of(record)
        ]
        if items:
            legend = fit(items, width, Text(" · ", style=Style(color=p.muted)))
        elif record.first_bot_speech:
            legend = Text(
                "from the client joining to the bot's first words", style=Style(color=p.muted)
            )
        else:
            legend = Text("per-stage times need enable_metrics=True", style=Style(color=p.muted))
        if legend.cell_len > width:
            legend.truncate(width, overflow="ellipsis")
        return [head, bar, legend]

    def _target_line(self, label: Text, scale: float, bar_w: int) -> Text:
        """The turn's label, with a tick over the bar where the target falls."""
        p = palette.current()
        target = palette.latency_target()
        at = int(target / scale * bar_w)
        line = label.copy()
        if at > line.cell_len + 1:
            line = pad(line, at)
            line.append("┊", style=Style(color=p.dim))
            note = f" {_secs(target)} target"
            if line.cell_len + len(note) <= bar_w + 2:
                line.append(note, style=Style(color=p.muted))
        return line

    # The session

    def _stats(self, session: WorkerSession, width: int) -> list[Text]:
        p = palette.current()
        latencies = [r.latency_secs for r in session.latencies if not r.first_bot_speech]
        muted = Style(color=p.muted)

        def value(seconds: Optional[float]) -> Text:
            color = palette.latency_color(seconds) if seconds else p.dim
            return Text(_secs(seconds), style=Style(color=color, bold=True))

        first = Text("median ", style=muted) + value(percentile(latencies, 0.5))
        if len(latencies) >= PERCENTILE_SAMPLES:
            first += Text(" · p95 ", style=muted) + value(percentile(latencies, 0.95))
        else:
            first += Text(" · worst ", style=muted) + value(max(latencies) if latencies else None)

        if latencies:
            on_target = sum(1 for s in latencies if s < palette.latency_target())
            second = Text()
            second.append(f"{on_target}", style=Style(color=p.bright, bold=True))
            second.append(f" of {len(latencies)} answers on target", style=muted)
        else:
            second = Text("no answers yet", style=muted)

        errors = [e for e in self.state.errors if e.worker in (None, session.name)]
        # What went wrong in the conversation, in amber or red when it happened.
        counts = [
            (session.early_end_count, "early end", "early", p.warn),
            (session.interruptions, "interruption", "cut", p.err),
            (len(errors), "error", "error", p.err),
            (session.backchannel_count, "backchannel", "mhm", p.bright),
        ]
        counts = [c for c in counts if c[0] or c[1] != "backchannel"]

        def line(items: list[tuple[int, str, str, str]], short: bool = False) -> Text:
            text = Text()
            for count, word, brief, color in items:
                if text:
                    text.append(" · ", style=muted)
                text.append(
                    f"{count}", style=Style(color=color if count else p.dim, bold=bool(count))
                )
                if short:
                    text.append(f" {brief}", style=muted)
                else:
                    text.append(f" {word}{'s' if count != 1 else ''}", style=muted)
            return text

        third = line(counts)
        # When the column is full, what didn't happen gives way first, then
        # backchannels, then the long words.
        while third.cell_len > width and any(not c[0] for c in counts):
            index = max(i for i, c in enumerate(counts) if not c[0])
            counts.pop(index)
            third = line(counts)
        if third.cell_len > width and counts[-1][1] == "backchannel":
            counts.pop()
            third = line(counts)
        if third.cell_len > width:
            third = line(counts, short=True)
        return [first, second, third]


def _last_record(session: WorkerSession) -> Optional[LatencyRecord]:
    """The latest measured answer, preferring turns over the greeting."""
    record = next((r for r in reversed(session.latencies) if not r.first_bot_speech), None)
    return record or session.last_latency


def _frozen(session: WorkerSession, record: Optional[LatencyRecord]) -> bool:
    """Whether the bot answered a wait that has no measured breakdown yet."""
    if session.wait_ended_at is None or session.wait_started_at is None:
        return False
    return record is None or record.timestamp < session.wait_started_at


def _running_call(session: WorkerSession) -> Optional[str]:
    turn = session.current_turn
    for call in reversed(turn.function_calls if turn else []):
        if not call.settled:
            return call.function_name
    return None


class ErrorBanner(Widget):
    """One line with the latest error, until dismissed."""

    DEFAULT_CSS = """
    ErrorBanner { height: 1; display: none; }
    ErrorBanner.visible { display: block; }
    """

    def __init__(self, state: SessionState):
        """Create the banner."""
        super().__init__(id="banner")
        self.state = state

    def update_from(self, state: SessionState) -> None:
        """Show the banner when there are errors that have not been dismissed."""
        self.set_class(bool(state.active_errors), "visible")
        self.refresh()

    def render(self):
        """Draw the banner."""
        p = palette.current()
        errors = self.state.active_errors
        if not errors:
            return Text("")
        error = errors[-1]
        text = Text()
        text.append(" ✕ ", style=Style(color=p.err, bold=True))
        text.append(error.processor, style=Style(color=p.err, bold=True))
        text.append(f" · {error.category}", style=Style(color=p.dim))
        if not error.processor_usable:
            text.append(" · unusable", style=Style(color=p.err, bold=True))
        text.append("  ")
        text.append(
            truncate(error.message, max(20, self.size.width - text.cell_len - 40)),
            style=Style(color=p.text),
        )
        extra = f"  +{len(errors) - 1} more" if len(errors) > 1 else ""
        hint = Text(extra, style=Style(color=p.dim))
        hint.append("   e ", style=Style(color=p.bright, bold=True))
        hint.append("logs", style=Style(color=p.muted))
        hint.append("  x ", style=Style(color=p.bright, bold=True))
        hint.append("dismiss ", style=Style(color=p.muted))
        gap = self.size.width - text.cell_len - hint.cell_len
        text.append(" " * max(gap, 1))
        text.append_text(hint)
        return text


SHORT_LABELS = {"end search": "end"}
"""Labels the key line shortens in a narrow pane before it drops keys."""


class KeyBar(Widget):
    """The keys that work on the current view, then the views, the active one underlined.

    Click a view to open it. When the line is too short, the views fold into
    ``1-5`` and the active one, then the view's keys give way from the last.
    Following is normal, so ``f`` shows only once the view is paused, as
    ``follow`` in amber. During a search, ``n`` and ``p`` step through
    matches instead of issues.
    """

    DEFAULT_CSS = """
    KeyBar { height: 1; }
    """

    class ViewSelected(Message):
        """A view was clicked."""

        def __init__(self, view: str):
            """Create the message."""
            super().__init__()
            self.view = view

    VIEW_KEYS = {
        # Most needed first: when the line is short, the last ones give way.
        "live": (
            ("⏎", "inspect"),
            ("f", "follow"),
            ("n/p", "issues"),
            ("/", "search"),
            ("+/-", "zoom"),
        ),
        "latency": (("↑↓", "turns"), ("⏎", "inspect")),
        "pipeline": (("b", "bus filter"),),
        "metrics": (),
        "logs": (("/", "search"), ("l", "level"), ("e", "next error"), ("f", "follow")),
    }

    def __init__(self):
        """Create the bar."""
        super().__init__(id="key-bar")
        self.view = "live"
        self.following = True
        self.searching = False
        self._spans: list[tuple[int, int, str]] = []

    def set_view(self, view: str) -> None:
        """Show the keys of a view."""
        self.view = view
        self.refresh()

    def set_following(self, following: bool) -> None:
        """Say whether the current view follows what's new, for the ``f`` key."""
        if following != self.following:
            self.following = following
            self.refresh()

    def set_searching(self, searching: bool) -> None:
        """Say whether a search is on: ``n`` then steps through matches, ``esc`` ends it."""
        if searching != self.searching:
            self.searching = searching
            self.refresh()

    def _keys(self) -> list[tuple[str, str]]:
        keys = list(self.VIEW_KEYS.get(self.view, ()))
        if self.following:
            keys = [(key, label) for key, label in keys if key != "f"]
        if self.view == "live" and self.searching:
            swap = {"n/p": ("n/p", "matches"), "/": ("esc", "end search")}
            keys = [swap.get(key, (key, label)) for key, label in keys]
        return keys

    def render(self):
        """Draw the bar: the most that fits, from all views spelled out to the bare minimum."""
        width = self.size.width
        view_keys = self._keys()
        # Then shorter labels, then the last keys give way.
        short = [(key, SHORT_LABELS.get(label, label)) for key, label in view_keys]
        tries = [
            (view_keys, True, True),
            (view_keys, True, False),
            (view_keys, False, True),
            (view_keys, False, False),
        ] + [(short[:count], False, False) for count in range(len(short), -1, -1)]
        text, spans = Text(), []
        for keys, all_views, quit_key in tries:
            text, spans = self._line(keys, all_views, quit_key)
            if text.cell_len <= width:
                break
        self._spans = spans
        return text

    def _line(
        self, keys: list[tuple[str, str]], all_views: bool, quit_key: bool
    ) -> tuple[Text, list[tuple[int, int, str]]]:
        p = palette.current()
        text = Text(" ")
        spans: list[tuple[int, int, str]] = []

        def cap(key: str) -> None:
            text.append(f" {key} ", style=Style(color=p.ink, bgcolor=p.dim, bold=True))

        def sep() -> None:
            text.append(" │ ", style=Style(color=p.border))

        for key, label in keys:
            cap(key)
            style = Style(color=p.dim)
            if key == "f":
                # Only shown while paused: following again is the way back.
                style = Style(color=p.warn, bold=True)
            text.append(f" {label}", style=style)
            text.append("  ")
        if keys:
            sep()
        active = Style(color=p.bright, bold=True, underline=True)
        if all_views:
            for index, (view, label) in enumerate(VIEWS, 1):
                start = text.cell_len
                cap(str(index))
                text.append(" ")
                text.append(label, style=active if view == self.view else Style(color=p.dim))
                spans.append((start, text.cell_len, view))
                text.append("  ")
        else:
            cap(f"1-{len(VIEWS)}")
            text.append(" ")
            label = dict(VIEWS).get(self.view, self.view)
            text.append(label, style=active)
            text.append("  ")
        sep()
        cap("?")
        text.append(" help  ", style=Style(color=p.dim))
        if quit_key:
            cap("q")
            text.append(" quit", style=Style(color=p.dim))
        text.rstrip()
        return text, spans

    def on_click(self, event: events.Click) -> None:
        """Open the clicked view."""
        for start, end, view in self._spans:
            if start <= event.x < end:
                self.post_message(self.ViewSelected(view))
                return
