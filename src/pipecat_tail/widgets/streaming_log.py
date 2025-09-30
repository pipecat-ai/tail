#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Streaming text widgets used by the Tail TUI.

Provides ``StreamingLog`` for auto-scrolling streaming text and an internal
``StreamingContent`` helper that manages styled text chunks and wrapping.
"""

from typing import List, Optional, Self

from rich.cells import cell_len
from rich.containers import Lines
from rich.style import Style
from rich.text import Text
from textual.cache import LRUCache
from textual.geometry import Size
from textual.reactive import var
from textual.scroll_view import ScrollView
from textual.strip import Strip


class StreamingLog(ScrollView, can_focus=True):
    """A ScrollView that displays streaming text with automatic scrolling."""

    DEFAULT_CSS = """
    StreamingLog{
        background: $surface;
        color: $foreground;
        overflow-y: scroll;
        &:focus {
            background-tint: $foreground 5%;
        }
    }
    """

    # Reactive attributes
    wrap: var[bool] = var(True)
    auto_scroll: var[bool] = var(True)

    def __init__(
        self,
        wrap: bool = True,
        auto_scroll: bool = True,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ):
        """Initialize the streaming log view.

        Args:
            wrap: Whether to wrap text within the view bounds.
            auto_scroll: Whether to automatically scroll to bottom when text is added.
            name: Optional widget name.
            id: Optional widget identifier.
            classes: Optional CSS class list for the widget.
        """
        super().__init__(name=name, id=id, classes=classes)
        self.wrap = wrap
        self.auto_scroll = auto_scroll
        self._lines: Lines = Lines()
        self._chunks: List[tuple[str, Optional[str | Style]]] = []
        self._render_line_cache: LRUCache[int, Strip] = LRUCache(1024)

    def notify_style_update(self) -> None:
        """Called by Textual when styles update."""
        super().notify_style_update()
        self._render_line_cache.clear()

    def add_text(self, text: str, style: Optional[str | Style] = None) -> None:
        """Add new text to the view, optionally styled.

        This can be called frequently to stream text incrementally.

        Args:
            text: Text to append to the existing content.
            style: Optional Rich style string (e.g., "bold red", "#ff8800").
        """
        self._chunks.append((text, style))

        # Build a text element with the whole contents.
        rich_text = Text(no_wrap=not self.wrap, style=self.rich_style)
        for chunk_text, chunk_style in self._chunks:
            rich_text.append(chunk_text, style=chunk_style)

        # This is how much text we can fit that can be viewed.
        scrollable_content_width = self.scrollable_content_region.width

        # Get all the lines, depending if we are wrapping or not.
        if self.wrap:
            self._lines = rich_text.wrap(self.app.console, scrollable_content_width)
            lines_width = 0
        else:
            self._lines = rich_text.split()
            lines_width = max([len(l) for l in self._lines])

        # Update virtual size with the maximum width and the total number of
        # lines, wether we are wrapping or not.
        max_width = max(scrollable_content_width, lines_width)
        self.virtual_size = Size(max_width, len(self._lines))

        # Just refresh the last line.
        self.refresh_lines(len(self._lines) - 1)

        if self.auto_scroll:
            self.scroll_end(animate=False)

    def clear(self) -> Self:
        """Clear all text content and reset the view state."""
        self._chunks = []
        self._lines = Lines()
        self.virtual_size = Size(0, 0)
        self._render_line_cache.clear()
        return self

    def render_line(self, y: int) -> Strip:
        """Render a line of content.

        Args:
            y: Y Coordinate of line.

        Returns:
            A rendered line.
        """
        scroll_x, scroll_y = self.scroll_offset
        strip = self._render_line(scroll_y + y, scroll_x, self.size.width)
        return strip

    def _render_line(self, y: int, scroll_x: int, width: int) -> Strip:
        """Render a line into a cropped strip.

        Args:
            y: Y offset of line.
            scroll_x: Current horizontal scroll.
            width: Width of the widget.

        Returns:
            A Strip suitable for rendering.
        """
        rich_style = self.rich_style
        if y >= len(self._lines):
            return Strip.blank(width, rich_style)

        line = self._render_line_strip(y)
        line = line.crop_extend(scroll_x, scroll_x + width, rich_style)
        line = line.apply_offsets(scroll_x, y)
        return line

    def _render_line_strip(self, y: int) -> Strip:
        """Render a line into a Strip.

        Args:
            y: Y offset of line.
            rich_style: Rich style of line.

        Returns:
            An uncropped Strip.
        """
        if y in self._render_line_cache:
            return self._render_line_cache[y]

        line = self._lines[y]

        line = Strip(line.render(self.app.console), cell_len(line.plain))

        self._render_line_cache[y] = line

        return line

    def refresh_lines(self, y_start: int, line_count: int = 1) -> None:
        """Refresh one or more lines.

        Args:
            y_start: First line to refresh.
            line_count: Total number of lines to refresh.
        """
        for y in range(y_start, y_start + line_count):
            self._render_line_cache.discard(y)
        super().refresh_lines(y_start, line_count=line_count)
