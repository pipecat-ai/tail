#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Latency: where the time goes between the user going silent and the bot speaking.

A waterfall per turn, like a browser's network panel, with every stage in
the same color as everywhere else in the app; the stages' percentiles and
share; the distribution of turns; and the speech lanes that matter when
tuning endpointing.
"""

from typing import Optional

from rich.console import Group
from rich.padding import Padding
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from pipecat_tail.state import LatencyRecord, SessionState, WorkerSession, percentile
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.graphics import cells, meter, overlay, pack, pad, stacked
from pipecat_tail.widgets.render import fit_hints, fmt_secs

NO_DATA = (
    "No turns measured yet. Latency counts from the user going silent to the bot "
    "speaking; the breakdown per stage needs enable_metrics=True in PipelineParams."
)


# Stages Pipecat measures only with metrics on.
MEASURED_STAGES = frozenset(
    {"llm_inference", "llm_tool_call", "speech_synthesis", "transcription", "turn_completion"}
)


def parts_of(record: LatencyRecord) -> list[tuple[float, str, str, str]]:
    """A record's stages as ``(seconds, color, key, owner)``, in order."""
    parts = []
    for c in record.contributions:
        secs = c.get("duration_secs")
        if not isinstance(secs, (int, float)) or secs <= 0:
            continue
        key = str(c.get("key", "service"))
        parts.append((float(secs), palette.contribution_color(key), key, str(c.get("owner") or "")))
    return parts


def stage_name(key: str) -> str:
    """Short name of a stage."""
    return palette.CONTRIBUTION_NAMES.get(key, key.replace("_", " "))


def note_for(record: LatencyRecord) -> Optional[Text]:
    """Explain a slow turn by its biggest stage."""
    p = palette.current()
    parts = parts_of(record)
    if not parts or record.latency_secs < palette.latency_target():
        return None
    secs, color, key, owner = max(parts, key=lambda part: part[0])
    if secs < record.latency_secs * 0.35:
        return None
    text = Text()
    if key == "function_handler":
        text.append(f"ƒ {owner} {fmt_secs(secs)}", style=Style(color=p.tool))
    else:
        label = stage_name(key)
        text.append(f"{label} {fmt_secs(secs)}", style=Style(color=color))
        if owner:
            text.append(f" · {owner}", style=Style(color=p.muted))
    return text


def turn_bar(record: LatencyRecord, scale: float, width: int) -> Text:
    """The stacked bar of one turn."""
    p = palette.current()
    parts = parts_of(record)
    if not parts:
        return stacked([(record.latency_secs, p.border)], scale, width)
    stages = [(secs, color) for secs, color, _, _ in parts]
    if record.first_bot_speech:
        # The greeting also counts the client joining, which no stage covers.
        joining = record.latency_secs - sum(secs for secs, _ in stages)
        if joining > 0:
            stages.insert(0, (joining, p.border))
    return stacked(stages, scale, width)


def scale_for(records: list[LatencyRecord]) -> float:
    """Seconds the full bar width stands for: whole seconds, at least three."""
    values = [r.latency_secs for r in records] or [1.0]
    top = max(3.0, percentile(values, 0.95) or 0.0, min(max(values), 6.0))
    return float(int(top + 0.999))


