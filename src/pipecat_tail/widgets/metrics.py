#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Metrics tab: latency per service and usage, and the startup panel.

Each service metric is a row: a sparkline of its recent values in the
service's color, then the last, the median and the worst. Usage is what each
service used, the figures behind the bill.
"""

from typing import Any, Optional

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from pipecat_tail.state import WorkerSession, percentile
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.graphics import Column, Table, pack, sparkline
from pipecat_tail.widgets.render import (
    bar,
    fmt_int,
    fmt_secs,
    fmt_secs_fixed,
)

KIND_ORDER = ("ttfb", "ttfat", "ttfa", "processing")
KIND_MEANING = {
    "ttfb": "time to first byte",
    "ttfat": "to the first answer token",
    "ttfa": "to the first audio",
    "processing": "total processing",
}
SPARK_POINTS = 30
"""How many recent measurements a row's sparkline shows."""


def _services(session: WorkerSession) -> list[str]:
    """Services with metrics or usage, in pipeline order, then any others."""
    seen = {processor for processor, _ in session.metrics} | {
        processor for processor, _ in session.usage
    }
    ordered = [name for name in session.processors if name in seen]
    return ordered + sorted(seen - set(ordered))


def latency_rows(session: WorkerSession, width: int) -> Group | Text:
    """One row per service metric: a sparkline of recent values, then last, median, worst.

    Each service is named once, above its metrics, in its color. Columns come
    in order of importance and only while they fit; a number is never cut.
    """
    p = palette.current()
    if not session.metrics:
        return Text(
            "No service metrics yet. They arrive with enable_metrics=True in PipelineParams.",
            style=Style(color=p.muted),
        )
    rows: list[tuple[str, dict[str, Text]]] = []
    kinds_seen: list[str] = []
    for processor in _services(session):
        color = palette.processor_color(processor)
        kinds = [k for k in KIND_ORDER if (processor, k) in session.metrics]
        kinds += sorted(k for (proc, k) in session.metrics if proc == processor and k not in kinds)
        for kind in kinds:
            values = [m.seconds for m in session.metrics[(processor, kind)]]
            if kind == "processing" and max(values) < 0.001:
                continue
            if kind not in kinds_seen:
                kinds_seen.append(kind)
            recent = values[-SPARK_POINTS:]
            extra = session.metrics[(processor, kind)][-1].extra
            note = ""
            if extra.get("thinking_time_secs") is not None:
                note = f"thinking {fmt_secs(extra['thinking_time_secs'])}"
            elif extra.get("leading_silence_secs") is not None:
                note = f"leading silence {fmt_secs(extra['leading_silence_secs'])}"
            rows.append(
                (
                    processor,
                    {
                        "metric": Text(kind, style=Style(color=p.text)),
                        "recent": sparkline(recent, lambda v, c=color: c, lo=0.0),
                        "last": Text(fmt_secs(values[-1]), style=Style(color=p.bright, bold=True)),
                        "median": Text(
                            fmt_secs(percentile(values, 0.5)), style=Style(color=p.text)
                        ),
                        "worst": Text(fmt_secs(max(values)), style=Style(color=p.text)),
                        "n": Text(str(len(values)), style=Style(color=p.dim)),
                        "note": Text(note, style=Style(color=p.muted)),
                    },
                )
            )
    if not rows:
        # Only processing times too small to show so far.
        return Text("No service measurements yet.", style=Style(color=p.muted))
    table = Table(
        [
            Column("metric"),
            Column("last", right=True),
            # As wide as the measurements so far, up to the window; a shorter
            # sparkline beats none.
            Column("recent", optional=True, shrink=True, min_width=8, max_width=SPARK_POINTS),
            Column("median", right=True),
            Column("worst", right=True),
            Column("n", right=True),
            Column("note", title="", optional=True, free=True),
        ],
        [row for _, row in rows],
        width,
        indent=1,
    )
    line = table.line
    blocks: list[Any] = [table.header(Style(color=p.muted))]
    for processor in dict.fromkeys(proc for proc, _ in rows):
        color = palette.processor_color(processor)
        name = Text("◆ ", style=Style(color=color))
        name.append(palette.short_name(processor), style=Style(color=p.bright, bold=True))
        blocks.append(name)
        for _, row in (r for r in rows if r[0] == processor):
            blocks.append(line(row))
    key = [
        Text(kind, style=Style(color=p.text))
        + Text(f" {KIND_MEANING[kind]}", style=Style(color=p.muted))
        for kind in kinds_seen
        if kind in KIND_MEANING
    ]
    return Group(*pack(key, width, sep="   "), Text(""), *blocks)


