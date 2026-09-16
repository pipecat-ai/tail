#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Conversation tab: one block per turn."""

import json
from typing import Any, Optional

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from pipecat_tail.state import FunctionCall, Turn, WorkerSession
from pipecat_tail.widgets.render import (
    STYLE_BOT,
    STYLE_BRIGHT,
    STYLE_DIM,
    STYLE_ERROR,
    STYLE_INTERIM,
    STYLE_LABEL,
    STYLE_STRUCK,
    STYLE_TEXT,
    STYLE_TOOL,
    STYLE_USER,
    fmt_clock,
    fmt_secs,
    fmt_secs_fixed,
    truncate,
)

LABEL_WIDTH = 7


def _label(text: Text, label: str, style) -> None:
    text.append(label.ljust(LABEL_WIDTH), style=style)


def _compact(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return truncate(value, limit)
    try:
        return truncate(json.dumps(value, separators=(",", ":"), default=str), limit)
    except (TypeError, ValueError):
        return truncate(str(value), limit)


def render_function_call(call: FunctionCall) -> Text:
    """Render one tool call as a single line."""
    text = Text()
    text.append(" " * LABEL_WIDTH)
    marker = "▸"
    style = STYLE_TOOL
    if call.kind in ("function_call_failed", "function_call_timed_out"):
        marker = "✗"
        style = STYLE_ERROR
    elif call.kind == "function_call_cancelled":
        marker = "⊘"
        style = STYLE_DIM
    text.append(f"{marker} {call.function_name}", style=style)
    if call.arguments is not None:
        text.append(f"  {_compact(call.arguments)}", style=STYLE_DIM)
    if call.settled:
        if call.error:
            text.append(f"  → {truncate(call.error, 100)}", style=STYLE_ERROR)
        elif call.result is not None:
            text.append(f"  →  {_compact(call.result)}", style=STYLE_DIM)
        elif call.kind == "function_call_cancelled":
            text.append("  cancelled", style=STYLE_DIM)
        duration = call.duration_secs
        if duration is not None:
            text.append(f"   {fmt_secs(duration)}", style=STYLE_DIM)
    elif call.kind == "function_call_in_progress":
        text.append("  running…", style=STYLE_DIM)
    else:
        text.append("  waiting", style=STYLE_DIM)
    return text


def render_turn(turn: Turn) -> Text:
    """Render a turn block."""
    text = Text()
    number = f"turn {turn.number}" if turn.number is not None else "turn"
    text.append(number, style=STYLE_LABEL)
    text.append(f"   {fmt_clock(turn.started_at)}", style=STYLE_DIM)
    if turn.latency_secs is not None:
        text.append(f"   {fmt_secs_fixed(turn.latency_secs)}", style=STYLE_TEXT)
    elif turn.has_bot_content and not turn.ended:
        text.append("   …", style=STYLE_DIM)
    text.append("\n")

    if turn.user_text or turn.user_interim:
        _label(text, "user", STYLE_USER)
        if turn.user_text:
            text.append(turn.user_text, style=STYLE_TEXT)
        if turn.user_interim:
            if turn.user_text:
                text.append(" ")
            text.append(turn.user_interim, style=STYLE_INTERIM)
            text.append("▌", style=STYLE_USER)
        text.append("\n")
        if turn.user_llm_text and turn.user_llm_text.strip() != turn.user_text.strip():
            text.append(" " * LABEL_WIDTH)
            text.append("→ llm  ", style=STYLE_DIM)
            text.append(f'"{truncate(turn.user_llm_text, 200)}"', style=STYLE_DIM)
            text.append("\n")

    if turn.has_bot_content:
        _label(text, "bot", STYLE_BOT)
        unspoken_style = STYLE_STRUCK if turn.interrupted else STYLE_DIM
        wrote = False
        for segment in turn.segments:
            if not segment.will_be_spoken:
                continue
            spoken = segment.spoken_text
            unspoken = segment.unspoken_text
            if spoken:
                text.append(spoken, style=STYLE_BRIGHT)
                wrote = True
            if unspoken:
                text.append(unspoken, style=unspoken_style)
                wrote = True
            if spoken or unspoken:
                text.append(" ")
        pending = turn.pending_text.strip()
        if pending:
            text.append(pending, style=unspoken_style)
            wrote = True
        if not wrote and turn.tts_text:
            text.append(turn.tts_text, style=STYLE_BRIGHT)
            wrote = True
        if not wrote and not turn.function_calls:
            text.append("…", style=STYLE_DIM)
        text.rstrip()
        text.append("\n")
        for call in turn.function_calls:
            text.append_text(render_function_call(call))
            text.append("\n")
        if turn.interrupted:
            text.append(" " * LABEL_WIDTH)
            text.append("✂ interrupted", style=STYLE_ERROR)
            if turn.interrupted_at:
                text.append(f" {fmt_clock(turn.interrupted_at)}", style=STYLE_DIM)
            spoken, total = turn.spoken_word_counts()
            if total:
                text.append(f" · {spoken} of {total} words spoken", style=STYLE_DIM)
            text.append("\n")
    text.rstrip()
    return text


class TurnPanel(Static):
    """One turn."""

    def __init__(self, turn: Turn):
        """Create the panel for a turn."""
        super().__init__(render_turn(turn), classes="turn")
        self.turn = turn

    def refresh_turn(self) -> None:
        """Re-render from the turn."""
        self.update(render_turn(self.turn))


class ConversationView(VerticalScroll):
    """Scrollable list of turn panels for the selected worker."""

    def __init__(self):
        """Create the view."""
        super().__init__(id="conversation-view")
        self._session: Optional[WorkerSession] = None
        self._panels: dict[int, TurnPanel] = {}
        self.following = True
        self._legend = Static(_legend(), classes="legend")

    def compose(self):
        """Compose the legend."""
        yield self._legend

    def sync(self, session: WorkerSession) -> None:
        """Bring the panels in line with the session's turns."""
        if session is not self._session:
            self._session = session
            for panel in self._panels.values():
                panel.remove()
            self._panels.clear()

        alive = {id(turn) for turn in session.turns}
        for key in list(self._panels):
            if key not in alive:
                self._panels.pop(key).remove()

        new_panels = []
        for turn in session.turns:
            key = id(turn)
            if key not in self._panels:
                panel = TurnPanel(turn)
                self._panels[key] = panel
                new_panels.append(panel)
        if new_panels:
            self.mount(*new_panels, before=self._legend)

        # Only the most recent turns change; refreshing all would be wasteful.
        for turn in list(session.turns)[-2:]:
            panel = self._panels.get(id(turn))
            if panel is not None and panel not in new_panels:
                panel.refresh_turn()

        if self.following:
            self.call_after_refresh(self.scroll_end, animate=False)

    def toggle_follow(self) -> bool:
        """Toggle auto-scroll and return the new setting."""
        self.following = not self.following
        if self.following:
            self.scroll_end(animate=False)
        return self.following


def _legend() -> Text:
    text = Text()
    text.append("spoken ", style=STYLE_DIM)
    text.append("bright", style=STYLE_BRIGHT)
    text.append("   not yet spoken ", style=STYLE_DIM)
    text.append("dim", style=STYLE_DIM)
    text.append("   interrupted ", style=STYLE_DIM)
    text.append("struck", style=STYLE_STRUCK)
    text.append("   interim ", style=STYLE_DIM)
    text.append("italic", style=STYLE_INTERIM)
    return text