class Waterfall(Widget):
    """One row per turn, newest at the bottom, with a cursor."""

    DEFAULT_CSS = """
    Waterfall { height: auto; }
    """

    def __init__(self, state: SessionState):
        """Create the waterfall."""
        super().__init__(id="waterfall")
        self.state = state
        self.cursor: Optional[int] = None

    def records(self) -> list[LatencyRecord]:
        """The records drawn, oldest first."""
        return list(self.state.selected.latencies)

    def move(self, step: int) -> Optional[LatencyRecord]:
        """Move the cursor and return the record under it."""
        records = self.records()
        if not records:
            return None
        if self.cursor is None:
            self.cursor = len(records) - 1
        else:
            self.cursor = max(0, min(len(records) - 1, self.cursor + step))
        self.refresh(layout=True)
        return records[self.cursor]

    def selected(self) -> Optional[LatencyRecord]:
        """The record under the cursor, or the last one."""
        records = self.records()
        if not records:
            return None
        if self.cursor is None or self.cursor >= len(records):
            return records[-1]
        return records[self.cursor]

    def get_content_height(self, container, viewport, width: int) -> int:
        """One line per turn plus the axis, a blank line and the legend."""
        records = self.records()
        return max(1, len(records) + 2 + len(self._legend(records, width)))

    def _legend(self, records: list[LatencyRecord], width: int) -> list[Text]:
        p = palette.current()
        keys: list[str] = []
        for record in records:
            for _, _, key, _ in parts_of(record):
                if key not in keys:
                    keys.append(key)
        items = [
            Text("■ ", style=Style(color=palette.contribution_color(key)))
            + Text(stage_name(key), style=Style(color=p.dim))
            for key in keys
        ]
        items.append(Text("┊ p50", style=Style(color=p.muted)))
        return pack(items, width, indent=11 if width >= 60 else 1)

    def render(self):
        """Draw the waterfall."""
        p = palette.current()
        records = self.records()
        if not records:
            return Text(NO_DATA, style=Style(color=p.muted))
        width = self.size.width
        head_w = 11
        tail_w = max(10, min(46, width // 3))
        bar_w = max(10, width - head_w - tail_w - 1)
        seconds = scale_for(records)
        scale = seconds / bar_w
        latencies = [r.latency_secs for r in records if not r.first_bot_speech]
        p50 = percentile(latencies, 0.5)

        axis = Text(" " * width)
        for s in range(int(seconds) + 1):
            pos = head_w + int(s / scale)
            if pos < width - 3:
                axis = overlay(axis, pos, Text(f"╵{s} s", style=Style(color=p.muted)))
        lines = [axis]
        for index, record in enumerate(records):
            selected = index == (self.cursor if self.cursor is not None else -1)
            head = Text(
                f"{'▸' if selected else ' '} turn {record.turn if record.turn is not None else '?':>3} ",
                style=Style(color=p.bright if selected else p.dim, bold=selected),
            )
            bar = turn_bar(record, scale, bar_w)
            if p50 is not None and not record.first_bot_speech:
                mark = int(p50 / scale)
                if 0 <= mark < bar_w and bar.plain[mark] == " ":
                    bar = overlay(bar, mark, Text("┊", style=Style(color=p.muted)))
            color = palette.latency_color(record.latency_secs)
            tail = Text(f" {record.latency_secs:5.2f} s", style=Style(color=color, bold=True))
            room = width - head_w - bar_w
            note = (
                Text("greeting", style=Style(color=p.muted))
                if record.first_bot_speech
                else note_for(record)
            )
            # The note only when it fits whole.
            if note is not None and tail.cell_len + 2 + note.cell_len <= room:
                tail.append("  ")
                tail.append_text(note)
            line = pad(head, head_w) + pad(bar, bar_w) + pad(tail, room)
            if selected:
                line.style = Style(bgcolor=p.raised)
            lines.append(line)
        lines.append(Text(""))
        lines.extend(self._legend(records, width))
        return Group(*lines)


def stage_table(session: WorkerSession, width: int = 120) -> Group | Text:
    """Percentiles and share of the time per stage.

    Columns come in order of importance and only while they fit: the stage
    and its p50, its share, its p95, a bar of the share, then who owns it.
    A number is never cut.
    """
    p = palette.current()
    records = [r for r in session.latencies if not r.first_bot_speech]
    if not records:
        return Text("No turns measured yet.", style=Style(color=p.muted))
    by_key: dict[str, list[float]] = {}
    owners: dict[str, set[str]] = {}
    for record in records:
        for secs, _, key, owner in parts_of(record):
            by_key.setdefault(key, []).append(secs)
            if owner:
                owners.setdefault(key, set()).add(owner)
    total = sum(sum(v) for v in by_key.values()) or 1.0
    rows = []
    for key, values in by_key.items():
        color = palette.contribution_color(key)
        share = sum(values) / total
        p50 = percentile(values, 0.5)
        p95 = percentile(values, 0.95)
        name = Text("■ ", style=Style(color=color))
        name.append(stage_name(key), style=Style(color=p.text))
        rows.append(
            {
                "stage": name,
                "p50": Text(fmt_secs(p50), style=Style(color=p.bright, bold=True)),
                "share": Text(f"{share:.0%}", style=Style(color=p.dim)),
                "p95": Text(
                    fmt_secs(p95),
                    style=Style(
                        color=p.warn if (p95 or 0) >= palette.latency_target() else p.bright
                    ),
                ),
                "bar": meter(share, 12, (color, color), p.grid),
                "set by": Text(
                    ", ".join(sorted(owners.get(key, set()))), style=Style(color=p.muted)
                ),
            }
        )
    if not rows:
        # Turns measured, but none broken down by stage.
        return Text(
            "No breakdown by stage yet. It arrives with enable_metrics=True in PipelineParams, "
            "which the setup file turns on.",
            style=Style(color=p.muted),
        )
    order = ["stage", "p50", "share", "p95", "bar", "set by"]
    widths = {c: max(len(c), *(r[c].cell_len for r in rows)) for c in order}
    widths["set by"] = max(len("set by"), 12)
    columns: list[str] = []
    used = 0
    for column in order:
        need = widths[column] + (2 if columns else 0)
        if columns and used + need > width:
            break
        columns.append(column)
        used += need
    shown = [c for c in order if c in columns]

    def line(row: dict[str, Text]) -> Text:
        parts = []
        for column in shown:
            last = column == "set by"
            justify = "right" if column in ("p50", "p95", "share") else "left"
            parts.append((row[column], 0 if last else widths[column], justify))
        out = cells(parts)
        if out.cell_len > width:
            out.truncate(width, overflow="ellipsis")
        return out

    header = {c: Text("" if c == "bar" else c, style=Style(color=p.muted)) for c in shown}
    return Group(line(header), *(line(row) for row in rows))


def histogram(session: WorkerSession, width: int, height: int = 5) -> Group | Text:
    """How the turns' latencies are spread, in 200 ms bins."""
    p = palette.current()
    values = [r.latency_secs for r in session.latencies if not r.first_bot_speech]
    if not values:
        return Text("No turns measured yet.", style=Style(color=p.muted))
    bins_n = max(6, min(16, (width - 4) // 4))
    step = 0.2
    bins = [0] * bins_n
    for v in values:
        bins[min(bins_n - 1, int(v / step))] += 1
    top = max(bins) or 1
    rows = []
    for r in range(height, 0, -1):
        line = Text(" ")
        for i, count in enumerate(bins):
            h = count / top * height
            color = palette.latency_color(i * step + 0.001)
            if h >= r:
                cell = "███"
            elif h >= r - 0.5 and count:
                cell = "▄▄▄"
            else:
                cell = "   "
            line.append(cell + " ", style=Style(color=color))
        rows.append(line)
    labels = Text(" ")
    for i in range(bins_n):
        labels.append(f"{i * step:<4.1f}"[:4], style=Style(color=p.muted))
    rows.append(labels)
    target = palette.latency_target()
    under = sum(1 for v in values if v < target) / len(values)
    summary = Text(" p50 ", style=Style(color=p.muted))
    p50 = percentile(values, 0.5)
    p95 = percentile(values, 0.95)
    summary.append(fmt_secs(p50), style=Style(color=palette.latency_color(p50), bold=True))
    summary.append("   p95 ", style=Style(color=p.muted))
    summary.append(fmt_secs(p95), style=Style(color=palette.latency_color(p95), bold=True))
    summary.append(f"   under {fmt_secs(target)} ", style=Style(color=p.muted))
    summary.append(f"{under:.0%}", style=Style(color=p.ok if under >= 0.8 else p.warn, bold=True))
    summary.append(f"   {len(values)} turns", style=Style(color=p.muted))
    rows.append(summary)
    return Group(*rows)


def speech_lanes(
    state: SessionState, session: WorkerSession, width: int, window: float = 30.0
) -> Group:
    """Raw VAD, the turn ruling and the bot's speech over the last seconds."""
    p = palette.current()
    label_w = 8
    cells = max(10, width - label_w)
    now = state.now()
    t0 = now - window

    def x(t: float) -> int:
        return max(0, min(cells, int((t - t0) / window * cells)))

    def lane(start_kind: str, stop_kind: str, char: str, color: str) -> Text:
        line = Text(" " * cells)
        open_at: Optional[float] = None
        spans = []
        for event in session.speech:
            if event.kind == start_kind:
                open_at = event.timestamp
            elif event.kind == stop_kind:
                spans.append((open_at if open_at is not None else event.timestamp, event.timestamp))
                open_at = None
        if open_at is not None:
            spans.append((open_at, now))
        for a, b in spans:
            if b < t0:
                continue
            xa, xb = x(a), max(x(a) + 1, x(b))
            line = overlay(line, xa, Text(char * (xb - xa), style=Style(color=color)))
        return line

    vad = lane("user_speech_started", "user_speech_stopped", "▆", p.vad)
    turn = lane("user_turn_started", "user_turn_stopped", "━", p.user)
    bot = lane("bot_speech_started", "bot_speech_stopped", "━", p.bot)
    for event in session.speech:
        if event.kind == "interruption" and event.timestamp >= t0:
            bot = overlay(bot, x(event.timestamp), Text("↯", style=Style(color=p.err, bold=True)))
    axis = Text(" " * cells)
    for s in range(int(window), 0, -5):
        axis = overlay(axis, x(now - s), Text(f"╵-{s}s", style=Style(color=p.muted)))
    axis = overlay(axis, cells - 4, Text("now╵", style=Style(color=p.accent, bold=True)))

    def label(text: str, color: str) -> Text:
        return pad(Text(text, style=Style(color=color, bold=True)), label_w)

    # How long after the user went silent the turn ended. With the VAD's own
    # timing that counts from the silence; without it, from the VAD saying so.
    gaps = []
    last_vad_stop = None
    from_silence = False
    for event in session.speech:
        if event.kind == "user_speech_stopped":
            last_vad_stop = event.started_at or event.timestamp
            from_silence = event.started_at is not None
        elif event.kind == "user_turn_stopped" and last_vad_stop is not None:
            # A turn with nothing heard ends on Pipecat's stop timeout instead.
            if event.heard:
                gaps.append(event.timestamp - last_vad_stop)
            last_vad_stop = None
    note = Text()
    if gaps:
        note.append("Your turn ended ", style=Style(color=p.muted))
        note.append(fmt_secs(gaps[-1]), style=Style(color=p.bright, bold=True))
        note.append(
            " after you went silent" if from_silence else " after the VAD heard silence",
            style=Style(color=p.muted),
        )
        note.append(" (median ", style=Style(color=p.muted))
        note.append(fmt_secs(percentile(gaps, 0.5)), style=Style(color=p.bright))
        note.append(
            "): the VAD's stop_secs, then any turn analyzer."
            if from_silence
            else "): any turn analyzer and the transcription, after the VAD's stop_secs.",
            style=Style(color=p.muted),
        )
    else:
        note.append(
            "Raw VAD next to the end of each turn: a pause shorter than stop_secs stays "
            "in the turn.",
            style=Style(color=p.muted),
        )
    return Group(
        label("vad", p.vad) + vad,
        label("turn", p.user) + turn,
        label("bot", p.bot) + bot,
        label("", p.muted) + axis,
        Padding(note, (0, 0, 0, label_w)),
    )


class StageStats(Static):
    """The stage table in a panel."""


class LatencyView(Vertical):
    """The Latency view."""

    DEFAULT_CSS = """
    LatencyView { height: 1fr; }
    LatencyView #waterfall-panel {
        height: 1fr;
        border: round $border;
        border-title-color: $text-muted;
        border-title-style: bold;
        border-subtitle-color: $text-muted;
        padding: 0 1;
    }
    LatencyView #latency-bottom { height: 12; }
    LatencyView #stage-stats, LatencyView #histogram {
        width: 1fr;
        height: 100%;
        border: round $border;
        border-title-color: $text-muted;
        border-title-style: bold;
        padding: 0 1;
    }
    LatencyView #speech-lanes {
        height: 8;
        border: round $border;
        border-title-color: $text-muted;
        border-title-style: bold;
        padding: 0 1;
    }
    """

    def __init__(self, state: SessionState):
        """Create the view."""
        super().__init__(id="latency-view")
        self.state = state
        self.waterfall = Waterfall(state)
        self.stats = StageStats(id="stage-stats")
        self.histogram = Static(id="histogram")
        self.lanes = Static(id="speech-lanes")

    def compose(self):
        """Lay out the waterfall over the stats, the histogram and the lanes."""
        panel = VerticalScroll(self.waterfall, id="waterfall-panel")
        # Arrow keys move the turn cursor, not the scroll.
        panel.can_focus = False
        panel.border_title = "turns · voice-to-voice"
        yield panel
        with Horizontal(id="latency-bottom"):
            yield self.stats
            yield self.histogram
        yield self.lanes

    def on_mount(self) -> None:
        """Title the panels."""
        self.stats.border_title = "where the time goes"
        self.histogram.border_title = "distribution"
        self.lanes.border_title = "speech · last 30 s"

    def refresh_view(self) -> None:
        """Redraw from the selected session."""
        session = self.state.selected
        self.waterfall.refresh(layout=True)
        self.stats.update(stage_table(session, max(20, (self.stats.size.width or 122) - 4)))
        self.histogram.update(histogram(session, self.histogram.size.width or 60))
        self.refresh_lanes()

    def on_resize(self) -> None:
        """Fit the tables to the new width."""
        self.call_after_refresh(self.refresh_view)

    def refresh_lanes(self) -> None:
        """Redraw the speech lanes, which move with time."""
        session = self.state.selected
        self.lanes.update(speech_lanes(self.state, session, max(20, self.lanes.size.width - 2)))
