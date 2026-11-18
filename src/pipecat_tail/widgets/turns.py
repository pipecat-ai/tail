#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Turns of the conversation, as the Live view shows them.

Each turn reads as a block: what the user said or typed, with tone and
speaker when the STT reads them; tool calls; what the bot said, bright where
spoken and dim where not yet, with delivery tags where the bot changes how it
speaks; backchannels; interruptions and where they cut the bot off.

The view follows the latest turn. To look back, ``n`` and ``p`` step through
the turns where something went wrong, and a search steps through the turns
that mention something.
"""

import json
import time
from typing import Any, Callable, Optional

from rich.console import Group
from rich.rule import Rule
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual import events
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Static

from pipecat_tail.state import (
    THINKING,
    EarlyEnd,
    ErrorRecord,
    FunctionCall,
    Segment,
    Turn,
    WorkerSession,
)
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.graphics import image_lines, pad, spinner
from pipecat_tail.widgets.render import fmt_secs, truncate


def compact_json(value: Any, limit: int = 160) -> str:
    """One-line JSON, cut to ``limit`` characters."""
    if value is None:
        return ""
    if isinstance(value, str):
        return truncate(value, limit)
    try:
        return truncate(json.dumps(value, separators=(", ", ": "), default=str), limit)
    except (TypeError, ValueError):
        return truncate(str(value), limit)


def call_arguments(value: Any, limit: int = 120) -> Text:
    """Arguments as ``name="value"`` pairs."""
    p = palette.current()
    text = Text()
    if isinstance(value, dict):
        for i, (k, v) in enumerate(value.items()):
            if i:
                text.append(", ", style=Style(color=p.muted))
            text.append(str(k), style=Style(color=p.muted))
            text.append("=", style=Style(color=p.muted))
            rendered = json.dumps(v, default=str) if not isinstance(v, str) else f'"{v}"'
            text.append(truncate(rendered, 60), style=Style(color=p.dim))
            if text.cell_len > limit:
                text.truncate(limit, overflow="ellipsis")
                break
    elif value is not None:
        text.append(compact_json(value, limit), style=Style(color=p.dim))
    return text


def render_call(call: FunctionCall, now: float) -> tuple[Text, Text]:
    """A tool call as a line and its status."""
    p = palette.current()
    line = Text()
    color = p.err if call.failed else p.tool
    line.append("ƒ ", style=Style(color=color, bold=True))
    line.append(call.function_name, style=Style(color=color, bold=True))
    line.append("(", style=Style(color=p.muted))
    line.append_text(call_arguments(call.arguments))
    line.append(")", style=Style(color=p.muted))
    if call.settled and call.error:
        line.append("  → ", style=Style(color=p.muted))
        line.append(truncate(call.error, 100), style=Style(color=p.err))
    elif call.settled and call.result is not None:
        line.append("  → ", style=Style(color=p.muted))
        line.append(compact_json(call.result, 64), style=Style(color=p.dim))

    status = Text()
    if call.settled:
        if call.kind == "function_call_cancelled":
            status.append("⊘ cancelled", style=Style(color=p.dim))
        elif call.failed:
            label = "timed out" if call.kind == "function_call_timed_out" else "failed"
            status.append(f"✕ {label}", style=Style(color=p.err, bold=True))
        else:
            status.append("✓ ", style=Style(color=p.ok, bold=True))
        duration = call.duration_secs
        if duration is not None:
            status.append(f" {fmt_secs(duration)}", style=Style(color=p.tool))
    else:
        start = call.in_progress_at or call.started_at
        status.append(spinner(time.time()) + " ", style=Style(color=p.tool, bold=True))
        if start:
            status.append(fmt_secs(max(0.0, now - start)), style=Style(color=p.tool))
        else:
            status.append("running", style=Style(color=p.tool))
    return line, status


def _segment_text(
    segment: Segment, unspoken_style: Style, chips: bool, last: list[tuple[str, ...]]
) -> Text:
    p = palette.current()
    spoken_style = Style(color=p.bright, bold=True)
    text = Text()
    spoken = segment.spoken_text
    unspoken = segment.unspoken_text
    if segment.spoken_status == "in-progress":
        if segment.text and segment.text.startswith(spoken):
            full = segment.text
        else:
            joiner = (
                ""
                if not spoken or not unspoken or spoken[-1].isspace() or unspoken[0].isspace()
                else " "
            )
            full = spoken + joiner + unspoken
        cut = len(spoken)
    else:
        full = segment.text
        cut = len(full) if spoken else 0

    marks = dict(segment.deliveries) if chips else {}
    for i, ch in enumerate(full):
        tags = marks.get(i)
        if tags is not None:
            # A chip where the delivery changes, not where it carries on.
            if tags and tags != last[0]:
                if i and not text.plain.endswith(" "):
                    text.append(" ")
                text.append(f"({' '.join(tags)})", style=Style(color=p.bot, italic=True))
                if ch != " ":
                    text.append(" ")
            last[0] = tags
        text.append(ch, style=spoken_style if i < cut else unspoken_style)
    return text


def render_bot_text(turn: Turn, chips: bool = True) -> Text:
    """What the bot said and is going to say in a turn."""
    p = palette.current()
    unspoken_style = Style(color=p.muted, strike=True) if turn.interrupted else Style(color=p.dim)
    text = Text()
    last: list[tuple[str, ...]] = [()]
    for segment in turn.segments:
        if segment.output_type == "backchannel":
            continue
        piece = _segment_text(segment, unspoken_style, chips, last)
        if not segment.will_be_spoken:
            piece.stylize(Style(color=p.text, bold=False, italic=True))
        if piece:
            if text:
                text.append(" ")
            text.append_text(piece)
    pending = turn.pending_text.strip()
    if pending:
        if text:
            text.append(" ")
        text.append(pending, style=unspoken_style)
    if not text and turn.tts_text:
        text.append(turn.tts_text, style=Style(color=p.bright, bold=True))
    return text


def render_llm_input(turn: Turn, limit: int = 200) -> Optional[Text]:
    """What the LLM got from the user, when it isn't simply what was heard.

    One message that differs from the transcription is quoted. Several are
    quoted in order: Pipecat ended the user's turn at a pause and the user
    went on, so the LLM got what they said as separate messages.
    """
    p = palette.current()
    parts = turn.user_llm_parts
    if not parts:
        return None
    muted = Style(color=p.muted)
    if len(parts) > 1:
        line = Text(f"llm got it as {len(parts)} messages: ", style=muted)
    else:
        line = Text("llm got ", style=muted)
    for i, part in enumerate(parts):
        if i:
            line.append(" + ", style=muted)
        line.append(f"“{truncate(part, max(20, limit // len(parts)))}”", style=muted)
    return line


def explain_early_end(early: EarlyEnd) -> Text:
    """What happened when Pipecat ended a turn before the user had finished."""
    p = palette.current()
    text = Text()
    text.append("▲ ", style=Style(color=p.warn, bold=True))
    text.append("Pipecat ended your turn after ", style=Style(color=p.text))
    text.append(
        f"“{truncate(early.heard, 80)}”" if early.heard else "a pause", style=Style(color=p.bright)
    )
    if early.llm_started:
        text.append(" and the LLM started on it", style=Style(color=p.text))
    text.append(". ", style=Style(color=p.text))
    if early.gap_secs < 0.05:
        text.append("You were already speaking again", style=Style(color=p.text))
    else:
        text.append("You went on ", style=Style(color=p.text))
        text.append(fmt_secs(early.gap_secs), style=Style(color=p.bright, bold=True))
        text.append(" later", style=Style(color=p.text))
    text.append(
        ", so what you said next reached the LLM as another message.", style=Style(color=p.text)
    )
    return text


def render_early_end(turn: Turn) -> Text:
    """The flag on a turn that Pipecat ended before the user had finished."""
    p = palette.current()
    count = len(turn.early_ends)
    label = "▲ early end" if count == 1 else f"▲ {count} early ends"
    return Text(label, style=Style(color=p.warn, bold=True))


def _latency(seconds: float) -> str:
    return f"{seconds:.2f} s" if seconds >= 1 else f"{seconds * 1000:.0f} ms"


def turn_flags(turn: Turn) -> Text:
    """What a turn's header says about it: what went wrong, then how long it took.

    An answer on target shows only its time; a slow one says so.
    """
    p = palette.current()
    muted = Style(color=p.muted)
    items: list[Text] = []
    if turn.early_ends:
        items.append(render_early_end(turn))
    if turn.interrupted:
        items.append(Text("↯ cut", style=Style(color=p.err, bold=True)))
    if turn.latency_secs is not None:
        secs = turn.latency_secs
        color = palette.latency_color(secs)
        greeting = bool(turn.latency and turn.latency.first_bot_speech)
        item = Text("greeting ", style=muted) if greeting else Text()
        item.append(_latency(secs), style=Style(color=color, bold=True))
        grade = palette.latency_grade(secs)
        if not greeting and grade in ("slow", "too slow"):
            item.append(f" {'▲' if grade == 'slow' else '✕'} {grade}", style=Style(color=color))
        items.append(item)
    return Text(" · ", style=muted).join(items)


MARKER_GLYPHS = {"short": "◔", "long": "◑"}


def turn_issues(turn: Turn) -> list[Text]:
    """What went wrong in a turn, labelled as its header and notes label it.

    An early end, an interruption, a slow answer and a turn the LLM marked
    incomplete. Errors belong to the session, so the view adds those.
    """
    p = palette.current()
    issues: list[Text] = []
    if turn.early_ends:
        issues.append(render_early_end(turn))
    if turn.interrupted:
        issues.append(Text("↯ cut", style=Style(color=p.err, bold=True)))
    greeting = bool(turn.latency and turn.latency.first_bot_speech)
    if turn.latency_secs is not None and not greeting:
        grade = palette.latency_grade(turn.latency_secs)
        if grade in ("slow", "too slow"):
            mark = "▲" if grade == "slow" else "✕"
            color = palette.latency_color(turn.latency_secs)
            issues.append(Text(f"{mark} {grade}", style=Style(color=color, bold=True)))
    kind = str((turn.marker or {}).get("kind") or "")
    if kind and kind != "complete":
        glyph = MARKER_GLYPHS.get(kind, "•")
        issues.append(Text(f"{glyph} turn {kind}", style=Style(color=p.warn)))
    return issues


def turn_text(turn: Turn) -> str:
    """Everything said in a turn, as plain text to search."""
    parts = [
        turn.user_text or "",
        turn.user_interim or "",
        render_bot_text(turn, chips=False).plain,
    ]
    for call in turn.function_calls:
        parts.append(call.function_name)
        parts.append(compact_json(call.arguments, 2000))
        parts.append(compact_json(call.result, 2000))
    return "\n".join(parts)


def _highlight(text: Text, query: Optional[str]) -> Text:
    if query:
        p = palette.current()
        text.highlight_words([query], Style(color=p.ink, bgcolor=p.accent), case_sensitive=False)
    return text


def turn_header(turn: Turn, *, selected: bool = False) -> Table:
    """The rule that opens a turn: its number on the left, its flags on the right."""
    p = palette.current()
    rule = Style(color=p.accent if selected else p.border)
    grid = Table.grid(expand=True)
    grid.add_column(no_wrap=True)
    grid.add_column(ratio=1, no_wrap=True)
    grid.add_column(no_wrap=True)
    left = Text("─ ", style=rule)
    left.append(
        f"{turn.number}" if turn.number is not None else "·",
        style=Style(color=p.bright, bold=True),
    )
    left.append(" ")
    right = Text()
    flags = turn_flags(turn)
    if flags:
        right.append(" ")
        right.append_text(flags)
        right.append(" ")
    right.append("─", style=rule)
    grid.add_row(
        left,
        Rule(style=rule, characters="─"),
        right,
        style=Style(bgcolor=p.sel) if selected else None,
    )
    return grid


def _collapsed_turn(turn: Turn, selected: bool, width: int, highlight: Optional[str]) -> Group:
    """A past turn as its header and a line each for the user and the bot."""
    p = palette.current()
    table = Table.grid(expand=True)
    table.add_column(width=4, no_wrap=True)
    table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")

    def line(text: Text) -> Text:
        return _highlight(
            Text(" ".join(text.plain.split()), style=Style(color=p.bright)), highlight
        )

    if turn.user_text:
        table.add_row(Text("you", style=Style(color=p.user, bold=True)), line(Text(turn.user_text)))
    bot = render_bot_text(turn, chips=False)
    if not bot and turn.function_calls:
        bot = Text(f"ƒ {turn.function_calls[-1].function_name}", style=Style(color=p.tool))
    elif bot and turn.function_calls:
        bot = Text("ƒ ", style=Style(color=p.tool)) + bot
    if bot:
        bot = bot.copy()
        bot = line(bot) if "\n" in bot.plain else _highlight(bot, highlight)
        table.add_row(Text("bot", style=Style(color=p.bot, bold=True)), bot)
    return Group(turn_header(turn, selected=selected), table)


def turn_images(turn: Turn) -> list[dict[str, Any]]:
    """Images the user sent the LLM in this turn, as the observer's thumbnails."""
    items = (turn.context_items or [])[turn.context_new_from or 0 :]
    return [
        media
        for item in items
        if item.get("role") == "user"
        for media in item.get("media") or []
        if media.get("kind") == "image" and media.get("rgb")
    ]


