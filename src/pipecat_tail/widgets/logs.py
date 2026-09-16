#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Logs tab: structured records with level filter, search and pinned errors."""

import re
from typing import Optional

from rich.style import Style
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, RichLog, Static

from pipecat_tail.state import LogRecord, SessionState
from pipecat_tail.widgets.render import (
    LEVEL_STYLES,
    STYLE_DIM,
    STYLE_ERROR,
    STYLE_LABEL,
    STYLE_TEXT,
    YELLOW,
    fmt_clock,
    truncate,
)

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LEVEL_RANK = {
    "TRACE": 0,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}
MAX_LINES = 2000
MATCH_STYLE = Style(color=YELLOW, bold=True, underline=True)


class LogLines(RichLog):
    """The log lines themselves."""

    def __init__(self):
        """Create the log."""
        super().__init__(id="log-lines", max_lines=MAX_LINES, wrap=False, highlight=False)


class LogsView(Vertical):
    """The Logs tab."""

    def __init__(self):
        """Create the tab."""
        super().__init__(id="logs-view")
        self.lines = LogLines()
        self.search = Input(
            placeholder="search (regex), Enter to apply, Esc to close", id="log-search"
        )
        self.pinned = Static(Text(""), id="log-pinned")
        self.level_index = 0
        self.pattern: Optional[re.Pattern] = None
        self.following = True
        self._shown = 0
        self._matches = 0
        self._state: Optional[SessionState] = None

    def compose(self):
        """Compose the search box, the lines and the pinned errors."""
        with Horizontal(id="log-toolbar"):
            yield self.search
        yield self.lines
        yield self.pinned

    def on_mount(self) -> None:
        """Start with the search box hidden."""
        self.search.display = False
        self._update_title()

    @property
    def level(self) -> str:
        """Minimum level shown."""
        return LEVELS[self.level_index]

    def cycle_level(self) -> str:
        """Raise the minimum level, wrapping around, and rebuild."""
        self.level_index = (self.level_index + 1) % len(LEVELS)
        self.rebuild()
        return self.level

    def set_pattern(self, pattern: str) -> Optional[str]:
        """Set the search pattern. Returns an error message if it is invalid."""
        pattern = pattern.strip()
        if not pattern:
            self.pattern = None
            self.rebuild()
            return None
        try:
            self.pattern = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return str(e)
        self.rebuild()
        return None

    def toggle_follow(self) -> bool:
        """Toggle auto-scroll and return the new setting."""
        self.following = not self.following
        self.lines.auto_scroll = self.following
        if self.following:
            self.lines.scroll_end(animate=False)
        self._update_title()
        return self.following

    def open_search(self) -> None:
        """Show and focus the search box."""
        self.search.display = True
        self.search.focus()

    def close_search(self) -> None:
        """Hide the search box."""
        self.search.display = False
        self.lines.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Apply the pattern on Enter."""
        error = self.set_pattern(event.value)
        if error:
            self.app.notify(f"Invalid pattern: {error}", severity="error")
        else:
            self.lines.focus()

    def accepts(self, record: LogRecord) -> bool:
        """Whether a record passes the level and search filters."""
        if LEVEL_RANK.get(record.level.upper(), 20) < LEVEL_RANK[self.level]:
            return False
        if self.pattern and not (
            self.pattern.search(record.message) or self.pattern.search(record.name)
        ):
            return False
        return True

    def bind_state(self, state: SessionState) -> None:
        """Remember the state to rebuild from."""
        self._state = state

    def rebuild(self) -> None:
        """Rewrite the lines from the state's log records."""
        self.lines.clear()
        self._shown = 0
        self._matches = 0
        if self._state is None:
            self._update_title()
            return
        for record in list(self._state.logs)[-MAX_LINES:]:
            self._write(record)
        self._update_title()
        self.refresh_pinned()

    def append(self, record: LogRecord) -> None:
        """Add one record if it passes the filters."""
        self._write(record)
        self._update_title()

    def _write(self, record: LogRecord) -> None:
        if not self.accepts(record):
            return
        self._shown += 1
        if self.pattern:
            self._matches += 1
        self.lines.write(self._line(record))

    def _line(self, record: LogRecord) -> Text:
        text = Text()
        text.append(record.clock, style=STYLE_DIM)
        level = record.level.upper()
        text.append(f"  {level[:8]:<8} ", style=LEVEL_STYLES.get(level, STYLE_TEXT))
        module = truncate(record.name, 34)
        if record.worker:
            module = truncate(f"{record.worker} {record.name}", 34)
        text.append(f"{module:<34} ", style=STYLE_DIM)
        message = Text(record.message, style=STYLE_TEXT)
        if level in ("ERROR", "CRITICAL"):
            message.stylize(STYLE_ERROR)
        elif level == "WARNING":
            message.stylize(LEVEL_STYLES["WARNING"])
        if self.pattern:
            message.highlight_regex(self.pattern, MATCH_STYLE)
        text.append_text(message)
        if record.exception:
            text.append(f"  {truncate(record.exception, 120)}", style=STYLE_ERROR)
        return text

    def _update_title(self) -> None:
        pieces = [f"level {self.level}"]
        if self.pattern:
            pieces.append(f"search /{self.pattern.pattern}/ · {self._matches} matches")
        pieces.append("following" if self.following else "paused (f to follow)")
        self.lines.border_title = "Logs"
        self.lines.border_subtitle = " · ".join(pieces)

    def refresh_pinned(self) -> None:
        """Re-render the pinned errors line."""
        state = self._state
        text = Text()
        if state is None or not state.errors:
            text.append("no errors", style=STYLE_DIM)
            text.append(f"   {self._shown} lines shown", style=STYLE_DIM)
            self.pinned.update(text)
            return
        error = state.errors[-1]
        text.append("pinned  ", style=STYLE_LABEL)
        text.append(
            f"{len(state.errors)} error{'s' if len(state.errors) != 1 else ''}", style=STYLE_ERROR
        )
        text.append(
            f"  {fmt_clock(error.timestamp)} {error.processor} · {error.category} · "
            f"{'still usable' if error.processor_usable else 'unusable'}",
            style=STYLE_DIM,
        )
        text.append(f"   {self._shown} lines shown", style=STYLE_DIM)
        self.pinned.update(text)
