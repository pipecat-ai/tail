#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Textual-based dashboard for Pipecat.

This module defines a Textual-based application composed of three panels (left,
middle, right). It receives RTVI messages and updates widgets such as metrics,
conversation transcript, system logs, and audio levels.
"""

from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from loguru import logger
from pipecat.processors.frameworks.rtvi import (
    RTVI_MESSAGE_LABEL,
    RTVIBotAudioLevelMessage,
    RTVIBotTTSTextMessage,
    RTVIMetricsMessage,
    RTVISystemLogMessage,
    RTVIUserAudioLevelMessage,
    RTVIUserTranscriptionMessage,
)
from pydantic import ValidationError
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, Rule, Static

from pipecat_tail.rtvi import RTVITailReadyMessage
from pipecat_tail.widgets.audio_level import AudioLevel
from pipecat_tail.widgets.conversation import Conversation
from pipecat_tail.widgets.llm_service_info import LLMServiceInfo
from pipecat_tail.widgets.service_metrics import ServiceMetrics
from pipecat_tail.widgets.system_info import SystemInfo
from pipecat_tail.widgets.system_logs import SystemLogs
from pipecat_tail.widgets.system_status import SystemStatus
from pipecat_tail.widgets.tts_service_info import TTSServiceInfo


class LeftPanel(Static):
    """Left-side panel showing service metrics.

    Maintains a set of ``ServiceMetrics`` widgets keyed by processor name and
    appends new values as metrics arrive.
    """

    def __init__(self):
        """Initialize the panel and its internal container state."""
        super().__init__()
        self._metrics: Dict[str, ServiceMetrics] = {}
        self._vertical = Vertical()

    def compose(self) -> ComposeResult:
        """Compose the service metrics layout."""
        yield Label("Metrics")
        yield self._vertical

    async def handle_metrics_ttfb_data(self, data: Mapping[str, Any]):
        """Handle a TTFB metrics entry.

        Args:
            data: A dict with keys ``processor`` and ``value``.
        """
        processor = data["processor"]
        value = data["value"]
        if not processor in self._metrics:
            # Metrics
            widget = ServiceMetrics(processor)
            self._metrics[processor] = widget
            self._vertical.mount(widget)
            widget.scroll_visible()
            # Rule
            rule = Rule()
            self._vertical.mount(rule)
            rule.scroll_visible()
        # Update metrics
        self._metrics[processor].add_value(value)


class MiddlePanel(Static):
    """Middle panel with conversation transcript and system logs."""

    def __init__(self):
        """Initialize the middle panel."""
        super().__init__()
        self._logs = SystemLogs()
        self._conversation = Conversation()

    def compose(self) -> ComposeResult:
        """Compose the conversation and logs layout."""
        with Vertical():
            yield self._conversation
            yield self._logs

    async def append_log(self, message):
        """Append a system log line.

        Args:
            message: Text to append to logs.
        """
        await self._logs.append_log(message)

    async def handle_user_started_speaking(self):
        """Notify that the user started speaking."""
        await self._conversation.handle_user_started_speaking()

    async def handle_user_stopped_speaking(self):
        """Notify that the user stopped speaking."""
        await self._conversation.handle_user_stopped_speaking()

    async def handle_user_transcription(self, text: str):
        """Append user transcription to the conversation.

        Args:
            text: Transcribed user text.
        """
        await self._conversation.handle_user_transcription(text)

    async def handle_bot_started_speaking(self):
        """Notify that the bot started speaking."""
        await self._conversation.handle_bot_started_speaking()

    async def handle_bot_stopped_speaking(self):
        """Notify that the bot stopped speaking."""
        await self._conversation.handle_bot_stopped_speaking()

    async def handle_bot_transcription(self, text: str):
        """Append bot transcription to the conversation.

        Args:
            text: Bot TTS text.
        """
        await self._conversation.handle_bot_transcription(text)


class RightPanel(Static):
    """Right panel with system info and audio levels."""

    def __init__(self):
        """Initialize widgets for system info and audio level meters."""
        super().__init__()
        self._system_info = SystemInfo()
        self._system_status = SystemStatus()
        self._user_audio_level = AudioLevel("User")
        self._bot_audio_level = AudioLevel("Bot")
        self._llm_service_info = LLMServiceInfo()
        self._tts_service_info = TTSServiceInfo()

    def compose(self) -> ComposeResult:
        """Compose the system info and audio level widgets."""
        with Vertical():
            yield self._system_info
            yield Rule()
            yield self._system_status
            yield Rule()
            yield self._user_audio_level
            yield Rule()
            yield self._bot_audio_level
            yield Rule()
            yield Label("LLM usage")
            yield self._llm_service_info
            yield Rule()
            yield Label("TTS usage")
            yield self._tts_service_info

    async def handle_system_info(self, info: Mapping[str, Any]):
        """Update the system info panel.

        Args:
            info: Mapping of system information.
        """
        self._system_info.update_system_info(info)

    async def handle_system_status(self, status: str):
        """Update the system status panel.

        Args:
            status: Current status.
        """
        self._system_status.update_status(status)

    async def handle_user_audio_level(self, level: float):
        """Update the user audio level meter.

        Args:
            level: Normalized audio level for the user.
        """
        self._user_audio_level.update_level(level)

    async def handle_bot_audio_level(self, level: float):
        """Update the bot audio level meter.

        Args:
            level: Normalized audio level for the bot.
        """
        self._bot_audio_level.update_level(level)

    async def handle_services_info(self, info: Mapping[str, Any]):
        """Update services info.

        Args:
            info: Services information (tokens, characters, ...).
        """
        if "tokens" in info:
            self._llm_service_info.update_tokens(info["tokens"])
        if "characters" in info:
            self._tts_service_info.update_characters(info["characters"])


class TailApp(App):
    """Main Textual application orchestrating the UI and message handling."""

    CSS_PATH = "tail.tcss"

    BINDINGS = [
        ("c", "connect", "Connect"),
        ("q", "quit", "Exit"),
    ]

    def __init__(
        self,
        *,
        on_mount: Optional[Callable[[], Awaitable[None]]] = None,
        on_shutdown: Optional[Callable[[], Awaitable[None]]] = None,
        action_connect: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """Initialize the Tail Textual app.

        Args:
            on_mount: Optional hook called when the UI mounts.
            on_shutdown: Optional hook called before quitting.
            action_connect: Optional hook called to start a connection.
        """
        super().__init__()
        self._on_mount = on_mount
        self._on_shutdown = on_shutdown
        self._action_connect = action_connect

        self._left_panel = LeftPanel()
        self._middle_panel = MiddlePanel()
        self._right_panel = RightPanel()

    async def on_mount(self):
        """Set up theme, titles, and invoke optional mount hook."""
        self.theme = "nord"
        self.title = "Tail"
        self.sub_title = "A terminal dashboard for Pipecat"
        if self._on_mount:
            await self._on_mount()

    async def action_connect(self):
        """Action to invoke the optional connect hook."""
        if self._action_connect:
            await self._action_connect()

    async def action_quit(self):
        """Action to cleanly shutdown and then quit the app."""
        if self._on_shutdown:
            await self._on_shutdown()
        await super().action_quit()

    def compose(self) -> ComposeResult:
        """Compose the app layout with header, three panels, and footer."""
        # Header
        yield Header(show_clock=True)
        # Main horizontal panels
        with Horizontal():
            yield self._left_panel
            yield self._middle_panel
            yield self._right_panel
        # Footer
        yield Footer()

    async def clear(self):
        """Clear system status, audio levels and other UI elements."""
        await self._right_panel.handle_user_audio_level(0)
        await self._right_panel.handle_bot_audio_level(0)
        await self._right_panel.handle_system_status("DISCONNECTED")

    async def handle_message(self, message: Dict[str, Any]):
        """Validate and dispatch incoming messages to handlers.

        Args:
            message: Raw message dictionary, expected to follow RTVI schema.
        """
        try:
            if message.get("label") != RTVI_MESSAGE_LABEL:
                logger.warning(f"Ignoring non-RTVI message: {message}")
                return
            await self._handle_message(message)
        except ValidationError as e:
            logger.warning(f"Invalid RTVI message (error: {e}): {message}")

    async def _handle_message(self, message: Dict[str, Any]):
        """Route a parsed RTVI message to the appropriate handler.

        Args:
            message: Message with a ``type`` field and associated data.
        """
        msg_type = message.get("type")
        if not msg_type:
            logger.warning(f"Ignoring non-RTVI message: {message}")
            return
        match msg_type:
            case "tail-ready":
                rtvi_message = RTVITailReadyMessage.model_validate(message)
                await self.handle_tail_ready(rtvi_message.data)
            case "tail-pipeline-finished":
                await self.handle_tail_pipeline_finished()
            case "user-started-speaking":
                await self.handle_user_started_speaking()
            case "user-stopped-speaking":
                await self.handle_user_stopped_speaking()
            case "user-audio-level":
                rtvi_message = RTVIUserAudioLevelMessage.model_validate(message)
                await self.handle_user_audio_level(rtvi_message.data.value)
            case "user-transcription":
                rtvi_message = RTVIUserTranscriptionMessage.model_validate(message)
                if rtvi_message.data.final:
                    await self.handle_user_transcription(rtvi_message.data.text)
            case "bot-started-speaking":
                await self.handle_bot_started_speaking()
            case "bot-stopped-speaking":
                await self.handle_bot_stopped_speaking()
            case "bot-audio-level":
                rtvi_message = RTVIBotAudioLevelMessage.model_validate(message)
                await self.handle_bot_audio_level(rtvi_message.data.value)
            case "bot-tts-text":
                rtvi_message = RTVIBotTTSTextMessage.model_validate(message)
                await self.handle_bot_transcription(rtvi_message.data.text)
            case "metrics":
                rtvi_message = RTVIMetricsMessage.model_validate(message)
                await self.handle_metrics(rtvi_message.data)
                await self.handle_services_info(rtvi_message.data)
            case "system-log":
                rtvi_message = RTVISystemLogMessage.model_validate(message)
                await self.handle_system_log(rtvi_message.data.text)

    async def handle_tail_ready(self, info: Mapping[str, Any]):
        """Handle initial tail-ready message by updating system info.

        Args:
            info: System information.
        """
        await self._right_panel.handle_system_info(info)
        await self._right_panel.handle_system_status("CONNECTED")

    async def handle_tail_pipeline_finished(self):
        """Reset audio meters when the pipeline has finished."""
        await self.clear()

    async def handle_user_started_speaking(self):
        """Notify conversation that the user started speaking."""
        await self._middle_panel.handle_user_started_speaking()

    async def handle_user_stopped_speaking(self):
        """Notify conversation that the user stopped speaking."""
        await self._middle_panel.handle_user_stopped_speaking()

    async def handle_user_audio_level(self, level: float):
        """Update the user audio level meter from message data.

        Args:
            level: User audio level.
        """
        await self._right_panel.handle_user_audio_level(level)

    async def handle_user_transcription(self, text: str):
        """Append the latest user transcription.

        Args:
            text: User transcription.
        """
        await self._middle_panel.handle_user_transcription(text + " ")

    async def handle_bot_started_speaking(self):
        """Notify conversation that the bot started speaking."""
        await self._middle_panel.handle_bot_started_speaking()

    async def handle_bot_stopped_speaking(self):
        """Notify conversation that the bot stopped speaking."""
        await self._middle_panel.handle_bot_stopped_speaking()
        await self._right_panel.handle_bot_audio_level(0)

    async def handle_bot_audio_level(self, level: float):
        """Update the bot audio level meter from message data.

        Args:
            level: Bot audio level.
        """
        await self._right_panel.handle_bot_audio_level(level)

    async def handle_bot_transcription(self, text: str):
        """Append the latest bot TTS text.

        Args:
            text: Bot transcription.
        """
        await self._middle_panel.handle_bot_transcription(text + " ")

    async def handle_metrics(self, metrics: Mapping[str, Any]):
        """Handle metrics payloads and update the services metrics panel.

        Args:
            metrics: RTVI metrics.
        """
        if not "ttfb" in metrics:
            return

        for metrics in metrics["ttfb"]:
            await self._left_panel.handle_metrics_ttfb_data(metrics)

    async def handle_system_log(self, text: str):
        """Append system log messages.

        Args:
            text: System log message.
        """
        await self._middle_panel.append_log(text)

    async def handle_system_status(self, status: str):
        """Update system status.

        Args:
            status: System status.
        """
        await self._right_panel.handle_system_status(status)

    async def handle_services_info(self, info: Mapping[str, Any]):
        """Handle metrics payloads and updates services info.

        Args:
            info: Services info (usage tokens, etc.).
        """
        await self._right_panel.handle_services_info(info)
