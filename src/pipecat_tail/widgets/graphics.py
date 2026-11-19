#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Terminal graphics drawn with Unicode blocks.

Everything here returns Rich ``Text`` and works in any terminal with a
Unicode font: bars with eighth-block precision, solid waveforms, sparklines,
gradient meters, three-row digits and half-block images.
"""

import base64
import binascii
import functools
import math
from typing import Callable, Optional, Sequence

from rich.style import Style
from rich.text import Text

EIGHTHS = " ▏▎▍▌▋▊▉█"
SPARK = "▁▂▃▄▅▆▇█"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def hex_rgb(color: str) -> tuple[int, int, int]:
    """Parse ``#rrggbb``."""
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def rgb_hex(r: float, g: float, b: float) -> str:
    """Format a color as ``#rrggbb``."""
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, round(r))), max(0, min(255, round(g))), max(0, min(255, round(b)))
    )


def blend(a: str, b: str, t: float) -> str:
    """Mix two colors, ``t`` of the way from ``a`` to ``b``."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = hex_rgb(a)
    br, bg, bb = hex_rgb(b)
    return rgb_hex(ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t)


GRADIENT_STEPS = 64
"""Gradients are sampled in this many steps, which the eye can't tell apart."""


@functools.lru_cache(maxsize=4096)
def _gradient(stops: tuple[str, ...], step: int) -> str:
    t = step / GRADIENT_STEPS
    position = t * (len(stops) - 1)
    index = min(int(position), len(stops) - 2)
    return blend(stops[index], stops[index + 1], position - index)


def gradient(stops: Sequence[str], t: float) -> str:
    """Color at ``t`` in ``[0, 1]`` along a gradient through ``stops``.

    Drawing calls this for every cell, so colors are cached per step.
    """
    if len(stops) == 1:
        return stops[0]
    t = max(0.0, min(1.0, t))
    return _gradient(tuple(stops), round(t * GRADIENT_STEPS))


def spinner(now: float, fps: float = 10.0) -> str:
    """A braille spinner frame for the current time."""
    return SPINNER[int(now * fps) % len(SPINNER)]


def pad(text: Text, width: int) -> Text:
    """Pad or cut a text to exactly ``width`` cells."""
    text = text.copy()
    if text.cell_len < width:
        text.append(" " * (width - text.cell_len))
    elif text.cell_len > width:
        text.truncate(width)
    return text


def cells(row: Sequence[tuple[Text, int, str]], gap: int = 2) -> Text:
    """Lay out ``(text, width, justify)`` cells on one line, ``gap`` spaces apart.

    Each cell is padded or cut to its width, aligned left or right. A width of
    zero leaves the cell as it is, for a last column that takes what is left.
    """
    line = Text()
    for index, (text, width, justify) in enumerate(row):
        if index:
            line.append(" " * gap)
        if width <= 0:
            line.append_text(text)
            continue
        cell = text.copy()
        if cell.cell_len > width:
            cell.truncate(width, overflow="ellipsis")
        room = width - cell.cell_len
        if justify == "right":
            line.append(" " * room)
            line.append_text(cell)
        else:
            line.append_text(cell)
            line.append(" " * room)
    return line


def pack(items: Sequence[Text], width: int, sep: str = "   ", indent: int = 0) -> list[Text]:
    """Lay items out in lines no wider than ``width``, never splitting one."""
    lines: list[Text] = []
    line = Text(" " * indent)
    for item in items:
        if line.cell_len > indent and line.cell_len + len(sep) + item.cell_len > width:
            lines.append(line)
            line = Text(" " * indent)
        if line.cell_len > indent:
            line.append(sep)
        line.append_text(item)
    if line.cell_len > indent or not lines:
        lines.append(line)
    return lines


def fit(items: Sequence[Text], width: int, sep: Text, *, keep_last: bool = False) -> Text:
    """As many whole items as fit on one line, with an ellipsis for those left out.

    Items are kept from the start, or with ``keep_last`` from the end, for a
    list where the latest item matters most.
    """
    if width <= 0:
        return Text()
    if keep_last:
        kept: list[Text] = []
        used = 2  # room for "… "
        for item in reversed(items):
            need = item.cell_len + (sep.cell_len if kept else 0)
            if kept and used + need > width:
                break
            kept.insert(0, item)
            used += need
        if len(kept) == len(items) and sep.join(items).cell_len <= width:
            return sep.join(items)
        line = Text("… ", style=sep.style) + sep.join(kept)
        if line.cell_len > width:
            line.truncate(width, overflow="ellipsis")
        return line
    line = Text()
    for index, item in enumerate(items):
        more = index < len(items) - 1
        need = (sep.cell_len if line else 0) + item.cell_len + (2 if more else 0)
        if line.cell_len + need > width:
            if line:
                line.append(" …", style=sep.style)
            else:
                line = item.copy()
                line.truncate(width, overflow="ellipsis")
            return line
        if line:
            line.append_text(sep)
        line.append_text(item)
    return line


