#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Textual widget for displaying Tail, Pipecat, and Python version info."""

from typing import List, Mapping

from textual.app import ComposeResult
from textual.widgets import DataTable, Static


class TTSServiceInfo(Static, can_focus=False, can_focus_children=False):
    """Compact table showing TTS service information."""

    DEFAULT_CSS = """
    DataTable {
        overflow-x: hidden;
    }
    """

    def __init__(self):
        """Initialize the data table and default placeholder values."""
        super().__init__()
        self._characters = 0
        self._rows = [
            ("Description", "Value"),
            ("Characters", "0"),
        ]
        self._table = DataTable(show_header=False, show_cursor=False, zebra_stripes=True)

    def compose(self) -> ComposeResult:
        """Compose the single data table widget."""
        yield self._table

    def on_mount(self) -> None:
        """Populate the table when the widget mounts."""
        self._update_table()

    def update_characters(self, characters: List[Mapping[str, int]]):
        """Update displayed TTS characters.

        Args:
            characters: List of TTS characters used.
        """
        for c in characters:
            self._characters += c["value"]

        self._rows = [
            ("Description", "Value"),
            ("Characters", self._characters),
        ]

        self._update_table()

    def _update_table(self):
        """Refresh the table with the current tokens info."""
        self._table.clear()
        self._table.add_column(self._rows[0][0], width=10)
        self._table.add_column(self._rows[0][1], width=5)
        self._table.add_rows(self._rows[1:])
        self._table.refresh()
