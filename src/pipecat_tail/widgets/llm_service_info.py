#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Textual widget for displaying Tail, Pipecat, and Python version info."""

from typing import Any, List, Mapping

from textual.app import ComposeResult
from textual.widgets import DataTable, Static


class LLMServiceInfo(Static, can_focus=False, can_focus_children=False):
    """Compact table showing LLM tokens information."""

    DEFAULT_CSS = """
    DataTable {
        overflow-x: hidden;
    }
    """

    def __init__(self):
        """Initialize the data table and default placeholder values."""
        super().__init__()
        self._tokens = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "reasoning_tokens": 0,
        }
        self._rows = [
            ("Description", "Value"),
            ("Prompt", "0"),
            ("Completion", "0"),
            ("Total", "0"),
            ("Cache Read Input", "0"),
            ("Cache Write Input", "0"),
            ("Reasoning", "0"),
        ]
        self._table = DataTable(show_header=False, show_cursor=False, zebra_stripes=True)

    def compose(self) -> ComposeResult:
        """Compose the single data table widget."""
        yield self._table

    def on_mount(self) -> None:
        """Populate the table when the widget mounts."""
        self._update_table()

    def update_tokens(self, tokens: List[Mapping[str, Any]]):
        """Update displayed LLM tokens.

        Args:
            tokens: List of LLM tokens.
        """
        for t in tokens:
            self._tokens["prompt_tokens"] += t["prompt_tokens"]
            self._tokens["completion_tokens"] += t["completion_tokens"]
            self._tokens["total_tokens"] += t["total_tokens"]

            if "cache_read_input_tokens" in t:
                self._tokens["cache_read_input_tokens"] += t["cache_read_input_tokens"]

            if "cache_creation_input_tokens" in t:
                self._tokens["cache_write_input_tokens"] += t["cache_creation_input_tokens"]

            if "reasoning_tokens" in t:
                self._tokens["reasoning_tokens"] += t["reasoning_tokens"]

        self._rows = [
            ("Name", "Value"),
            ("Prompt", self._tokens["prompt_tokens"]),
            ("Completion", self._tokens["completion_tokens"]),
            ("Total", self._tokens["prompt_tokens"]),
            ("Cache Read Input", self._tokens["cache_read_input_tokens"]),
            ("Cache Write Input", self._tokens["cache_write_input_tokens"]),
            ("Reasoning", self._tokens["reasoning_tokens"]),
        ]

        self._update_table()

    def _update_table(self):
        """Refresh the table with the current tokens info."""
        self._table.clear()
        self._table.add_column(self._rows[0][0], width=17)
        self._table.add_column(self._rows[0][1], width=5)
        self._table.add_rows(self._rows[1:])
        self._table.refresh()
