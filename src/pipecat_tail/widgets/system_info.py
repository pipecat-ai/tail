#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Textual widget for displaying Tail, Pipecat, and Python version info."""

from typing import Any, Mapping

from textual.app import ComposeResult
from textual.widgets import DataTable, Static


class SystemInfo(Static, can_focus=False, can_focus_children=False):
    """Compact table showing system version information."""

    DEFAULT_CSS = """
    DataTable {
        overflow-x: hidden;
    }
    """

    def __init__(self):
        """Initialize the data table and default placeholder values."""
        super().__init__()
        self._rows = [
            ("Info", "Version"),
            ("Tail", "N/A"),
            ("Pipecat", "N/A"),
            ("Python", "N/A"),
        ]
        self._table = DataTable(show_header=False, show_cursor=False, zebra_stripes=True)

    def compose(self) -> ComposeResult:
        """Compose the single data table widget."""
        yield self._table

    def on_mount(self) -> None:
        """Populate the table when the widget mounts."""
        self._update_table()

    def update_system_info(self, info: Mapping[str, Any]):
        """Update displayed versions from a mapping.

        Args:
            info: Mapping with optional keys ``tail``, ``pipecat``, and ``python``.
        """
        tail_version = info.get("tail") or "N/A"
        pipecat_version = info.get("pipecat") or "N/A"
        python_version = info.get("python") or "N/A"
        self._rows = [
            ("Info", "Version"),
            ("Tail", tail_version),
            ("Pipecat", pipecat_version),
            ("Python", python_version),
        ]
        self._update_table()

    def _update_table(self):
        """Refresh the table with the current system info."""
        self._table.clear()
        self._table.add_column(self._rows[0][0], width=7)
        self._table.add_column(self._rows[0][1], width=15)
        self._table.add_rows(self._rows[1:])
        self._table.refresh()
