#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Metrics tab: latency chart, usage summary and startup timing."""

from typing import Any, Optional

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, SelectionList, Static
from textual_plotext import PlotextPlot

from pipecat_tail.state import WorkerSession, percentile
from pipecat_tail.widgets.render import (
    FROST_BLUE,
    FROST_CYAN,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    STYLE_BRIGHT,
    STYLE_DIM,
    STYLE_LABEL,
    STYLE_TEXT,
    YELLOW,
    bar,
    fmt_int,
    fmt_secs,
    fmt_secs_fixed,
)

SERIES_COLORS = [PURPLE, ORANGE, FROST_CYAN, GREEN, YELLOW, FROST_BLUE, RED]
DEFAULT_KINDS = ("ttfb", "ttfa", "ttfat")
Series = tuple[str, str]


class SeriesSelector(SelectionList):
    """Which latency series the chart shows."""

    def __init__(self):
        """Create the selector."""
        super().__init__(id="series")
        self.border_title = "Series"
        self._known: list[Series] = []

    def sync(self, session: WorkerSession) -> None:
        """Add any series the session has that the list does not."""
        for series in session.metrics:
            if series in self._known:
                continue
            self._known.append(series)
            processor, kind = series
            index = len(self._known) - 1
            label = Text()
            label.append(f"{kind:<11}", style=STYLE_TEXT)
            label.append(processor, style=STYLE_DIM)
            self.add_option((label, index, kind in DEFAULT_KINDS))

    def selected_series(self) -> list[Series]:
        """The series currently ticked, in the order they were first seen."""
        return [self._known[i] for i in sorted(self.selected) if i < len(self._known)]

    def color_for(self, series: Series) -> str:
        """Stable color for a series."""
        try:
            return SERIES_COLORS[self._known.index(series) % len(SERIES_COLORS)]
        except ValueError:
            return SERIES_COLORS[0]


class LatencyChart(PlotextPlot):
    """Line chart of the selected latency series over time."""

    def __init__(self):
        """Create the chart."""
        super().__init__(id="latency-chart")
        self.border_title = "Service latency"
        self._session: Optional[WorkerSession] = None
        self._series: list[Series] = []
        self._colors: dict[Series, str] = {}

    def on_mount(self) -> None:
        """Set up the plot once."""
        self.plt.xlabel("turn")
        self.plt.ylabel("seconds")

    def set_data(
        self, session: WorkerSession, series: list[Series], colors: dict[Series, str]
    ) -> None:
        """Choose what to plot and replot."""
        self._session = session
        self._series = series
        self._colors = colors
        self.replot()

    def replot(self) -> None:
        """Redraw the chart."""
        plt = self.plt
        plt.clear_data()
        plt.clear_figure()
        plt.xlabel("measurement")
        plt.ylabel("seconds")
        session = self._session
        has_data = False
        if session is not None:
            for series in self._series:
                points = session.metrics.get(series)
                if not points:
                    continue
                ys = [p.seconds for p in points]
                xs = list(range(1, len(ys) + 1))
                processor, kind = series
                label = f"{kind} {processor}"
                plt.plot(xs, ys, marker="braille", label=label, color=self._colors.get(series))
                has_data = True
        if has_data:
            plt.ylim(0, None)
            longest = max(len(session.metrics.get(s, ())) for s in self._series) if session else 1
            if longest <= 20:
                plt.xticks(list(range(1, max(longest, 2) + 1)))
        else:
            plt.plot([0], [0], marker="dot", color=FROST_BLUE)
            plt.title("No latency metrics yet: set enable_metrics=True in PipelineParams")
        self.refresh()


class SummaryTable(DataTable):
    """Last, average and p95 of every latency series, plus usage totals."""

    def __init__(self):
        """Create the table."""
        super().__init__(id="summary-table", cursor_type="none", zebra_stripes=True)
        self.border_title = "Summary"

    def on_mount(self) -> None:
        """Add the columns."""
        self.add_columns("service", "kind", "last", "avg", "p95", "n", "detail")

    def sync(self, session: WorkerSession) -> None:
        """Rebuild the rows from a session, one service at a time, alphabetically."""
        self.clear()
        rows: list[tuple[tuple, tuple]] = []
        for (processor, kind), points in session.metrics.items():
            values = [p.seconds for p in points]
            last = values[-1]
            avg = sum(values) / len(values)
            p95 = percentile(values, 0.95)
            extra = ""
            tail = points[-1].extra
            if tail.get("thinking_time_secs") is not None:
                extra = f"thinking {fmt_secs(tail['thinking_time_secs'])}"
            elif tail.get("leading_silence_secs") is not None:
                extra = f"leading silence {fmt_secs(tail['leading_silence_secs'])}"
            model = points[-1].model
            if model:
                extra = f"{model}  {extra}".strip()
            rows.append(
                (
                    (processor.lower(), 0, kind),
                    (
                        processor,
                        kind,
                        Text(fmt_secs_fixed(last), style=STYLE_BRIGHT),
                        fmt_secs_fixed(avg),
                        fmt_secs_fixed(p95),
                        str(len(values)),
                        extra,
                    ),
                )
            )
        for (processor, kind), totals in session.usage.items():
            if kind == "llm":
                detail = (
                    f"prompt {fmt_int(totals.prompt_tokens)} · "
                    f"completion {fmt_int(totals.completion_tokens)}"
                )
                if totals.cache_read_input_tokens:
                    detail += f" · cache read {fmt_int(totals.cache_read_input_tokens)}"
                if totals.reasoning_tokens:
                    detail += f" · reasoning {fmt_int(totals.reasoning_tokens)}"
                if totals.input_audio_tokens or totals.output_audio_tokens:
                    detail += (
                        f" · audio {fmt_int(totals.input_audio_tokens)} in"
                        f" / {fmt_int(totals.output_audio_tokens)} out"
                    )
                value = fmt_int(totals.total_tokens)
                label = "tokens"
            elif kind == "tts":
                value = fmt_int(totals.characters)
                label = "characters"
                detail = totals.model or ""
            elif kind == "stt":
                value = f"{totals.audio_seconds:.1f}s"
                label = "audio"
                detail = totals.model or ""
            else:
                value = str(totals.count)
                label = kind
                detail = ""
            rows.append(
                (
                    (processor.lower(), 1, label),
                    (
                        processor,
                        label,
                        Text(value, style=STYLE_BRIGHT),
                        "",
                        "",
                        str(totals.count),
                        detail,
                    ),
                )
            )
        for _, row in sorted(rows, key=lambda item: item[0]):
            self.add_row(*row)