def overlay(base: Text, at: int, text: Text) -> Text:
    """Write ``text`` over ``base`` from cell ``at``, keeping its length."""
    length = len(base)
    at = max(0, min(length, at))
    end = min(length, at + len(text))
    if end <= at:
        return base
    return base[:at] + text[: end - at] + base[end:]


def block_wave(
    values: Sequence[float],
    rows: int,
    stops: Sequence[str],
    *,
    down: bool = False,
) -> list[Text]:
    """Solid bar envelope, one sample per cell.

    Bars grow up from the bottom with eighth-block precision, or hang down
    from the top with ``down`` (at half-block precision, the finest that
    terminals draw from the top). Color follows ``stops`` along the height.
    """
    cells: list[list[tuple[str, Optional[str]]]] = [[] for _ in range(rows)]
    for v in values:
        h = max(0.0, min(1.0, v)) * rows * 8
        for r in range(rows):
            k = r if down else rows - 1 - r
            fill = int(round(max(0.0, min(8.0, h - k * 8))))
            if fill <= 0:
                cells[r].append((" ", None))
                continue
            color = gradient(stops, min(1.0, (k + max(fill, 1) / 8) / rows))
            if fill >= 8 or (down and fill >= 6):
                char = "█"
            elif down:
                char = "▀" if fill >= 3 else "▔"
            else:
                char = SPARK[fill - 1]
            cells[r].append((char, color))
    return [runs(line) for line in cells]


@functools.lru_cache(maxsize=2048)
def fg(color: str) -> Style:
    """A foreground style, cached: drawing asks for the same few colors over and over."""
    return Style(color=color)


def runs(cells: Sequence[tuple[str, Optional[str]]]) -> Text:
    """Text from ``(character, color)`` cells, one span per run of a color."""
    text = Text()
    chars: list[str] = []
    current: Optional[str] = None
    for char, color in cells:
        if color != current and chars:
            text.append("".join(chars), style=fg(current) if current else None)
            chars = []
        current = color
        chars.append(char)
    if chars:
        text.append("".join(chars), style=fg(current) if current else None)
    return text


def stacked(
    parts: Sequence[tuple[float, str]],
    scale: float,
    width: int,
    *,
    offset: float = 0.0,
    background: Optional[str] = None,
) -> Text:
    """A stacked horizontal bar with eighth-block precision.

    Args:
        parts: ``(seconds, color)`` in order.
        scale: Seconds per cell.
        width: Cells to draw.
        offset: Seconds of empty space before the first part.
        background: Color behind the bar, needed to start it mid-cell.
    """
    cover: list[list[tuple[float, str]]] = [[] for _ in range(width)]
    acc = offset
    for secs, color in parts:
        if secs <= 0:
            continue
        a, b = acc / scale, (acc + secs) / scale
        acc += secs
        for c in range(max(0, int(a)), min(width, math.ceil(b))):
            f = max(0.0, min(c + 1, b) - max(c, a))
            if f > 0:
                cover[c].append((f, color))
    text = Text()
    for c in range(width):
        cell = cover[c]
        if not cell:
            text.append(" ")
            continue
        total = sum(f for f, _ in cell)
        starts_mid = c < offset / scale < c + 1
        if total >= 0.95 and len(cell) == 1:
            text.append("█", style=Style(color=cell[0][1]))
        elif len(cell) >= 2:
            f0, c0 = cell[0]
            n = max(1, min(7, round(f0 * 8)))
            text.append(EIGHTHS[n], style=Style(color=c0, bgcolor=cell[-1][1]))
        elif starts_mid and background:
            # Starts mid-cell: the empty left part is drawn in the background
            # color over a cell filled with the bar color.
            n = 8 - max(1, round(total * 8))
            if n:
                text.append(EIGHTHS[n], style=Style(color=background, bgcolor=cell[0][1]))
            else:
                text.append("█", style=Style(color=cell[0][1]))
        else:
            n = max(1, round(total * 8))
            text.append(EIGHTHS[n], style=Style(color=cell[0][1]))
    return text


