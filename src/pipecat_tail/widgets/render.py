#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Rendering helpers shared by the Tail widgets."""

import datetime
from typing import Optional

from rich.style import Style
from rich.text import Text

from pipecat_tail.widgets import palette


def fmt_secs(value: Optional[float]) -> str:
    """Format seconds as ``340 ms`` or ``1.26 s``, or a dash when unknown."""
    if value is None:
        return "—"
    if value < 1.0:
        return f"{value * 1000:.0f} ms"
    return f"{value:.2f} s"


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


def bar(fraction: float, width: int, color: str) -> Text:
    """Draw a horizontal bar filled to ``fraction`` of ``width`` cells."""
    width = max(width, 0)
    filled = max(0, min(width, int(round(fraction * width))))
    return Text("█" * filled, style=Style(color=color)) + Text(" " * (width - filled))


_STATUS_ROLES = {
    "connected": "ok",
    "running": "ok",
    "ready": "ok",
    "connecting": "warn",
    "idle": "warn",
    "starting": "warn",
    "known": "warn",
    "cancelled": "warn",
    "error": "err",
    "failed": "err",
}


def status_style(status: str) -> Style:
    """How a connection, worker or job status is drawn: green going, amber in between, red failed."""
    role = _STATUS_ROLES.get(status, "dim")
    return palette.style(role, bold=role == "err")


def status_dot(status: str) -> Text:
    """A colored dot for a status, hollow once it's done."""
    return Text("●" if status in _STATUS_ROLES else "○", style=status_style(status))
