#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Session recording and replay.

A session file is JSON Lines: one wire message per line, wrapped as
``{"t": <unix seconds>, "message": {...}}``. Recording every message as it
arrives means a replay reproduces exactly what the app saw, with the
original timing.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional, TextIO


class SessionWriter:
    """Appends wire messages to a session file."""

    def __init__(self, path: str | Path):
        """Open the session file for writing.

        Args:
            path: File to write. Created or truncated.
        """
        self._path = Path(path)
        self._file: Optional[TextIO] = self._path.open("w", encoding="utf-8")
        self._count = 0

    @property
    def path(self) -> Path:
        """The file being written."""
        return self._path

    @property
    def count(self) -> int:
        """Messages written so far."""
        return self._count

    def write(self, message: dict[str, Any], *, t: Optional[float] = None) -> None:
        """Append one message.

        Args:
            message: The wire message.
            t: Timestamp to record. Defaults to now.
        """
        if not self._file:
            return
        line = json.dumps(
            {"t": t if t is not None else time.time(), "message": message},
            separators=(",", ":"),
            default=str,
        )
        self._file.write(line + "\n")
        self._count += 1

    def flush(self) -> None:
        """Flush pending writes to disk."""
        if self._file:
            self._file.flush()

    def close(self) -> None:
        """Close the file."""
        if self._file:
            self._file.close()
            self._file = None


def read_session(path: str | Path) -> list[tuple[float, dict[str, Any]]]:
    """Load every message of a session file.

    Args:
        path: The session file.

    Returns:
        List of ``(timestamp, message)`` pairs in file order. Malformed lines
        are skipped.
    """
    entries: list[tuple[float, dict[str, Any]]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message")
            if isinstance(message, dict):
                entries.append((float(entry.get("t", 0.0)), message))
    return entries


async def replay_session(
    path: str | Path, *, speed: float = 1.0, max_gap_secs: float = 5.0
) -> AsyncIterator[dict[str, Any]]:
    """Yield the messages of a session file with their original timing.

    Args:
        path: The session file.
        speed: Playback speed multiplier. ``0`` yields everything at once.
        max_gap_secs: Longest pause reproduced between two messages.
    """
    previous: Optional[float] = None
    for t, message in read_session(path):
        if previous is not None and speed > 0:
            gap = min(max(t - previous, 0.0), max_gap_secs) / speed
            if gap > 0:
                await asyncio.sleep(gap)
        previous = t
        yield message
