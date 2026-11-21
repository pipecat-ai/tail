#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The turn inspector: everything about one turn in one place.

Three pages, each one level closer to the raw data:

1. Turn: what was said, where the time went, the tool calls.
2. Context: what the LLM saw for this turn, with what's new since the last
   turn marked: the system instruction, the tools offered and every message,
   images included.
3. Events: every message Tail received for the turn, in order, timed from
   when the user started speaking.
"""

import dataclasses
import json
from typing import Any, Optional

from rich.console import Group
from rich.json import JSON
from rich.padding import Padding
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from pipecat_tail.state import Turn, WorkerSession
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.graphics import image_lines, pack, pad, stacked
from pipecat_tail.widgets.render import fit_hints, fmt_clock, fmt_secs, truncate
from pipecat_tail.widgets.turns import (
    compact_json,
    explain_early_end,
    render_bot_text,
    render_call,
    render_llm_input,
)

PAGES = ("turn", "context", "events")


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        try:
            return JSON.from_data(value, indent=2)
        except (TypeError, ValueError):
            pass
    return Text(str(value))


def turn_as_dict(turn: Turn) -> dict[str, Any]:
    """A turn as plain data, for copying."""

    def convert(value):
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {k: convert(v) for k, v in dataclasses.asdict(value).items()}
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        if isinstance(value, dict):
            return {str(k): convert(v) for k, v in value.items()}
        return value

    data = convert(turn)
    return data if isinstance(data, dict) else {}


def inspect_turn(turn: Turn, width: int, now: float) -> Group:
    """Render the inspector body for a turn."""
    p = palette.current()
    rows: list[Any] = []

    # The header, in whole pieces that wrap between them, never inside one.
    pieces = [
        Text(
            f"turn {turn.number if turn.number is not None else '?'}",
            style=Style(color=p.bright, bold=True),
        )
    ]
    when = Text(fmt_clock(turn.started_at), style=Style(color=p.muted))
    if turn.duration_secs:
        when.append(f" · {turn.duration_secs:.1f} s long", style=Style(color=p.muted))
    pieces.append(when)
    if turn.latency_secs is not None:
        color = palette.latency_color(turn.latency_secs)
        latency = Text(fmt_secs(turn.latency_secs), style=Style(color=color, bold=True))
        latency.append(" to first word", style=Style(color=p.muted))
        pieces.append(latency)
    if turn.interrupted:
        spoken, total = turn.spoken_word_counts()
        pieces.append(
            Text(f"↯ interrupted at {spoken} of {total} words", style=Style(color=p.err, bold=True))
        )
    else:
        pieces.append(Text("not interrupted", style=Style(color=p.ok)))
    rows.extend(pack(pieces, width))
    rows.append(Text(""))

    # The user.
    if turn.user_text or turn.user_interim:
        rows.append(
            _said(
                Text("you", style=Style(color=p.user, bold=True)),
                Text(turn.user_text or turn.user_interim, style=Style(color=p.bright)),
            )
        )
        detail = Text()
        if turn.input_type == "chat":
            detail.append("typed in the client", style=Style(color=p.user))
            detail.append("   ")
        stt = [
            c
            for c in (turn.latency.contributions if turn.latency else [])
            if c.get("key") == "transcription"
        ]
        if stt:
            detail.append("stt ", style=Style(color=p.muted))
            detail.append(str(stt[0].get("owner") or ""), style=Style(color=p.stt))
            detail.append(
                f"  {fmt_secs(stt[0].get('duration_secs'))}   ", style=Style(color=p.muted)
            )
        tone = turn.tone
        if tone:
            detail.append("tone ", style=Style(color=p.muted))
            detail.append(f"{tone[0]} .{int(tone[1] * 100):02d}   ", style=Style(color=p.user))
        for signal in turn.perception:
            if signal.get("value") is not None:
                detail.append(f"{signal.get('name')} ", style=Style(color=p.muted))
                detail.append(f"{signal.get('value'):.2f}   ", style=Style(color=p.user))
        if turn.speaker:
            detail.append("speaker ", style=Style(color=p.muted))
            detail.append(f" {turn.speaker} ", style=Style(color=p.ink, bgcolor=p.user, bold=True))
        if detail.cell_len:
            rows.append(Padding(detail, (0, 0, 0, 5)))
        llm_input = render_llm_input(turn, 300)
        if llm_input:
            rows.append(Padding(llm_input, (0, 0, 0, 5)))
        for early in turn.early_ends:
            rows.append(Padding(explain_early_end(early), (0, 0, 0, 5)))
        if turn.early_ends:
            rows.append(
                Padding(
                    Text(
                        "If this happens often, tune your turn detection: the VAD's "
                        "stop_secs, or your turn analyzer if you use one, such as smart turn.",
                        style=Style(color=p.muted),
                    ),
                    (0, 0, 0, 5),
                )
            )
        rows.append(Text(""))

    # Where the time went.
    record = turn.latency
    if record is not None and record.contributions:
        starts: list[float] = [
            float(c["start_time"])
            for c in record.contributions
            if isinstance(c.get("start_time"), (int, float)) and c.get("start_time")
        ]
        origin = min(starts) if starts else None
        span = max(
            [
                (c.get("start_time", 0) - origin if origin else 0) + (c.get("duration_secs") or 0)
                for c in record.contributions
            ]
            + [record.latency_secs]
        )
        seconds = max(1.0, float(int(span + 0.999)))
        # The bar takes what the stage, its time and, with room, its owner leave.
        label_w = 20 if width >= 70 else 16
        owner_w = 28 if width - label_w - 12 - 28 >= 30 else 0
        bar_w = max(12, min(70, width - label_w - 2 - 11 - owner_w))
        scale = seconds / bar_w
        axis = Text(" " * bar_w)
        for s in range(int(seconds) + 1):
            pos = int(s / scale)
            if pos < bar_w - 3:
                tick = "╵0" if s == 0 else f"╵{s} s"
                axis = axis[:pos] + Text(tick, style=Style(color=p.muted)) + axis[pos + len(tick) :]
        title = Text("  where the time went", style=Style(color=p.muted, bold=True))
        if title.cell_len <= label_w + 1:
            rows.append(pad(title, label_w + 2) + pad(axis, bar_w))
        else:
            # Too narrow to share the line with the axis.
            rows.append(title)
            rows.append(Text(" " * (label_w + 2)) + pad(axis, bar_w))
        sequential = 0.0
        for stage in record.stages:
            key, secs, owner = stage.key, stage.secs, stage.owner
            color = palette.contribution_color(key)
            offset = (stage.start - origin) if (origin and stage.start) else sequential
            sequential = offset + secs
            label = palette.stage_name(key)
            if key == "function_handler" and owner:
                label = f"ƒ {owner}"
            bar = stacked(
                [(secs, color)], scale, bar_w, offset=max(0.0, offset), background=p.surface
            )
            line = pad(
                Text(f"  {truncate(label, label_w - 2)}", style=Style(color=p.dim)), label_w + 2
            )
            line.append_text(pad(bar, bar_w))
            line.append(f" {secs * 1000:>6.0f} ms ", style=Style(color=p.bright, bold=True))
            if owner and owner_w and key != "function_handler":
                line.append(truncate(owner, owner_w), style=Style(color=p.muted))
            rows.append(line)
        rows.append(Text(""))

    # Tool calls.
    for call in turn.function_calls:
        line, status = render_call(call, now)
        title = Text("  ")
        title.append_text(
            Text(
                f"ƒ {call.function_name}",
                style=Style(color=p.err if call.failed else p.tool, bold=True),
            )
        )
        title.append("  ")
        title.append_text(status)
        rows.append(title)
        if call.arguments is not None:
            rows.append(Text("    arguments", style=Style(color=p.muted)))
            rows.append(_indent(_json(call.arguments)))
        if call.error:
            rows.append(Text(f"    error  {call.error}", style=Style(color=p.err)))
        elif call.result is not None:
            rows.append(Text("    result", style=Style(color=p.muted)))
            rows.append(_indent(_json(call.result)))
        rows.append(Text(""))

    # The bot.
    bot = render_bot_text(turn)
    if bot:
        rows.append(_said(Text("bot", style=Style(color=p.bot, bold=True)), bot))
        detail = Text()
        segments = [s for s in turn.segments if s.output_type]
        types = sorted({s.output_type for s in segments if s.output_type})
        if types:
            detail.append("output ", style=Style(color=p.muted))
            detail.append(", ".join(types) + "   ", style=Style(color=p.dim))
        deliveries = sorted({t for s in turn.segments for _, tags in s.deliveries for t in tags})
        if deliveries:
            detail.append("delivery ", style=Style(color=p.muted))
            detail.append(", ".join(deliveries) + "   ", style=Style(color=p.bot))
        if turn.marker and turn.marker.get("kind"):
            detail.append("marker ", style=Style(color=p.muted))
            detail.append(str(turn.marker.get("kind")), style=Style(color=p.ok))
        if detail.cell_len:
            rows.append(Padding(detail, (0, 0, 0, 5)))
        rows.append(Text(""))

    for b in turn.backchannels:
        line = Text()
        line.append(f"{'you' if b.who == 'user' else 'bot'} said ", style=Style(color=p.muted))
        line.append(f"“{b.text}”", style=Style(color=p.dim, italic=True))
        line.append(
            f" at {fmt_clock(b.timestamp)}, a backchannel that didn't take the turn",
            style=Style(color=p.muted),
        )
        rows.append(Padding(line, (0, 0, 0, 5)))

    if turn.context:
        context = turn.context
        line = Text()
        line.append("context sent to the LLM  ", style=Style(color=p.muted, bold=True))
        line.append(f"{context.get('messages', 0)} messages", style=Style(color=p.text))
        roles = context.get("roles") or {}
        if roles:
            line.append(
                " (" + ", ".join(f"{v} {k}" for k, v in roles.items()) + ")",
                style=Style(color=p.muted),
            )
        chars = context.get("characters")
        if chars:
            line.append(f" · {chars:,} characters", style=Style(color=p.muted))
        if context.get("images"):
            line.append(f" · {context['images']} images", style=Style(color=p.user))
        tools = context.get("tools") or []
        if tools:
            line.append(f" · tools: {', '.join(tools[:8])}", style=Style(color=p.tool))
        rows.append(Padding(line, (0, 0, 0, 2)))
        rows.append(
            Padding(
                Text("tab: the whole context, then every event", style=Style(color=p.muted)),
                (0, 0, 0, 2),
            )
        )
    return Group(*rows)


def _said(who: Text, text: Text) -> Table:
    """A line of the conversation, wrapping under its own text."""
    table = Table.grid(padding=(0, 1))
    table.add_column(width=3, no_wrap=True)
    table.add_column()
    table.add_row(who, text)
    return table


ROLE_COLORS = {
    "system": "muted",
    "developer": "muted",
    "user": "user",
    "assistant": "bot",
    "tool": "tool",
    "specific": "dim",
}


def _media_lines(media: list[dict[str, Any]], indent: str) -> list[Text]:
    p = palette.current()
    lines: list[Text] = []
    for m in media:
        kind = m.get("kind")
        if kind == "image" and m.get("rgb"):
            size = ""
            if m.get("source_width"):
                size = f" {m.get('source_width')}×{m.get('source_height')}"
            lines.append(Text(f"{indent}▣ image{size}", style=Style(color=p.user)))
            lines.extend(Text(indent) + line for line in image_lines(m))
        elif kind == "image":
            lines.append(Text(f"{indent}▣ image {m.get('url') or ''}", style=Style(color=p.user)))
        else:
            name = f" {m.get('name')}" if m.get("name") else ""
            lines.append(Text(f"{indent}▣ {kind}{name}", style=Style(color=p.dim)))
    return lines


def _schema_line(schema: dict[str, Any], width: int) -> Text:
    p = palette.current()
    line = Text("  ƒ ", style=Style(color=p.tool, bold=True))
    line.append(str(schema.get("name", "?")), style=Style(color=p.tool, bold=True))
    params = schema.get("parameters") or {}
    required = set(schema.get("required") or [])
    names = [k if k in required else f"{k}?" for k in params] if isinstance(params, dict) else []
    line.append("(" + ", ".join(names) + ")", style=Style(color=p.dim))
    description = schema.get("description")
    if description:
        line.append(
            "  " + truncate(str(description), max(20, width - line.cell_len - 4)),
            style=Style(color=p.muted),
        )
    return line


def context_page(turn: Turn, width: int) -> Group:
    """What the LLM saw for a turn."""
    p = palette.current()
    rows: list[Any] = []
    items = turn.context_items
    if items is None:
        if turn.context:
            rows.append(
                Text(
                    "This bot's Tail observer sends only counts of the context. "
                    "Update pipecat-ai-tail in the bot to see the messages.",
                    style=Style(color=p.muted),
                )
            )
        else:
            rows.append(Text("No LLM context was sent in this turn.", style=Style(color=p.muted)))
        return Group(*rows)
    context = turn.context or {}
    head = Text()
    head.append(str(context.get("llm") or "LLM"), style=Style(color=p.llm, bold=True))
    head.append(f"  {len(items)} messages", style=Style(color=p.bright))
    new_from = turn.context_new_from or 0
    new = len(items) - new_from
    if new > 0:
        head.append(f" · {new} new this turn", style=Style(color=p.accent, bold=True))
    chars = context.get("characters")
    if chars:
        head.append(f" · {chars:,} characters", style=Style(color=p.muted))
    rows.extend([head, Text("")])

    if turn.context_system:
        rows.append(Text("system instruction", style=Style(color=p.muted, bold=True)))
        for line in turn.context_system.splitlines() or [""]:
            rows.append(Text("  " + line, style=Style(color=p.dim)))
        rows.append(Text(""))
    if turn.context_tools:
        rows.append(Text("tools offered", style=Style(color=p.muted, bold=True)))
        for schema in turn.context_tools:
            rows.append(_schema_line(schema, width))
        rows.append(Text(""))

    rows.append(Text("messages", style=Style(color=p.muted, bold=True)))
    names: dict[str, str] = {}
    for item in items:
        for call in item.get("tool_calls") or []:
            if call.get("id"):
                names[str(call["id"])] = str(call.get("name") or "")
    # A grid keeps wrapped text in its column.
    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify="right", no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(ratio=1)
    fold_w = max(30, width - 20)
    for index, item in enumerate(items):
        fresh = index >= new_from
        role = str(item.get("role", "other"))
        color = getattr(p, ROLE_COLORS.get(role, "dim"))
        label = role if role != "specific" else str(item.get("llm") or role)
        body: list[Any] = []
        text = str(item.get("text") or "")
        line = Text()
        if role == "tool" and item.get("tool_call_id"):
            called = names.get(str(item["tool_call_id"]), "")
            line.append(f"→ {called} " if called else "→ ", style=Style(color=p.tool))
        style = Style(color=p.bright if fresh else p.dim)
        # Older messages fold to a line each; this turn's are in full.
        line.append(text if fresh else truncate(" ".join(text.split()), fold_w), style=style)
        if line.plain:
            body.append(line)
        for call in item.get("tool_calls") or []:
            call_line = Text(f"ƒ {call.get('name')}", style=Style(color=p.tool, bold=fresh))
            arguments = str(call.get("arguments") or "")
            call_line.append(
                f"({arguments if fresh else truncate(arguments, fold_w - 4)})",
                style=Style(color=p.dim),
            )
            body.append(call_line)
        body.extend(_media_lines(item.get("media") or [], ""))
        grid.add_row(
            Text(str(index), style=Style(color=p.muted)),
            Text("●" if fresh else " ", style=Style(color=p.accent, bold=True)),
            Text(label, style=Style(color=color, bold=fresh)),
            Group(*body) if body else Text(""),
        )
    rows.append(grid)
    rows.append(Text(""))
    rows.append(Text("● new in this turn", style=Style(color=p.accent)))
    return Group(*rows)


EVENT_FAMILIES = (
    ("user-", "user"),
    ("vad-", "vad"),
    ("bot-llm", "llm"),
    ("llm-", "llm"),
    ("bot-tts", "tts"),
    ("tts-", "tts"),
    ("bot-", "bot"),
    ("tail-function", "tool"),
    ("llm-function", "tool"),
    ("tail-error", "err"),
    ("error", "err"),
)


def _event_color(kind: str) -> str:
    p = palette.current()
    for prefix, role in EVENT_FAMILIES:
        if kind.startswith(prefix):
            return getattr(p, role)
    return p.muted


def turn_window(turn: Turn, session: WorkerSession) -> tuple[float, Optional[float]]:
    """When a turn's events start and end, on the bot's clock.

    A turn opens when Tail first hears of it, which is a little after the
    user starts speaking; the window starts at the speech that led to it.
    """
    turns = [t for t in session.turns if t.opened_at is not None]

    def start_of(t: Turn, previous: Optional[Turn], *, restarted: bool = False) -> float:
        opened = t.opened_at or t.started_at
        if t.input_type == "chat":
            return opened
        floor = previous.opened_at if previous and previous.opened_at else opened - 15.0
        if previous is not None and previous.bot_started_at:
            # Speech before the bot answered the previous turn belongs to it,
            # as when the user went on after the turn ended early.
            floor = max(floor, previous.bot_started_at)
        if restarted and t.restarted_at is not None:
            # The turn's first part, with nothing heard, belongs to neither turn.
            floor, opened = opened, t.restarted_at

        def times(kind: str, lo: float, hi: float) -> list[float]:
            return [ts for ts, k, _ in session.events if k == kind and lo < ts <= hi]

        # The last time the user started speaking before the turn opened,
        # and the VAD start just before it. A typed turn starts when it opens.
        speech = times("user-started-speaking", floor, opened + 0.05) or times(
            "vad-user-started-speaking", floor, opened + 0.05
        )
        if not speech:
            return opened
        anchor = max(speech)
        return min([anchor, *times("vad-user-started-speaking", max(floor, anchor - 1.5), anchor)])

    if turn not in turns:
        return (turn.opened_at or turn.started_at, None)
    index = turns.index(turn)
    start = start_of(turn, turns[index - 1] if index else None, restarted=True)
    end = start_of(turns[index + 1], turn) if index + 1 < len(turns) else None
    return start, end


def events_page(turn: Turn, session: WorkerSession, width: int) -> Group:
    """Every message of a turn, in order."""
    p = palette.current()
    start, end = turn_window(turn, session)
    events = [
        (ts, kind, data)
        for ts, kind, data in session.events
        if ts >= start and (end is None or ts < end)
    ]
    if not events:
        return Group(Text("No events recorded for this turn.", style=Style(color=p.muted)))
    rows: list[Any] = [
        Text(
            f"{len(events)} messages from {fmt_clock(start)}, timed from the first",
            style=Style(color=p.muted),
        ),
        Text(""),
    ]
    data_w = max(20, width - 36)
    for ts, kind, data in events:
        line = Text(f"{ts - start:+8.3f} s  ", style=Style(color=p.muted))
        line.append(pad(Text(kind, style=Style(color=_event_color(kind), bold=True)), 26))
        line.append(compact_json(data, data_w) if data else "", style=Style(color=p.dim))
        rows.append(line)
    return Group(*rows)


def _indent(renderable) -> Any:
    return Padding(renderable, (0, 0, 0, 6))


class TurnInspector(ModalScreen):
    """Everything about one turn, on three pages."""

    DEFAULT_CSS = """
    TurnInspector { align: center middle; background: $background 70%; }
    TurnInspector #inspector {
        width: 90%;
        max-width: 160;
        height: 90%;
        border: round $primary;
        border-title-color: $text;
        border-title-style: bold;
        border-subtitle-color: $text-muted;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape,q,enter", "dismiss", "close"),
        Binding("left,h", "move(-1)", "previous", show=False),
        Binding("right,l", "move(1)", "next", show=False),
        Binding("tab", "page(1)", "next page", show=False, priority=True),
        Binding("shift+tab", "page(-1)", "previous page", show=False, priority=True),
        Binding("y", "copy", "copy as JSON"),
    ]

    def __init__(self, session: WorkerSession, turn: Turn, now):
        """Create the inspector."""
        super().__init__()
        self.session = session
        self.turn = turn
        self.now = now
        self.page = "turn"
        self.body = Static()

    HINTS = ["esc close", "tab page", "←→ turn", "↑↓ scroll", "y copy as JSON"]

    def compose(self) -> ComposeResult:
        """Lay out the panel."""
        panel = VerticalScroll(self.body, id="inspector")
        panel.border_subtitle = " · ".join(self.HINTS)
        yield panel

    def on_resize(self) -> None:
        """Lay the page out for its width, and keep the hints that fit the border whole."""
        panel = self.query_one("#inspector")
        panel.border_subtitle = fit_hints(self.HINTS, max(10, panel.size.width - 6))
        self.call_after_refresh(self.redraw)

    def on_mount(self) -> None:
        """Draw the turn; the arrows scroll it."""
        self.redraw()
        self.query_one("#inspector").focus()
        self.set_interval(0.5, self._tick)

    def _tick(self) -> None:
        # Only the turn page moves with time (running tools, a live turn).
        if self.page == "turn" and not self.turn.ended:
            self.redraw()

    def _title(self) -> str:
        p = palette.current()
        number = self.turn.number if self.turn.number is not None else "?"
        parts = []
        for page in PAGES:
            if page == self.page:
                parts.append(f"[{p.ink} on {p.accent} bold] {page} [/]")
            else:
                parts.append(f"[{p.dim}] {page} [/]")
        return f"⏎ turn {number}  " + "".join(parts)

    def redraw(self) -> None:
        """Draw the current page."""
        panel = self.query_one("#inspector")
        panel.border_title = self._title()
        # What's left inside the border, the padding and a scrollbar.
        width = (panel.size.width or 128) - 8
        if self.page == "context":
            body = context_page(self.turn, width)
        elif self.page == "events":
            body = events_page(self.turn, self.session, width)
        else:
            body = inspect_turn(self.turn, width, self.now())
        self.body.update(body)

    def _turns(self) -> list[Turn]:
        return [t for t in self.session.turns if t.has_content]

    def action_move(self, step: int) -> None:
        """Show the previous or next turn."""
        turns = self._turns()
        if self.turn not in turns:
            return
        index = max(0, min(len(turns) - 1, turns.index(self.turn) + step))
        self.turn = turns[index]
        self.redraw()

    def action_page(self, step: int) -> None:
        """Show the next or previous page."""
        self.page = PAGES[(PAGES.index(self.page) + step) % len(PAGES)]
        self.redraw()
        self.query_one("#inspector", VerticalScroll).scroll_home(animate=False)

    def action_copy(self) -> None:
        """Copy the turn as JSON."""
        data = json.dumps(turn_as_dict(self.turn), indent=2, default=str)
        self.app.copy_to_clipboard(data)
        self.app.notify(f"Copied turn {self.turn.number} as JSON", timeout=2)
