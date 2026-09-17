#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The always-visible strip under the header and the error banner."""

from rich.text import Text
from textual.color import Gradient
from textual.containers import Horizontal
from textual.widgets import Label, ProgressBar, Static

from pipecat_tail.state import SessionState
from pipecat_tail.widgets.render import (
    LEVEL_GRADIENT,
    STYLE_BRIGHT,
    STYLE_DIM,
    STYLE_ERROR,
    STYLE_LABEL,
    STYLE_TEXT,
    fmt_clock,
    fmt_int,
    fmt_secs_fixed,
    status_dot,
    truncate,
)


class LevelMeter(ProgressBar):
    """An audio level as a gradient bar, blue when quiet and red when loud."""

    def __init__(self, id: str):
        """Create the meter."""
        super().__init__(
            total=1.0,
            # Textual paints the fill's gradient from its right end back to
            # the left, so the colors go in reversed to keep blue on the left.
            gradient=Gradient.from_colors(*reversed(LEVEL_GRADIENT)),
            show_percentage=False,
            show_eta=False,
            id=id,
        )

    def set_level(self, level: float) -> None:
        """Move the bar to a level in ``[0, 1]``."""
        self.update(progress=max(0.0, min(1.0, level)), total=1.0)


class TopStrip(Horizontal):
    """One line answering: connected to what, which worker, how is it doing."""

    def __init__(self):
        """Create the strip."""
        super().__init__(id="strip")
        self._text = Static(Text(""), id="strip-text")
        self.user_meter = LevelMeter("user-meter")
        self.bot_meter = LevelMeter("bot-meter")

    def compose(self):
        """Compose the text and the two meters."""
        yield self._text
        yield Label("user", id="user-label")
        yield self.user_meter
        yield Label("bot", id="bot-label")
        yield self.bot_meter

    @property
    def content(self):
        """The text part of the strip, for tests."""
        return self._text.content

    def update_from(self, state: SessionState) -> None:
        """Re-render the strip from the state."""
        session = state.selected
        self.user_meter.set_level(session.user_level)
        self.bot_meter.set_level(session.bot_level)
        self.query_one("#user-label", Label).set_class(session.user_speaking, "speaking")
        self.query_one("#bot-label", Label).set_class(session.bot_speaking, "speaking")
        text = Text()
        text.append_text(status_dot(state.status))
        text.append(f" {state.status}", style=STYLE_TEXT)
        if state.status != "connected" and state.status_detail:
            text.append(f" {truncate(state.status_detail, 40)}", style=STYLE_DIM)

        def field(label: str, value: Text | str) -> None:
            text.append("   ")
            text.append(f"{label} ", style=STYLE_LABEL)
            if isinstance(value, Text):
                text.append_text(value)
            else:
                text.append(value, style=STYLE_TEXT)

        workers = state.pipeline_workers
        if session.name:
            suffix = f" ({len(workers)})" if len(workers) > 1 else ""
            field("worker", f"{session.name}{suffix}")
        elif state.runner:
            field("runner", state.runner)

        turn = session.current_turn
        if turn is not None and turn.number is not None:
            field("turn", f"#{turn.number}")
        last = session.last_latency
        if last is not None:
            field("last", fmt_secs_fixed(last.latency_secs))
        if session.prompt_tokens or session.completion_tokens:
            field(
                "tokens",
                f"{fmt_int(session.prompt_tokens)}→{fmt_int(session.completion_tokens)}",
            )
        if session.tts_characters:
            field("tts", f"{fmt_int(session.tts_characters)} ch")

        self._text.update(text)


class ErrorBanner(Static):
    """Banner across the top showing the latest error until dismissed."""

    def __init__(self):
        """Create the banner, hidden."""
        super().__init__(Text(""), id="banner")
        self.display = False

    def update_from(self, state: SessionState) -> None:
        """Show the newest active error, or hide when there is none."""
        errors = state.active_errors
        if not errors:
            self.display = False
            return
        error = errors[-1]
        text = Text()
        text.append("⚠ error", style=STYLE_ERROR)
        if len(errors) > 1:
            text.append(f" ({len(errors)})", style=STYLE_ERROR)
        text.append("  ")
        text.append(error.processor, style=STYLE_BRIGHT)
        text.append(f" · {error.category}", style=STYLE_TEXT)
        text.append(
            " · still usable" if error.processor_usable else " · unusable", style=STYLE_TEXT
        )
        if error.worker:
            text.append(f" · {error.worker}", style=STYLE_DIM)
        text.append(f' · "{truncate(error.message, 70)}"', style=STYLE_TEXT)
        text.append(f"   {fmt_clock(error.timestamp)}", style=STYLE_DIM)
        text.append("   e jump · x dismiss", style=STYLE_DIM)
        self.update(text)
        self.display = True
