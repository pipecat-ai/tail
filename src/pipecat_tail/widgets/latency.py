#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Latency tab: per-turn breakdown bars and the speaking timeline."""

import time
from typing import Optional

from rich.style import Style
from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from pipecat_tail.state import LatencyRecord, WorkerSession, percentile
from pipecat_tail.widgets.render import (
    DIM,
    FROST_CYAN,
    GREEN,
    POLAR_1,
    PURPLE,
    RED,
    STYLE_BRIGHT,
    STYLE_DIM,
    STYLE_LABEL,
    STYLE_TEXT,
    STYLE_WARN,
    contribution_color,
    fmt_clock,
    fmt_secs_fixed,
    stacked_bar,
)

MAX_ROWS = 500
TIMELINE_SECS = 30.0

# The order the waits happen in a turn, for the legend.
CONTRIBUTION_ORDER = [
    "endpointing_wait",
    "first_request",
    "transcription",
    "turn_detection",
    "turn_completion",
    "waiting_for_user",
    "llm_inference",
    "llm_tool_call",
    "function_handler",
    "sentence_aggregation",
    "awaiting_speakable_text",
    "speech_synthesis",
    "output_transport",
]


def _scale_for(records: list[LatencyRecord]) -> float:
    """Seconds that fill a full bar: the longest turn rounded up to a friendly step."""
    longest = max([(r.total_secs or r.latency_secs) for r in records] + [0.5])
    step = 0.5
    while step < longest:
        step *= 2
    return step


def _bar_width(widget_width: int) -> int:
    width = max(widget_width - 2, 40)
    return max(width - 26, 20)


def _ruler(scale: float, bar_width: int) -> Text:
    text = Text("turn   ", style=STYLE_LABEL)
    ruler = Text()
    for tick in range(0, 5):
        secs = scale * tick / 4
        label = f"{secs:g}s"
        position = int(bar_width * tick / 4)
        while len(ruler) < position:
            ruler.append(" ")
        ruler.append(label, style=STYLE_LABEL)
    text.append_text(ruler)
    return text


class LatencyBars(Vertical):
    """Stacked bar per turn under a fixed time scale, following the newest turn."""

    def __init__(self):
        """Create the widget."""
        super().__init__(id="latency-bars")
        self.border_title = "Per-turn latency"
        self.border_subtitle = "user stops speaking → bot starts speaking"
        self._session: Optional[WorkerSession] = None
        self._ruler = Static(Text(""), classes="latency-ruler")
        self._ruler_bottom = Static(Text(""), classes="latency-ruler")
        self._scroll = VerticalScroll(id="latency-scroll")
        self._body = Static(Text(""))

    def compose(self):
        """Compose the scale above and below the scrolling bars."""
        yield self._ruler
        with self._scroll:
            yield self._body
        yield self._ruler_bottom

    def set_session(self, session: WorkerSession) -> None:
        """Point the widget at a session and re-render."""
        self._session = session
        self.refresh_view()

    def on_resize(self) -> None:
        """Re-render when the width changes."""
        self.refresh_view()

    def refresh_view(self) -> None:
        """Re-render from the session and keep the newest turn in view."""
        session = self._session
        if session is None or not session.latencies:
            ruler = Text("")
        else:
            records = session.latencies[-MAX_ROWS:]
            ruler = _ruler(_scale_for(records), _bar_width(self.size.width))
        self._ruler.update(ruler)
        self._ruler_bottom.update(ruler.copy())
        self._body.update(self._build())
        self.call_after_refresh(self._scroll.scroll_end, animate=False)

    def _build(self) -> Text:
        text = Text()
        session = self._session
        if session is None or not session.latencies:
            text.append("No turns measured yet.\n", style=STYLE_DIM)
            text.append(
                "Latency is measured from the user going silent to the bot starting to speak. "
                "The breakdown needs enable_metrics=True in PipelineParams.",
                style=STYLE_DIM,
            )
            return text

        records = session.latencies[-MAX_ROWS:]
        bar_width = _bar_width(self.size.width)
        scale = _scale_for(records)

        worst = max(records, key=lambda r: r.latency_secs)
        for record in records:
            turn = f"{record.turn:>4}" if record.turn is not None else "   ?"
            text.append(f"{turn}   ", style=STYLE_TEXT)
            parts = [
                (c.get("duration_secs", 0.0), contribution_color(c.get("key", "")))
                for c in record.contributions
            ]
            if not parts:
                parts = [(record.latency_secs, DIM)]
            bar = stacked_bar(parts, bar_width, scale)
            text.append_text(bar)
            pad = bar_width - len(bar) + 2
            text.append(" " * max(pad, 1))
            style = STYLE_WARN if record is worst and len(records) > 1 else STYLE_TEXT
            text.append(fmt_secs_fixed(record.latency_secs), style=style)
            if record.first_bot_speech:
                text.append("  greeting", style=STYLE_DIM)
            text.append("\n")

        text.rstrip()
        return text