COMPACT_TURN_WIDTH = 72
"""Below this width a turn's side notes go under what was said, not beside it."""


def turn_table(
    turn: Turn,
    session: Optional[WorkerSession],
    now: float,
    *,
    selected: bool = False,
    width: int = 0,
    collapsed: bool = False,
    highlight: Optional[str] = None,
) -> Group:
    """Render a turn: its header, then who said what with side notes.

    Args:
        turn: The turn.
        session: Its session, to tell whether it is the current turn.
        now: The time on the bot's clock.
        selected: Whether the turn is selected.
        width: The width it is drawn in, when known. Below
            ``COMPACT_TURN_WIDTH`` side notes go on their own lines, so what
            was said keeps the room.
        collapsed: Draw one line for the user and one for the bot, for a past
            turn in a narrow pane. The inspector has the rest.
        highlight: Text to mark wherever it was said, for a search.
    """
    p = palette.current()
    compact = 0 < width < COMPACT_TURN_WIDTH
    if compact and collapsed:
        return _collapsed_turn(turn, selected, width, highlight)
    table = Table.grid(expand=True, padding=(0, 0))
    table.add_column(width=4 if compact else 5, no_wrap=True)
    table.add_column(ratio=1)
    if not compact:
        table.add_column(justify="right", no_wrap=True)
    rows = [0]

    def row(who: str, what: Text, side: Optional[Text] = None) -> None:
        if who == "you":
            label = Text("you", style=Style(color=p.user, bold=True))
        elif who == "bot":
            label = Text("bot", style=Style(color=p.bot, bold=True))
        else:
            label = Text(who, style=Style(color=p.muted))
        rows[0] += 1
        _highlight(what, highlight)
        if compact:
            # Notes go on their own line under what was said.
            if what.plain.strip() or not side:
                table.add_row(label, what)
                label = Text("")
            if side:
                table.add_row(label, side)
            return
        table.add_row(label, what, Text("  ") + side if side else Text(""))

    # The user.
    if turn.user_text or turn.user_interim:
        what = Text()
        if turn.user_text:
            what.append(turn.user_text, style=Style(color=p.bright))
        if turn.user_interim:
            if turn.user_text:
                what.append(" ")
            what.append(turn.user_interim, style=Style(color=p.dim, italic=True))
            what.append("▌", style=Style(color=p.user))
        notes: list[Text] = []
        if turn.input_type == "chat":
            notes.append(Text("typed", style=Style(color=p.dim)))
        if turn.speaker:
            notes.append(Text(turn.speaker, style=Style(color=p.user, bold=True)))
        tone = turn.tone
        if tone:
            note = Text("tone ", style=Style(color=p.muted))
            note.append(tone[0], style=Style(color=p.user))
            note.append(f" .{int(tone[1] * 100):02d}", style=Style(color=p.muted))
            notes.append(note)
        images = turn_images(turn)
        if images:
            notes.append(
                Text(
                    "▣ image" if len(images) == 1 else f"▣ {len(images)} images",
                    style=Style(color=p.dim),
                )
            )
        row("you", what, Text(" · ", style=Style(color=p.muted)).join(notes) or None)
        for media in images[:2]:
            # A small thumbnail of what the user showed the bot.
            for line in image_lines(media, max_cols=14):
                row("", line)
        llm_input = render_llm_input(turn)
        if llm_input:
            # ⏎ opens the selected turn, or the latest one when none is selected.
            current = session is not None and session.current_turn is turn
            why = None
            if turn.early_ends and (selected or current):
                why = Text()
                why.append(" ⏎ ", style=Style(color=p.ink, bgcolor=p.dim, bold=True))
                why.append(" why", style=Style(color=p.dim))
            row("", llm_input, why)

    # Tool calls.
    for call in turn.function_calls:
        line, status = render_call(call, now)
        line.no_wrap = True
        line.overflow = "ellipsis"
        row("bot", line, status)

    # What the bot said.
    bot_text = render_bot_text(turn)
    is_current = session is not None and session.current_turn is turn
    thinking = session is not None and is_current and session.bot_state_at(now) == THINKING
    if bot_text:
        row("bot", bot_text)
    elif thinking and session is not None:
        # The HUD's clock counts the wait; here it is only named.
        what = Text("thinking", style=Style(color=p.llm))
        if turn.context:
            what.append(
                f"  ·  context {turn.context.get('messages', 0)} msgs",
                style=Style(color=p.muted),
            )
        row("bot", what)
    elif turn.has_bot_content and not turn.function_calls:
        row("bot", Text("…", style=Style(color=p.muted)))

    if turn.interrupted:
        spoken, total = turn.spoken_word_counts()
        side = Text("↯ ", style=Style(color=p.err, bold=True))
        side.append(
            f"cut at {spoken} of {total} words" if total else "interrupted",
            style=Style(color=p.err),
        )
        row("", Text(""), side)

    for b in turn.backchannels:
        who = "you" if b.who == "user" else "bot"
        row(
            who,
            Text(b.text, style=Style(color=p.dim, italic=True)),
            Text("backchannel", style=Style(color=p.muted)),
        )

    # A completed turn is the normal case; only an incomplete one is marked.
    kind = str((turn.marker or {}).get("kind") or "")
    if kind and kind != "complete":
        glyph = MARKER_GLYPHS.get(kind, "•")
        row("", Text(""), Text(f"{glyph} turn {kind}", style=Style(color=p.warn)))

    if not rows[0]:
        if is_current and session is not None and session.user_speaking:
            what = Text("▌", style=Style(color=p.user, bold=True))
            what.append(" listening", style=Style(color=p.user, italic=True))
            row("you", what)
        else:
            row("", Text("…", style=Style(color=p.muted)))
    return Group(turn_header(turn, selected=selected), table)