class StartupPanel(Static):
    """Per-processor setup time and transport milestones for the last start."""

    def __init__(self):
        """Create the panel."""
        super().__init__(Text(""), id="startup")
        self.border_title = "Startup"
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
                "No startup report yet. It arrives once the pipeline has started.", style=STYLE_DIM
            )
            return text
        timings = [t for t in report.get("processor_timings", []) if isinstance(t, dict)]
        name_width = max([len(str(t.get("processor_name", ""))) for t in timings] + [9])
        name_width = min(name_width, 40)
        bar_width = max(self.size.width - name_width - 26, 10)
        longest = max([float(t.get("duration_secs", 0.0) or 0.0) for t in timings] + [0.001])
        text.append(f"{'processor':<{name_width}}  {'setup':>8}  {'start':>8}\n", style=STYLE_LABEL)
        for timing in timings:
            name = str(timing.get("processor_name", "?"))
            setup = float(timing.get("setup_duration_secs", 0.0) or 0.0)
            start = float(timing.get("start_duration_secs", 0.0) or 0.0)
            duration = float(timing.get("duration_secs", setup + start) or 0.0)
            text.append(f"{name[:name_width]:<{name_width}}  ", style=STYLE_TEXT)
            text.append(f"{fmt_secs(setup):>8}  ", style=STYLE_BRIGHT)
            text.append(f"{fmt_secs(start):>8}  ", style=STYLE_DIM)
            text.append_text(bar(duration / longest, bar_width, FROST_BLUE, empty=" "))
            text.append("\n")
        text.append("\n")
        text.append("setup phase ", style=STYLE_LABEL)
        text.append(fmt_secs_fixed(report.get("setup_phase_secs")), style=STYLE_BRIGHT)
        text.append("   start phase ", style=STYLE_LABEL)
        text.append(fmt_secs_fixed(report.get("start_phase_secs")), style=STYLE_BRIGHT)
        text.append("   total ", style=STYLE_LABEL)
        text.append(fmt_secs_fixed(report.get("total_duration_secs")), style=STYLE_BRIGHT)
        warmup = report.get("warmup")
        if isinstance(warmup, dict):
            text.append("   warm-up ", style=STYLE_LABEL)
            text.append(fmt_secs_fixed(warmup.get("duration_secs")), style=STYLE_TEXT)
            text.append(
                f" ({fmt_secs(warmup.get('blocking_duration_secs'))} blocking)", style=STYLE_DIM
            )
        transport = session.transport_timing if session else None
        if isinstance(transport, dict):
            if transport.get("bot_connected_secs") is not None:
                text.append("   bot connected ", style=STYLE_LABEL)
                text.append(fmt_secs_fixed(transport["bot_connected_secs"]), style=STYLE_BRIGHT)
            if transport.get("client_connected_secs") is not None:
                text.append("   client connected ", style=STYLE_LABEL)
                text.append(fmt_secs_fixed(transport["client_connected_secs"]), style=STYLE_BRIGHT)
        return text


class MetricsView(Vertical):
    """The Metrics tab."""

    def __init__(self):
        """Create the tab."""
        super().__init__(id="metrics-view")
        self.selector = SeriesSelector()
        self.chart = LatencyChart()
        self.summary = SummaryTable()
        self.startup = StartupPanel()
        self._session: Optional[WorkerSession] = None

    def compose(self):
        """Compose the chart beside the selector, then summary and startup."""
        with Horizontal(id="metrics-top"):
            yield self.chart
            yield self.selector
        yield self.summary
        yield self.startup

    def set_session(self, session: WorkerSession) -> None:
        """Scope every panel to a session."""
        self._session = session
        self.startup.set_session(session)
        self.refresh_view()

    def refresh_view(self) -> None:
        """Re-render the chart and the summary."""
        session = self._session
        if session is None:
            return
        self.selector.sync(session)
        series = self.selector.selected_series()
        colors = {s: self.selector.color_for(s) for s in series}
        self.chart.set_data(session, series, colors)
        self.summary.sync(session)

    def refresh_startup(self) -> None:
        """Re-render the startup panel."""
        self.startup.refresh_view()

    def on_selection_list_selected_changed(self, _event) -> None:
        """Replot when the ticked series change."""
        self.refresh_view()
