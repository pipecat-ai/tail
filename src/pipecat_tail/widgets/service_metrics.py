#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Textual widgets for plotting and summarizing service metrics (e.g., TTFB)."""

import time
from typing import List

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static
from textual_plotext import PlotextPlot


class MetricsPlot(PlotextPlot):
    """Lightweight plot for streaming metric values using plotext."""

    def __init__(self, title: str):
        """Initialize the plot with a title and internal buffers.

        Args:
            title: Title displayed at the top of the plot.
        """
        super().__init__()
        self._title = title
        self._time: List[float] = []
        self._values: List[float] = []
        self._last_time = time.time()

    def on_mount(self) -> None:
        """Set the plot title when the widget mounts."""
        self.plt.title(self._title)

    def replot(self) -> None:
        """Recompute and render the plot with current data buffers."""
        self.plt.clear_data()
        self.plt.ylim(0, 10.0)
        self.plt.yticks(list(range(0, 11, 2)))
        self.plt.xticks([])
        self.plt.plot(self._time, self._values, marker="braille")
        self.refresh()

    def add_value(self, value: float):
        """Append a new metric value and refresh the plot.

        Args:
            value: The metric value to append.
        """
        self._values.append(value)
        self._time.append(time.time() - self._last_time)
        self.replot()


class ServiceMetrics(Static):
    """Container widget that shows a metrics plot and live summary stats."""

    def __init__(self, title: str):
        """Initialize the metrics view.

        Args:
            title: Title displayed in the embedded plot.
        """
        super().__init__()
        self._plot = MetricsPlot(title)
        self._label = Label("Min: 0.00 - Max: 0.00 - Avg.: 0.00")
        self._values = []

    def compose(self) -> ComposeResult:
        """Compose the metrics plot and summary label."""
        with Vertical():
            yield self._plot
            yield self._label

    def add_value(self, value: float):
        """Add a value to the series and update summary statistics.

        Args:
            value: New metric value to record.
        """
        self._values.append(value)
        self._plot.add_value(value)
        self._update_values()

    def _update_values(self):
        """Compute min/max/avg over recorded values and refresh the label."""
        min_ttfb = min(self._values)
        max_ttfb = max(self._values)
        avg_ttfb = sum(self._values) / len(self._values) if self._values else 0.0
        self._label.update(f"Min: {min_ttfb:.2f} - Max: {max_ttfb:.2f} - Avg.: {avg_ttfb:.2f}")
