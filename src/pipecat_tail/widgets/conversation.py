#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Textual widget showing a streaming conversation between user and agent."""

from rich.style import Style
from textual.app import ComposeResult
from textual.widgets import Label, Static

from pipecat_tail.widgets.streaming_log import StreamingLog


class Conversation(Static):
    """Conversation transcript widget with user and agent turns.

    Maintains speaking state for user and agent and streams text into a
    ``StreamingLog`` as transcripts arrive.
    """

    def __init__(self):
        """Initialize the conversation widget."""
        super().__init__()
        self._log = StreamingLog()

        # User and bot styles.
        self._user_style = Style()
        self._bot_style = Style()

        self._user_speaking = False
        self._bot_speaking = False

    def on_mount(self) -> None:
        """Get user and bot colors when the widget mounts."""
        css = self.app.get_css_variables()
        # Only set foreground colors so the StreamingLog background is visible.
        self._user_style = Style(color=css["text-primary"])
        self._bot_style = Style(color=css["foreground"])

    def compose(self) -> ComposeResult:
        """Compose the conversation header and streaming log."""
        yield Label("Conversation")
        yield self._log

    async def handle_user_started_speaking(self):
        """Mark that the user started speaking and start a new line."""
        if not self._user_speaking:
            self._log.add_text("\n\n")
            self._log.add_text("User: ", style=self._user_style)
            self._user_speaking = True
            self._bot_speaking = False

    async def handle_user_stopped_speaking(self):
        """Mark that the user stopped speaking."""
        pass

    async def handle_bot_started_speaking(self):
        """Mark that the agent started speaking and start a new line."""
        if not self._bot_speaking:
            self._log.add_text("\n\n")
            self._log.add_text("Agent: ", style=self._bot_style)
            self._user_speaking = False
            self._bot_speaking = True

    async def handle_bot_stopped_speaking(self):
        """Mark that the agent stopped speaking."""
        pass

    async def handle_user_transcription(self, text: str):
        """Append transcribed user text to the current line.

        Args:
            text: User transcription chunk to append.
        """
        if self._user_speaking:
            self._log.add_text(text, style=self._user_style)

    async def handle_bot_transcription(self, text: str):
        """Append transcribed agent text to the current line.

        Args:
            text: Agent transcription chunk to append.
        """
        if self._bot_speaking:
            self._log.add_text(text, style=self._bot_style)