def usage_rows(session: WorkerSession, width: int) -> Group | Text:
    """What each service used: tokens, characters, audio. The billing figures."""
    p = palette.current()
    if not session.usage:
        return Text(
            "No usage yet. It arrives with enable_usage_metrics=True in PipelineParams.",
            style=Style(color=p.muted),
        )
    lines: list[Any] = []
    for processor in _services(session):
        for (proc, kind), totals in session.usage.items():
            if proc != processor:
                continue
            color = palette.processor_color(processor)
            head = Text("◆ ", style=Style(color=color))
            head.append(palette.short_name(processor), style=Style(color=p.bright, bold=True))
            if totals.model:
                head.append(f"  {totals.model}", style=Style(color=p.muted))
            items: list[Text] = []

            def item(value: str, label: str) -> None:
                items.append(
                    Text.assemble(
                        (value, Style(color=p.bright, bold=True)),
                        (f" {label}", Style(color=p.muted)),
                    )
                )

            if kind == "llm":
                item(fmt_int(totals.prompt_tokens), "tokens in")
                item(fmt_int(totals.completion_tokens), "out")
                if totals.prompt_tokens and totals.cache_read_input_tokens:
                    share = totals.cache_read_input_tokens / totals.prompt_tokens
                    item(f"{share:.0%}", "cached")
                if totals.reasoning_tokens:
                    item(fmt_int(totals.reasoning_tokens), "reasoning")
                if totals.input_audio_tokens or totals.output_audio_tokens:
                    item(
                        f"{fmt_int(totals.input_audio_tokens)} / "
                        f"{fmt_int(totals.output_audio_tokens)}",
                        "audio tokens in / out",
                    )
                item(fmt_int(totals.count), "requests")
            elif kind == "tts":
                item(fmt_int(totals.characters), "characters")
                item(fmt_int(totals.count), "requests")
            elif kind == "stt":
                item(f"{totals.audio_seconds:.1f} s", "of audio")
            else:
                item(fmt_int(totals.count), kind)
            lines.append(head)
            lines.extend(pack(items, width, sep=" · ", indent=2))
    return Group(*lines)


class MetricsView(VerticalScroll):
    """The Metrics tab: latency per service, then usage."""

    def __init__(self):
        """Create the tab."""
        super().__init__(id="metrics-view")
        self.latency = Static(id="service-latency")
        self.usage = Static(id="usage")
        self._session: Optional[WorkerSession] = None
        self.can_focus = False

    def compose(self):
        """Lay out latency over usage."""
        yield self.latency
        yield self.usage

    def on_mount(self) -> None:
        """Title the sections."""
        self.latency.border_title = f"service latency · last {SPARK_POINTS} measurements"
        self.usage.border_title = "usage"

    def set_session(self, session: WorkerSession) -> None:
        """Scope every section to a session."""
        self._session = session
        self.refresh_view()

    def on_resize(self) -> None:
        """Fit the sections to the new width."""
        self.refresh_view()

    def refresh_view(self) -> None:
        """Re-render both sections."""
        session = self._session
        if session is None:
            return
        width = max(20, (self.size.width or 120) - 4)
        self.latency.update(latency_rows(session, width))
        self.usage.update(usage_rows(session, width))


def _startup_total(timing: dict[str, Any]) -> float:
    setup = float(timing.get("setup_duration_secs", 0.0) or 0.0)
    start = float(timing.get("start_duration_secs", 0.0) or 0.0)
    return setup + start