def sparkline(
    values: Sequence[float],
    color: Callable[[float], str],
    lo: Optional[float] = None,
    hi: Optional[float] = None,
) -> Text:
    """One-row chart of values."""
    if not values:
        return Text()
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    span = (hi - lo) or 1.0
    text = Text()
    for v in values:
        f = (v - lo) / span
        text.append(SPARK[max(0, min(7, round(f * 7)))], style=Style(color=color(v)))
    return text


def meter(
    level: float,
    width: int,
    stops: Sequence[str],
    track: str,
) -> Text:
    """Horizontal level meter with eighth-block precision."""
    text = Text()
    filled = max(0.0, min(1.0, level)) * width
    for c in range(width):
        color = gradient(stops, c / max(width - 1, 1))
        f = max(0.0, min(1.0, filled - c))
        if f >= 1:
            text.append("█", style=Style(color=color))
        elif f > 0:
            text.append(EIGHTHS[max(1, round(f * 8))], style=Style(color=color, bgcolor=track))
        else:
            text.append(" ", style=Style(bgcolor=track))
    return text


BIG_DIGITS = {
    "0": ("█▀█", "█ █", "▀▀▀"),
    "1": ("▀█ ", " █ ", "▀▀▀"),
    "2": ("▀▀█", "█▀▀", "▀▀▀"),
    "3": ("▀▀█", " ▀█", "▀▀▀"),
    "4": ("█ █", "▀▀█", "  ▀"),
    "5": ("█▀▀", "▀▀█", "▀▀▀"),
    "6": ("█▀▀", "█▀█", "▀▀▀"),
    "7": ("▀▀█", "  █", "  ▀"),
    "8": ("█▀█", "█▀█", "▀▀▀"),
    "9": ("█▀█", "▀▀█", "▀▀▀"),
    ".": (" ", " ", "▀"),
    "-": ("   ", "▀▀▀", "   "),
    " ": (" ", " ", " "),
}


def big_number(
    value: str, color: str, unit: str = "", unit_color: Optional[str] = None
) -> list[Text]:
    """Draw a number three rows tall, with an optional unit on the last row."""
    rows = [Text(), Text(), Text()]
    style = Style(color=color, bold=True)
    for i, ch in enumerate(value):
        glyph = BIG_DIGITS.get(ch, BIG_DIGITS[" "])
        for r in range(3):
            rows[r].append(glyph[r], style=style)
            if i < len(value) - 1:
                rows[r].append(" ")
    if unit:
        rows[0].append(" " * (len(unit) + 1))
        rows[1].append(" " * (len(unit) + 1))
        rows[2].append(" " + unit, style=Style(color=unit_color or color, bold=True))
    return rows


def halfblock(pixels: Sequence[Sequence[tuple[int, int, int]]]) -> list[Text]:
    """Draw an RGB image with upper half blocks, two pixel rows per line."""
    lines = []
    for y in range(0, len(pixels) - 1, 2):
        text = Text()
        for x in range(len(pixels[y])):
            text.append(
                "▀", style=Style(color=rgb_hex(*pixels[y][x]), bgcolor=rgb_hex(*pixels[y + 1][x]))
            )
        lines.append(text)
    return lines


def image_lines(media: dict, max_cols: int = 32) -> list[Text]:
    """A thumbnail sent by the observer (raw RGB in base64), drawn with half blocks."""
    try:
        width = int(media["width"])
        height = int(media["height"])
        raw = base64.b64decode(media["rgb"])
    except (KeyError, TypeError, ValueError, binascii.Error):
        return []
    if width <= 0 or height <= 0 or len(raw) < width * height * 3:
        return []
    cols = min(max_cols, width)
    rows = max(2, int(height * cols / width) // 2 * 2)
    pixels = []
    for y in range(rows):
        sy = min(height - 1, int(y * height / rows))
        row = []
        for x in range(cols):
            sx = min(width - 1, int(x * width / cols))
            i = (sy * width + sx) * 3
            row.append((raw[i], raw[i + 1], raw[i + 2]))
        pixels.append(row)
    return halfblock(pixels)
