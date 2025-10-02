#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Textual widget for displaying a normalized audio level as a progress bar."""

from textual.app import ComposeResult
from textual.color import Gradient
from textual.containers import Vertical
from textual.widgets import Label, ProgressBar, Static


class AudioLevel(Static):
    """A compact audio level meter widget.

    Shows a label and a gradient progress bar representing the current level.
    """

    def __init__(self, text: str):
        """Initialize the audio level widget.

        Args:
            text: The text to display for this audio level.
        """
        super().__init__()
        self._text = text

        gradient = Gradient.from_colors(
            "#881177",
            "#aa3355",
            "#cc6666",
            "#ee9944",
            "#eedd00",
            "#99dd55",
            "#44dd88",
            "#22ccbb",
            "#00bbcc",
            "#0099cc",
            "#3366bb",
            "#663399",
        )
        self._bar = ProgressBar(total=1.0, gradient=gradient, show_percentage=False, show_eta=False)

    def compose(self) -> ComposeResult:
        """Compose the label and progress bar layout."""
        with Vertical():
            yield Label(self._text)
            yield self._bar

    def on_mount(self) -> None:
        """Initialize the progress bar value when the widget mounts."""
        self._bar.update(progress=0)

    def update_level(self, level: float):
        """Update the audio level.

        Args:
            level: Normalized level in the range [0.0, 1.0].
        """
        self._bar.update(progress=level, total=1.0)