class LatencyStats(Static):
    """Percentiles and the worst turn, one figure per line."""

    def __init__(self):
        """Create the widget."""
        super().__init__(Text(""), id="latency-stats")
        self.border_title = "Summary"
        self._session: Optional[WorkerSession] = None

    def set_session(self, session: WorkerSession) -> None:
        """Point the widget at a session and re-render."""
        self._session = session
        self.refresh_view()

    def refresh_view(self) -> None:
        """Re-render from the session."""
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        session = self._session
        if session is None or not session.latencies:
            text.append("No turns measured yet.", style=STYLE_DIM)
            return text
        records = session.latencies
        values = [r.latency_secs for r in records if not r.first_bot_speech]
        if not values:
            values = [r.latency_secs for r in records]
        worst = max(records, key=lambda r: r.latency_secs)
        greeting = next((r for r in records if r.first_bot_speech), None)

        def row(label: str, value: str, detail: str = "", style=STYLE_BRIGHT) -> None:
            text.append(f"{label:<10}", style=STYLE_LABEL)
            text.append(value, style=style)
            if detail:
                text.append(f"  {detail}", style=STYLE_DIM)
            text.append("\n")

        row("p50", fmt_secs_fixed(percentile(values, 0.5)))
        row("p95", fmt_secs_fixed(percentile(values, 0.95)))
        row("max", fmt_secs_fixed(max(values)))
        row("min", fmt_secs_fixed(min(values)))
        worst_turn = f"turn {worst.turn}" if worst.turn is not None else "?"
        biggest = _biggest_contribution(worst)
        row("worst", worst_turn, biggest or "", style=STYLE_WARN)
        share = _llm_share(records)
        if share is not None:
            row("llm share", f"{share:.0%}", "of the measured waits", style=STYLE_TEXT)
        if greeting is not None:
            row(
                "greeting",
                fmt_secs_fixed(greeting.latency_secs),
                "client connect → bot",
                style=STYLE_TEXT,
            )
        row("turns", str(len(values)), "measured", style=STYLE_TEXT)
        text.rstrip()
        return text


class LatencyLegend(Static):
    """The color key for the bars, always in view."""

    def __init__(self):
        """Create the widget."""
        super().__init__(Text(""), id="latency-legend")
        self.border_title = "Legend"
        self._session: Optional[WorkerSession] = None

    def set_session(self, session: WorkerSession) -> None:
        """Point the widget at a session and re-render."""
        self._session = session
        self.refresh_view()

    def on_resize(self) -> None:
        """Re-render when the width changes."""
        self.refresh_view()

    def refresh_view(self) -> None:
        """Re-render from the session."""
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        session = self._session
        if session is None or not session.latencies:
            text.append(
                "Latency is measured from the user going silent to the bot starting to speak. "
                "The breakdown per service needs enable_metrics=True in PipelineParams.",
                style=STYLE_DIM,
            )
            return text
        records = session.latencies
        text.append_text(self._legend(self._contributions_seen(records)))
        return text

    @staticmethod
    def _contributions_seen(records: list[LatencyRecord]) -> dict[str, tuple[str, str]]:
        seen: dict[str, tuple[str, str]] = {}
        for record in records:
            for c in record.contributions:
                key = str(c.get("key", ""))
                if key and key not in seen:
                    seen[key] = (str(c.get("label", key)), str(c.get("owner", "")))
        return seen

    @staticmethod
    def _legend(seen: dict[str, tuple[str, str]]) -> Text:
        # One line per kind of wait, in the order they happen in a turn, with
        # the service or setting that owns it.
        text = Text()
        if not seen:
            text.append("■ ", style=Style(color=DIM))
            text.append("total only", style=STYLE_TEXT)
            text.append(
                "  enable_metrics is off, so there is no breakdown per service", style=STYLE_DIM
            )
            return text
        width = max(len(label) for label, _ in seen.values())

        def order(item):
            key = item[0]
            return (
                CONTRIBUTION_ORDER.index(key)
                if key in CONTRIBUTION_ORDER
                else len(CONTRIBUTION_ORDER)
            )

        for key, (label, owner) in sorted(seen.items(), key=order):
            text.append("■ ", style=Style(color=contribution_color(key)))
            text.append(f"{label:<{width}}", style=STYLE_TEXT)
            if owner:
                text.append(f"   {owner}", style=STYLE_DIM)
            text.append("\n")
        text.rstrip()
        return text