class TurnBlock(Static):
    """One turn in a conversation list."""

    DEFAULT_CSS = """
    TurnBlock { margin-bottom: 0; }
    """

    class Picked(Message):
        """A turn was clicked."""

        def __init__(self, turn: Turn):
            """Create the message."""
            super().__init__()
            self.turn = turn

    def __init__(self, turn: Turn, view: "ConversationView"):
        """Create the block."""
        super().__init__()
        self.turn = turn
        self.view = view
        self._width = 0

    def redraw(self) -> None:
        """Render the turn again."""
        session = self.view.session
        now = self.view.now()
        selected = self.turn is self.view.selected_turn
        current = session is not None and session.current_turn is self.turn
        self._width = self.size.width
        self.update(
            turn_table(
                self.turn,
                session,
                now,
                selected=selected,
                width=self._width,
                collapsed=self.view.collapse_past and not selected and not current,
                highlight=self.view.search_text,
            )
        )
        self.view.keep_at_end()

    def on_resize(self) -> None:
        """Lay the turn out again for its new width."""
        if self.size.width != self._width:
            self.redraw()

    def on_click(self, event: events.Click) -> None:
        """Select the turn, and inspect it on a double click."""
        self.view.select(self.turn)
        if event.chain >= 2:
            self.post_message(self.Picked(self.turn))