class StartupPanel(Static):
    """Per-processor setup time and transport milestones for the last start."""

    def __init__(self):
        """Create the panel."""
        super().__init__(Text(""), id="startup")
        self.border_title = "startup"
        self._session: Optional[WorkerSession] = None

    def set_session(self, session: WorkerSession) -> None:
        """Point the panel at a session and re-render."""
        self._session = session
        self.refresh_view()

    def on_resize(self) -> None:
        """Re-render when the width changes."""
        self.refresh_view()

    def refresh_view(self) -> None:
        """Re-render from the session."""
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        session = self._session
        report: Optional[dict[str, Any]] = session.startup if session else None
        if not report:
            text.append(
                "No startup report yet. It arrives once the pipeline has started.",
                style=palette.style("dim"),
            )
            return text
        timings = [t for t in report.get("processor_timings", []) if isinstance(t, dict)]
        # Slowest first: setup plus start is what delays the pipeline.
        timings.sort(key=_startup_total, reverse=True)
        avail = max(20, self.size.width - 2)
        name_width = min(max([len(str(t.get("processor_name", ""))) for t in timings] + [9]), 40)
        # Columns in order of importance: setup, start, then a bar.
        with_start = name_width + 20 <= avail
        with_bar = name_width + 30 <= avail
        two_lines = name_width + 10 > avail
        bar_width = avail - name_width - 22
        longest = max([float(t.get("duration_secs", 0.0) or 0.0) for t in timings] + [0.001])
        if not two_lines:
            text.append(f"{'processor':<{name_width}}  {'setup':>8}", style=palette.style("dim"))
            if with_start:
                text.append(f"  {'start':>8}", style=palette.style("dim"))
            text.append("\n")
        for timing in timings:
            name = str(timing.get("processor_name", "?"))
            setup = float(timing.get("setup_duration_secs", 0.0) or 0.0)
            start = float(timing.get("start_duration_secs", 0.0) or 0.0)
            duration = float(timing.get("duration_secs", setup + start) or 0.0)
            if two_lines:
                # Too narrow for columns: the name, then its times under it.
                text.append(f"{name}\n", style=palette.style("text"))
                text.append("  setup ", style=palette.style("dim"))
                text.append(fmt_secs(setup), style=palette.style("bright", bold=True))
                text.append("  start ", style=palette.style("dim"))
                text.append(f"{fmt_secs(start)}\n", style=palette.style("dim"))
                continue
            text.append(f"{name[:name_width]:<{name_width}}  ", style=palette.style("text"))
            text.append(f"{fmt_secs(setup):>8}", style=palette.style("bright", bold=True))
            if with_start:
                text.append(f"  {fmt_secs(start):>8}", style=palette.style("dim"))
            if with_bar:
                text.append("  ")
                text.append_text(bar(duration / longest, bar_width, palette.current().user))
            text.append("\n")
        text.append("\n")
        items: list[Text] = []

        def item(label: str, value: str, style=palette.style("bright", bold=True)) -> None:
            items.append(Text(f"{label} ", style=palette.style("dim")) + Text(value, style=style))

        item("setup phase", fmt_secs_fixed(report.get("setup_phase_secs")))
        item("start phase", fmt_secs_fixed(report.get("start_phase_secs")))
        item("total", fmt_secs_fixed(report.get("total_duration_secs")))
        warmup = report.get("warmup")
        if isinstance(warmup, dict):
            item(
                "warm-up",
                f"{fmt_secs_fixed(warmup.get('duration_secs'))} "
                f"({fmt_secs(warmup.get('blocking_duration_secs'))} blocking)",
                palette.style("text"),
            )
        transport = session.transport_timing if session else None
        if isinstance(transport, dict):
            if transport.get("bot_connected_secs") is not None:
                item("bot connected", fmt_secs_fixed(transport["bot_connected_secs"]))
            if transport.get("client_connected_secs") is not None:
                item("client connected", fmt_secs_fixed(transport["client_connected_secs"]))
        text.append_text(Text("\n").join(pack(items, avail)))
        return text