def _biggest_contribution(record: LatencyRecord) -> Optional[str]:
    if not record.contributions:
        return None
    biggest = max(record.contributions, key=lambda c: c.get("duration_secs", 0.0))
    return f"{biggest.get('label', biggest.get('key', '?'))} {fmt_secs_fixed(biggest.get('duration_secs', 0.0))}"


def _llm_share(records: list[LatencyRecord]) -> Optional[float]:
    total = 0.0
    llm = 0.0
    for record in records:
        for c in record.contributions:
            secs = float(c.get("duration_secs", 0.0) or 0.0)
            total += secs
            if c.get("key") == "llm_inference":
                llm += secs
    if total <= 0:
        return None
    return llm / total


class SpeakingTimeline(Static):
    """Three rows: raw VAD, the user turn ruling, and bot speech."""

    def __init__(self):
        """Create the widget."""
        super().__init__(Text(""), id="speaking-timeline")
        self.border_title = f"Speaking timeline · last {TIMELINE_SECS:.0f} s"
        self.border_subtitle = (
            f"[{GREEN}]▇ vad[/] raw speech detection   "
            f"[{FROST_CYAN}]━ user[/] turn ruling   "
            f"[{PURPLE}]━ bot[/] speaking   "
            f"[{RED}]╳[/] interruption"
        )
        self._session: Optional[WorkerSession] = None

    def set_session(self, session: WorkerSession) -> None:
        """Point the widget at a session and re-render."""
        self._session = session
        self.refresh_view()

    def on_mount(self) -> None:
        """Keep the window sliding while the app runs."""
        self.set_interval(0.5, self.refresh_view)

    def on_resize(self) -> None:
        """Re-render when the width changes."""
        self.refresh_view()

    def refresh_view(self) -> None:
        """Re-render from the session."""
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        session = self._session
        width = max(self.size.width - 2, 30)
        lane_width = max(width - 6, 20)
        now = time.time()
        start = now - TIMELINE_SECS

        if session is None or not session.speech:
            text.append("No speech events yet.", style=STYLE_DIM)
            return text

        def column(timestamp: float) -> int:
            fraction = (timestamp - start) / TIMELINE_SECS
            return max(0, min(lane_width - 1, int(fraction * lane_width)))

        vad = [" "] * lane_width
        user = ["─"] * lane_width
        bot = ["─"] * lane_width
        vad_styles = [POLAR_1] * lane_width
        user_styles = [POLAR_1] * lane_width
        bot_styles = [POLAR_1] * lane_width
        marks: list[int] = []

        open_vad: Optional[float] = None
        open_user: Optional[float] = None
        open_bot: Optional[float] = None

        def fill(lane, styles, begin: float, end: float, char: str, color: str) -> None:
            a = column(max(begin, start))
            b = column(min(end, now))
            for i in range(a, b + 1):
                lane[i] = char
                styles[i] = color
            if a < lane_width and begin >= start:
                lane[a] = "╾"
            if b < lane_width and end <= now and b > a:
                lane[b] = "╼"

        for event in session.speech:
            if event.timestamp < start - 60:
                continue
            kind = event.kind
            if kind == "user_speech_started":
                open_vad = event.timestamp
            elif kind == "user_speech_stopped":
                begin = event.started_at if event.started_at is not None else open_vad
                if begin is not None:
                    fill(vad, vad_styles, begin, event.timestamp, "▇", GREEN)
                open_vad = None
            elif kind == "user_turn_started":
                open_user = event.timestamp
            elif kind == "user_turn_stopped":
                begin = event.started_at if event.started_at is not None else open_user
                if begin is not None:
                    fill(user, user_styles, begin, event.timestamp, "━", FROST_CYAN)
                open_user = None
            elif kind == "bot_speech_started":
                open_bot = event.timestamp
            elif kind == "bot_speech_stopped":
                begin = event.started_at if event.started_at is not None else open_bot
                if begin is not None:
                    fill(bot, bot_styles, begin, event.timestamp, "━", PURPLE)
                open_bot = None
            elif kind == "interruption":
                marks.append(column(event.timestamp))

        if open_vad is not None:
            fill(vad, vad_styles, open_vad, now, "▇", GREEN)
        if open_user is not None:
            fill(user, user_styles, open_user, now, "━", FROST_CYAN)
        if open_bot is not None:
            fill(bot, bot_styles, open_bot, now, "━", PURPLE)
        for mark in marks:
            if 0 <= mark < lane_width:
                bot[mark] = "╳"
                bot_styles[mark] = RED

        def lane_text(label: str, lane, styles) -> None:
            text.append(f"{label:<5} ", style=STYLE_LABEL)
            for char, color in zip(lane, styles):
                text.append(char, style=Style(color=color))
            text.append("\n")

        lane_text("vad", vad, vad_styles)
        lane_text("user", user, user_styles)
        lane_text("bot", bot, bot_styles)

        ruler = Text("      ")
        for tick in range(0, 4):
            secs = start + TIMELINE_SECS * tick / 3
            position = 6 + int((lane_width - 1) * tick / 3)
            label = fmt_clock(secs)
            if tick == 3:
                position -= len(label) - 1
            while len(ruler) < position:
                ruler.append(" ")
            ruler.append(label, style=STYLE_DIM)
        text.append_text(ruler)
        text.append("\n")

        interruptions = sum(1 for e in session.speech if e.kind == "interruption")
        text.append("interruptions ", style=STYLE_LABEL)
        text.append(str(interruptions), style=STYLE_TEXT)
        gap = _last_endpointing_gap(session)
        if gap is not None:
            text.append("   last vad gap before ruling ", style=STYLE_LABEL)
            text.append(fmt_secs_fixed(gap), style=STYLE_TEXT)
        return text


