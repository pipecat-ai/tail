#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Rendering helpers shared by the Tail widgets."""

import datetime
from typing import Iterable, Optional, Sequence

from rich.style import Style
from rich.text import Text

from pipecat_tail.widgets import palette

# Tail, dark
POLAR_1 = "#19212c"
POLAR_3 = "#2f3b4e"
DIM = "#8592a6"
SNOW = "#c9d3e0"
BRIGHT = "#f2f6fb"
FROST_CYAN = "#4cc2ff"
FROST_BLUE = "#7fd8ff"
FROST_DEEP = "#5b8def"
RED = "#ff5a6a"
ORANGE = "#ff9d5c"
YELLOW = "#ffc857"
GREEN = "#4be08a"
PURPLE = "#b18cff"

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
    """Format seconds as ``340 ms`` or ``1.26 s``, or a dash when unknown."""
    if value is None:
        return "—"
    if value < 1.0:
        return f"{value * 1000:.0f} ms"
    return f"{value:.{digits}f} s"


def fmt_secs_fixed(value: Optional[float]) -> str:
    """Format seconds with two decimals and a unit, or a dash when unknown."""
    if value is None:
        return "—"
    return f"{value:.2f} s"


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


def fmt_compact(value: float) -> str:
    """Format a count compactly: ``980``, ``12.4k``, ``3.1M``."""
    value = float(value or 0)
    if value < 1000:
        return f"{value:.0f}"
    if value < 1_000_000:
        return f"{value / 1000:.1f}k"
    return f"{value / 1_000_000:.1f}M"


def fmt_latency_digits(seconds: float) -> tuple[str, str]:
    """Split a latency into digits and unit for the large readout."""
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}", "ms"
    if seconds < 10.0:
        return f"{seconds:.2f}", "s"
    return f"{seconds:.1f}", "s"


def fmt_int(value: int) -> str:
    """Format an integer with thousands separators."""
    return f"{value:,}"


def fit_hints(hints: list[str], width: int, sep: str = " · ") -> str:
    """As many whole hints as fit in ``width`` characters, joined by ``sep``."""
    shown: list[str] = []
    for hint in hints:
        if len(sep.join([*shown, hint])) > width:
            break
        shown.append(hint)
    return sep.join(shown)


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
        return Text("●", style=palette.style("ok"))
    if status in ("connecting", "idle", "starting", "known"):
        return Text("●", style=palette.style("warn"))
    if status in ("error", "failed"):
        return Text("●", style=palette.style("err", bold=True))
    return Text("○", style=palette.style("dim"))