class ConversationView(VerticalScroll):
    """Scrollable list of turns for the selected worker, following the latest.

    Following reads like a page: the text stays put while it fills the view,
    and when it reaches the bottom the view jumps so the latest line is in
    the middle, as Emacs recenters. Below the last turn sits half a view of
    blank space for it to jump into.

    Selecting an earlier turn, or jumping to an issue or a search match,
    pauses following; the title says where you are.
    """

    DEFAULT_CSS = """
    ConversationView {
        border: round $border;
        border-title-color: $text-muted;
        border-title-style: bold;
        border-subtitle-color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        id: str,
        max_turns: Optional[int] = None,
        now=time.time,
        collapse_past: bool = False,
        errors: Optional[Callable[[], list[ErrorRecord]]] = None,
    ):
        """Create the view.

        Args:
            id: Widget id.
            max_turns: Show only the most recent turns.
            now: Clock on the bot's time.
            collapse_past: In a narrow pane, draw past turns on two lines so
                more of them fit.
            errors: The session's errors, so a turn an error happened in
                counts as an issue.
        """
        super().__init__(id=id)
        self.session: Optional[WorkerSession] = None
        self.max_turns = max_turns
        self.collapse_past = collapse_past
        self.following = True
        self.selected_turn: Optional[Turn] = None
        self.now = now
        self.errors = errors or (lambda: [])
        self.search_text: Optional[str] = None
        self._blocks: dict[int, TurnBlock] = {}
        self._end_pending = False
        self._room = Static("", classes="room")
        self.border_title = "conversation"
        self.update_legend()

    def update_legend(self) -> None:
        """Title the panel with where you are: following is normal, so it says nothing.

        A jump says which issue or match of how many and what it is, a search
        how many turns match, and a pause that it's paused.
        """
        parts = ["conversation"]
        position = None if self.following else self._position_of(self.selected_turn)
        if position:
            parts.append(position)
        elif self.search_text:
            count = len(self.matches())
            parts.append(f"“{self.search_text}” {count} turn{'s' if count != 1 else ''}")
        if not self.following and not position:
            parts.append("paused")
        self.border_title = " · ".join(parts)

    def _position_of(self, turn: Optional[Turn]) -> Optional[str]:
        """Which search match or issue a turn is, as ``issue 2 of 5 · ▲ slow``."""
        found, what = self._found()
        for index, (candidate, label) in enumerate(found):
            if candidate is turn:
                return f"{what} {index + 1} of {len(found)}" + (f" · {label}" if label else "")
        return None

    def _found(self) -> tuple[list[tuple[Turn, str]], str]:
        """The turns to step through: the search's matches, or else the issues."""
        if self.search_text:
            return [(turn, "") for turn in self.matches()], f"“{self.search_text}”"
        return self.issues(), "issue"

    def compose(self):
        """Start with the blank space the turns go above."""
        yield self._room

    def _visible_turns(self, session: WorkerSession) -> list[Turn]:
        turns = [t for t in session.turns if t.has_content or t is session.current_turn]
        if self.max_turns:
            turns = turns[-self.max_turns :]
        return turns

    def sync(self, session: WorkerSession, *, full: bool = False) -> None:
        """Bring the blocks in line with the session's turns."""
        if session is not self.session:
            self.session = session
            self.selected_turn = None
            for block in self._blocks.values():
                block.remove()
            self._blocks.clear()
        turns = self._visible_turns(session)
        alive = {id(t) for t in turns}
        for key in list(self._blocks):
            if key not in alive:
                self._blocks.pop(key).remove()
        new_blocks = []
        for turn in turns:
            if id(turn) not in self._blocks:
                block = TurnBlock(turn, self)
                self._blocks[id(turn)] = block
                new_blocks.append(block)
        if new_blocks:
            self.mount(*new_blocks, before=self._room)
        changed = turns if full else turns[-3:]
        for turn in changed:
            block = self._blocks.get(id(turn))
            if block is not None:
                block.redraw()
        for block in new_blocks:
            block.redraw()
        if self.search_text or not self.following:
            self.update_legend()
        self.keep_at_end()

    def keep_at_end(self) -> None:
        """While following, keep the latest line in view once the turns are laid out.

        Every redraw of a turn calls this, since a turn can grow without a
        new message: laid out again for its width, a tool call adding a
        line, the view shown again.
        """
        if self.following and not self._end_pending:
            self._end_pending = True
            self.call_after_refresh(self._follow)

    def latest_line(self) -> Optional[int]:
        """Where the conversation ends, in the view's scrolled space."""
        blocks = [block for block in self._blocks.values() if block.display]
        return max((block.virtual_region.bottom for block in blocks), default=None)

    def _follow(self) -> None:
        """Jump only when the latest line would leave the view, putting it in the middle."""
        self._end_pending = False
        end = self.latest_line()
        height = self.scrollable_content_region.height
        if not self.following or end is None or height <= 0:
            return
        if end > self.scroll_y + height or end <= self.scroll_y:
            self.scroll_to(y=max(0, end - height // 2), animate=False, immediate=True)

    def on_resize(self) -> None:
        """Keep half a view of room below the turns, and the latest line in view."""
        self._room.styles.height = max(1, self.scrollable_content_region.height // 2)
        self.keep_at_end()

    def tick(self) -> None:
        """Redraw the turns that animate: the current one and running tool calls."""
        if self.session is None:
            return
        for turn in list(self.session.turns)[-2:]:
            block = self._blocks.get(id(turn))
            if block is not None:
                block.redraw()

    def toggle_follow(self) -> bool:
        """Toggle auto-scroll and return the new setting."""
        self.set_following(not self.following)
        return self.following

    def set_following(self, following: bool) -> None:
        """Follow the latest turn, or stay where the view is."""
        self.following = following
        self.update_legend()
        self.keep_at_end()

    def select(self, turn: Optional[Turn], *, top: bool = False) -> None:
        """Select a turn, and scroll it into view (to the top with ``top``)."""
        previous = self.selected_turn
        self.selected_turn = turn
        for t in (previous, turn):
            if t is not None and id(t) in self._blocks:
                self._blocks[id(t)].redraw()
        # While following, the latest line decides where the view is.
        if turn is not None and id(turn) in self._blocks and not self.following:
            self._blocks[id(turn)].scroll_visible(animate=False, top=top)
        self.update_legend()

    #
    # Looking back: issues and search
    #

    def issues(self) -> list[tuple[Turn, str]]:
        """The turns where something went wrong, in order, with what it was."""
        if self.session is None:
            return []
        turns = self._visible_turns(self.session)
        errors = self._errors_by_turn(turns)
        found = []
        for turn in turns:
            labels = [issue.plain for issue in turn_issues(turn)]
            labels += [f"✕ {e.processor} error" for e in errors.get(id(turn), [])]
            if labels:
                found.append((turn, " · ".join(labels)))
        return found

    def _errors_by_turn(self, turns: list[Turn]) -> dict[int, list[ErrorRecord]]:
        """Each error goes with the turn that was going on when it happened."""
        name = self.session.name if self.session is not None else None
        # When each turn opened, on the bot's clock like the errors' times.
        opened = [(turn.opened_at, turn) for turn in turns if turn.opened_at is not None]
        by_turn: dict[int, list[ErrorRecord]] = {}
        for error in self.errors():
            if error.worker not in (None, name):
                continue
            during = [turn for at, turn in opened if at <= error.timestamp]
            if during:
                by_turn.setdefault(id(during[-1]), []).append(error)
        return by_turn

    def matches(self) -> list[Turn]:
        """The turns that mention the search, in order."""
        if self.session is None or not self.search_text:
            return []
        query = self.search_text.casefold()
        return [t for t in self._visible_turns(self.session) if query in turn_text(t).casefold()]

    def set_search(self, query: Optional[str]) -> int:
        """Search what was said, or stop searching with ``None``, and return the matches."""
        self.search_text = (query or "").strip() or None
        for block in self._blocks.values():
            block.redraw()
        self.update_legend()
        return len(self.matches())

    def jump(self, step: int) -> Optional[Turn]:
        """Select the next (1) or previous (-1) search match, or issue when not searching.

        From the selected turn, it's the nearest one after or before it,
        wrapping around; with no selection, the first or the last. ``0``
        keeps the selected turn when it is one, as a new search does.
        Jumping pauses following, so the turn stays put.
        """
        if self.session is None:
            return None
        found, _ = self._found()
        if not found:
            return None
        order = {id(t): i for i, t in enumerate(self._visible_turns(self.session))}
        at = order.get(id(self.selected_turn)) if self.selected_turn is not None else None
        positions = [order[id(turn)] for turn, _ in found]
        if step == 0 and at in positions:
            index = positions.index(at)
        elif at is None:
            index = 0 if step >= 0 else len(found) - 1
        elif step >= 0:
            index = next((i for i, pos in enumerate(positions) if pos > at), 0)
        else:
            later = [i for i, pos in enumerate(positions) if pos < at]
            index = later[-1] if later else len(found) - 1
        turn = found[index][0]
        self.following = False
        self.select(turn, top=True)
        return turn

    def move_selection(self, step: int) -> Optional[Turn]:
        """Select the previous or next turn and return it."""
        if self.session is None:
            return None
        turns = self._visible_turns(self.session)
        if not turns:
            return None
        if self.selected_turn in turns:
            index = turns.index(self.selected_turn) + step
        else:
            index = len(turns) - 1
        index = max(0, min(len(turns) - 1, index))
        self.set_following(index == len(turns) - 1)
        self.select(turns[index])
        return turns[index]

    def current_or_last(self) -> Optional[Turn]:
        """The selected turn, or the last turn with something in it."""
        if self.selected_turn is not None:
            return self.selected_turn
        if self.session is None:
            return None
        turns = [t for t in self.session.turns if t.has_content]
        return turns[-1] if turns else None
