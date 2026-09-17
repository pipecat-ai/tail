#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Rendering helpers shared by the Tail widgets.

Colors follow the Nord palette the app theme uses, so Rich text drawn by the
widgets matches the Textual chrome around it.
"""

import datetime
from typing import Iterable, Optional, Sequence

from rich.style import Style
from rich.text import Text

# Nord
POLAR_1 = "#3B4252"
POLAR_3 = "#4C566A"
DIM = "#7B88A1"
SNOW = "#D8DEE9"
BRIGHT = "#ECEFF4"
FROST_CYAN = "#88C0D0"
FROST_BLUE = "#81A1C1"
FROST_DEEP = "#5E81AC"
RED = "#BF616A"
ORANGE = "#D08770"
YELLOW = "#EBCB8B"
GREEN = "#A3BE8C"
PURPLE = "#B48EAD"

STYLE_DIM = Style(color=DIM)
STYLE_LABEL = Style(color=DIM)
STYLE_TEXT = Style(color=SNOW)
STYLE_BRIGHT = Style(color=BRIGHT, bold=True)
STYLE_USER = Style(color=FROST_CYAN, bold=True)
STYLE_BOT = Style(color=PURPLE, bold=True)
STYLE_TOOL = Style(color=YELLOW)
STYLE_INTERIM = Style(color=DIM, italic=True)
STYLE_STRUCK = Style(color=DIM, strike=True)
STYLE_ERROR = Style(color=RED, bold=True)
STYLE_WARN = Style(color=YELLOW)
STYLE_OK = Style(color=GREEN)
STYLE_INFO = Style(color=FROST_BLUE)

LEVEL_STYLES = {
    "TRACE": Style(color=POLAR_3),
    "DEBUG": STYLE_DIM,
    "INFO": STYLE_OK,
    "SUCCESS": STYLE_OK,
    "WARNING": STYLE_WARN,
    "ERROR": STYLE_ERROR,
    "CRITICAL": STYLE_ERROR,
}

# Colors for the pieces of a latency breakdown, keyed by contribution key.
CONTRIBUTION_COLORS = {
    "endpointing_wait": FROST_BLUE,
    "first_request": FROST_BLUE,
    "transcription": FROST_CYAN,
    "turn_detection": FROST_DEEP,
    "llm_inference": PURPLE,
    "turn_completion": FROST_DEEP,
    "waiting_for_user": FROST_DEEP,
    "llm_tool_call": YELLOW,
    "function_handler": YELLOW,
    "sentence_aggregation": GREEN,
    "awaiting_speakable_text": GREEN,
    "speech_synthesis": ORANGE,
    "output_transport": RED,
}
CONTRIBUTION_FALLBACK = DIM

LEVEL_BLOCKS = "▁▂▃▄▅▆▇█"

# Audio meter gradient: blue when quiet, red when loud.
LEVEL_GRADIENT = [
    "#3366bb",
    "#0099cc",
    "#22ccbb",
    "#44dd88",
    "#99dd55",
    "#eedd00",
    "#ee9944",
    "#dd5533",
    "#cc2222",
]


def contribution_color(key: str) -> str:
    """Color for a latency contribution key."""
    return CONTRIBUTION_COLORS.get(key, CONTRIBUTION_FALLBACK)


def fmt_secs(value: Optional[float], digits: int = 2) -> str:
    """Format seconds compactly, or a dash when unknown."""
    if value is None:
        return "—"
    if value < 1.0:
        return f"{value * 1000:.0f} ms"
    return f"{value:.{digits}f}s"


def fmt_secs_fixed(value: Optional[float]) -> str:
    """Format seconds with two decimals and a unit, or a dash when unknown."""
    if value is None:
        return "—"
    return f"{value:.2f}s"


def fmt_clock(timestamp: Optional[float]) -> str:
    """Format a unix timestamp as local wall-clock time."""
    if timestamp is None:
        return "—"
    return datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")


def fmt_elapsed(seconds: float) -> str:
    """Format a duration as ``m:ss`` or ``h:mm:ss``."""
    seconds = int(max(seconds, 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def fmt_int(value: int) -> str:
    """Format an integer with thousands separators."""
    return f"{value:,}"


def truncate(text: str, limit: int) -> str:
    """Cut a string to ``limit`` characters with an ellipsis."""
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "…"


def level_meter(levels: Sequence[float], color: str = GREEN) -> Text:
    """Draw a small bar meter from a history of audio levels in ``[0, 1]``."""
    chars = []
    for level in levels:
        index = min(len(LEVEL_BLOCKS) - 1, max(0, int(round(level * (len(LEVEL_BLOCKS) - 1)))))
        chars.append(LEVEL_BLOCKS[index])
    return Text("".join(chars), style=Style(color=color))


def _blend(a: str, b: str, t: float) -> str:
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#{:02x}{:02x}{:02x}".format(
        round(ar + (br - ar) * t), round(ag + (bg - ag) * t), round(ab + (bb - ab) * t)
    )


def gradient_color(fraction: float) -> str:
    """Color of the level gradient at ``fraction`` in ``[0, 1]``."""
    stops = LEVEL_GRADIENT
    position = max(0.0, min(1.0, fraction)) * (len(stops) - 1)
    index = min(int(position), len(stops) - 2)
    return _blend(stops[index], stops[index + 1], position - index)


def level_bar(level: float, width: int = 12) -> Text:
    """Draw a level meter that fills with the gradient as ``level`` rises."""
    width = max(width, 1)
    filled = max(0, min(width, int(round(max(0.0, min(1.0, level)) * width))))
    text = Text()
    for i in range(width):
        if i < filled:
            text.append("█", style=Style(color=gradient_color(i / max(width - 1, 1))))
        else:
            text.append("░", style=Style(color=POLAR_1))
    return text


def bar(fraction: float, width: int, color: str, *, fill: str = "█", empty: str = "░") -> Text:
    """Draw a horizontal bar filled to ``fraction`` of ``width`` cells."""
    width = max(width, 0)
    filled = max(0, min(width, int(round(fraction * width))))
    text = Text(fill * filled, style=Style(color=color))
    if width - filled:
        text.append(empty * (width - filled), style=Style(color=POLAR_1))
    return text


def stacked_bar(parts: Iterable[tuple[float, str]], width: int, total: float) -> Text:
    """Draw a stacked bar.

    Args:
        parts: ``(seconds, color)`` pairs in order.
        width: Cells available for ``total`` seconds.
        total: Seconds that fill the full width.
    """
    text = Text()
    if total <= 0 or width <= 0:
        return text
    used = 0
    for seconds, color in parts:
        cells = int(round(seconds / total * width))
        cells = max(0, min(cells, width - used))
        if cells:
            text.append("█" * cells, style=Style(color=color))
            used += cells
    return text


def status_dot(status: str) -> Text:
    """A colored dot for a connection or worker status."""
    if status in ("connected", "running", "ready"):
        return Text("●", style=STYLE_OK)
    if status in ("connecting", "idle", "starting", "known"):
        return Text("●", style=STYLE_WARN)
    if status in ("error", "failed"):
        return Text("●", style=STYLE_ERROR)
    return Text("○", style=STYLE_DIM)
