#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The pipeline as a flow: every processor in order, with what moves through it.

Each processor shows the frames it pushes per second, its latest time to
first byte and the trend of both, its errors, and whether it is working right
now (the LLM generating, the TTS synthesizing, the STT hearing the user). A
stage that stopped passing frames stands out.
"""

import time
from typing import Optional

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual.widget import Widget

from pipecat_tail.state import FlowStats, SessionState, WorkerSession
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.graphics import pad, sparkline
from pipecat_tail.widgets.render import fmt_secs

KIND_NAMES = {
    "audio": "audio",
    "image": "video",
    "data": "data",
    "control": "ctrl",
    "system": "sys",
}


def _rates(rates: dict[str, float]) -> str:
    # Audio flows through every processor all the time; what else moves is
    # the interesting part, so audio shows only when it is all there is.
    parts = []
    for kind in ("image", "data", "control", "system"):
        value = rates.get(kind, 0.0)
        if value >= 0.5:
            parts.append(f"{KIND_NAMES[kind]} {value:.0f}/s")
    if not parts and rates.get("audio", 0.0) >= 0.5:
        parts.append(f"audio {rates['audio']:.0f}/s")
    return " · ".join(parts)


def _busy(session: WorkerSession, name: str) -> Optional[str]:
    if "LLM" in name and "Aggregator" not in name and session.llm_active:
        return "generating"
    if "TTS" in name and session.tts_active:
        return "synthesizing"
    if "STT" in name and session.user_speaking:
        return "listening"
    return None


def processor_rows(
    state: SessionState, session: WorkerSession, width: int, *, edges: bool
) -> list[Text]:
    """Rows for every processor of a session, in pipeline order."""
    p = palette.current()
    names = list(session.processors) or list(session.flow)
    now = time.time()
    errors: dict[str, int] = {}
    for error in state.errors_for(session):
        errors[error.processor] = errors.get(error.processor, 0) + 1
    lines: list[Text] = []
    last_flow = ""
    for index, name in enumerate(names):
        stats: FlowStats = session.flow.get(name) or FlowStats()
        color = palette.processor_color(name)
        service = color != p.dim
        active = stats.out_total > 0
        left = Text(" ")
        pulse = "●" if active and int(now * 4) % 2 == 0 else ("○" if active else "·")
        left.append(pulse + " ", style=Style(color=color if active else p.border))
        left.append("◆ " if service else "◇ ", style=Style(color=color, bold=service))
        left.append(
            palette.short_name(name),
            style=Style(color=p.bright if service else p.dim, bold=service),
        )
        base = left.copy()
        busy = _busy(session, name)
        if busy:
            left.append(f"  ◉ {busy}", style=Style(color=color, bold=True))
        if errors.get(name):
            left.append(f"  ✕ {errors[name]}", style=Style(color=p.err, bold=True))

        right = Text()
        ttfb = session.metrics.get((name, "ttfb"))
        if ttfb:
            last = ttfb[-1].seconds
            right.append("ttfb ", style=Style(color=p.muted))
            right.append(fmt_secs(last), style=Style(color=color, bold=True))
            values = [m.seconds for m in list(ttfb)[-10:]]
            right.append("  ")
            right.append_text(pad(sparkline(values, lambda v, c=color: c, lo=0.0), 10))
        elif "Transport" in name:
            rates = stats.out_rates if "Input" in name else stats.in_rates
            audio = rates.get("audio", 0.0)
            right.append("▶ " if audio else "▷ ", style=Style(color=color if audio else p.muted))
            right.append(f"audio {audio:.0f}/s", style=Style(color=p.dim if audio else p.muted))
            history = list(stats.history)[-10:]
            if history:
                right.append("  ")
                right.append_text(pad(sparkline(history, lambda v, c=color: c, lo=0.0), 10))
        gap = width - left.cell_len - right.cell_len
        if gap < 1 and busy:
            # Short on room: keep the name, mark the busy processor with its dot.
            left = base
            left.append(" ◉", style=Style(color=color, bold=True))
            if errors.get(name):
                left.append(f" ✕ {errors[name]}", style=Style(color=p.err, bold=True))
            gap = width - left.cell_len - right.cell_len
        below: Optional[Text] = None
        if gap < 1 and right:
            # Still short: the name keeps its line and the figures go under it.
            below, right = right, Text()
            gap = width - left.cell_len
        if gap < 1:
            left.truncate(max(1, width - 1), overflow="ellipsis")
            gap = 1
        lines.append(left + Text(" " * max(gap, 1)) + right)

        if edges and index < len(names) - 1:
            flow = _rates(stats.out_rates)
            edge = Text("   ")
            edge.append("┃" if active else "│", style=Style(color=color if active else p.border))
            if below is not None:
                edge.append(" " * max(1, width - edge.cell_len - below.cell_len))
                edge.append_text(below)
            elif flow and flow != last_flow:
                # The same rate on every edge says nothing; only a change does.
                edge.append("  " + flow, style=Style(color=p.muted))
            last_flow = flow
            lines.append(edge)
        elif below is not None:
            lines.append(Text(" " * max(5, width - below.cell_len)) + below)
    return lines


class PipelineFlow(Widget):
    """The processors of the selected worker as a vertical flow."""

    DEFAULT_CSS = """
    PipelineFlow {
        height: 1fr;
        border-subtitle-color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, state: SessionState, *, id: str = "flow"):
        """Create the flow."""
        super().__init__(id=id)
        self.state = state
        self.border_title = "pipeline"

    def get_content_height(self, container, viewport, width: int) -> int:
        """Two lines per processor, the frame count and room for figures under a name."""
        names = list(self.state.selected.processors) or list(self.state.selected.flow)
        return max(1, len(names) * 2 + 3)

    def render(self):
        """Draw the flow."""
        p = palette.current()
        session = self.state.selected
        width = max(20, self.size.width)
        height = max(1, self.size.height)
        names = list(session.processors) or list(session.flow)
        if not names:
            return Text("Processors appear when the pipeline starts.", style=Style(color=p.muted))
        edges = len(names) * 2 - 1 <= height - 2
        lines = processor_rows(self.state, session, width, edges=edges)
        total = sum(s.out_total for s in session.flow.values())
        errors = len(self.state.errors_for(session))
        footer = Text(" frames ", style=Style(color=p.muted))
        footer.append(f"{total:,.0f}/s", style=Style(color=p.bright, bold=True))
        footer.append("   errors ", style=Style(color=p.muted))
        footer.append(str(errors), style=Style(color=p.err if errors else p.bright, bold=True))
        if len(lines) < height - 1:
            lines.append(Text(""))
            lines.append(footer)
        self.border_title = f"pipeline · {len(names)} processors"
        return Group(*lines[: max(1, height)])
