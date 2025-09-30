#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Textual widget for displaying Tail, Pipecat, and Python version info."""

from textual.app import ComposeResult, RenderResult
from textual.containers import HorizontalGroup
from textual.widget import Widget
from textual.widgets import Label, Static


class ConnectedStatus(Widget):
    """Widget that simply shows the connected system status."""

    def render(self) -> RenderResult:
        """Render the connected system status."""
        return "CONNECTED"


class DisconnectedStatus(Widget):
    """Widget that simply shows the disconnected system status."""

    def render(self) -> RenderResult:
        """Render the disconnected system status."""
        return "DISCONNECTED"


class ErrorStatus(Widget):
    """Widget that simply shows the error system status."""

    def render(self) -> RenderResult:
        """Render the error system status."""
        return "ERROR"


class SystemStatus(Static):
    """Container widget that shows the system status."""

    def __init__(self):
        """Initialize the system status widget."""
        super().__init__()
        self._status = DisconnectedStatus()

    def compose(self) -> ComposeResult:
        """Compose the system status label and actual status."""
        with HorizontalGroup(id="container"):
            yield Label("Status: ")
            yield self._status

    def update_status(self, status: str):
        """Update the system status."""
        container = self.query_one("#container")
        self._status.remove()
        match status:
            case "CONNECTED":
                self._status = ConnectedStatus()
            case "DISCONNECTED":
                self._status = DisconnectedStatus()
            case "ERROR":
                self._status = ErrorStatus()
            case _:
                self._status = DisconnectedStatus()
        container.mount(self._status)
        self._status.scroll_visible()