def _last_endpointing_gap(session: WorkerSession) -> Optional[float]:
    # Time between the raw VAD saying the user stopped and the turn strategy
    # agreeing. This is what stop_secs and smart-turn settings tune.
    last_vad_stop: Optional[float] = None
    gap: Optional[float] = None
    for event in session.speech:
        if event.kind == "user_speech_stopped":
            last_vad_stop = event.timestamp
        elif event.kind == "user_turn_stopped" and last_vad_stop is not None:
            gap = max(event.timestamp - last_vad_stop, 0.0)
            last_vad_stop = None
    return gap


class LatencyView(Vertical):
    """The Latency tab."""

    def __init__(self):
        """Create the tab."""
        super().__init__(id="latency-view")
        self.bars = LatencyBars()
        self.stats = LatencyStats()
        self.legend = LatencyLegend()
        self.timeline = SpeakingTimeline()

    def compose(self):
        """Compose the bars, then summary and legend side by side, then the timeline."""
        yield self.bars
        with Horizontal(id="latency-bottom"):
            yield self.stats
            yield self.legend
        yield self.timeline

    def set_session(self, session: WorkerSession) -> None:
        """Scope every panel to a session."""
        self.bars.set_session(session)
        self.stats.set_session(session)
        self.legend.set_session(session)
        self.timeline.set_session(session)

    def refresh_view(self) -> None:
        """Re-render every panel."""
        self.bars.refresh_view()
        self.stats.refresh_view()
        self.legend.refresh_view()
        self.timeline.refresh_view()
