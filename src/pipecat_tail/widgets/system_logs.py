#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Textual widget for displaying streaming system log lines."""

from textual.app import ComposeResult
from textual.widgets import Label, RichLog, Static


class SystemLogs(Static):
    """Simple logs view with a header and a RichLog widget."""

    def __init__(self):
        """Initialize the log widget with wrapping and highlighting enabled."""
        super().__init__()
        self._log = RichLog(wrap=True, highlight=True)

    def compose(self) -> ComposeResult:
        """Compose the logs header and the RichLog area."""
        yield Label("Logs")
        yield self._log

    async def append_log(self, message: str):
        """Append a new log entry.

        Args:
            message: The log message to write.
        """
        self._log.write(message.strip())
