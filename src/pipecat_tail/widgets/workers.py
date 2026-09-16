#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Workers tab: worker tree, jobs table and bus message feed."""

import json
from typing import Any, Optional

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, RichLog, Tree

from pipecat_tail.state import BusRecord, SessionState
from pipecat_tail.widgets.render import (
    FROST_BLUE,
    FROST_CYAN,
    GREEN,
    PURPLE,
    RED,
    STYLE_BRIGHT,
    STYLE_DIM,
    STYLE_ERROR,
    STYLE_LABEL,
    STYLE_OK,
    STYLE_TEXT,
    STYLE_WARN,
    YELLOW,
    fmt_clock,
    fmt_elapsed,
    status_dot,
    truncate,
)

BUS_FILTERS = ("no frames", "all", "jobs", "lifecycle")
MAX_BUS_LINES = 1000

_CATEGORY_COLORS = {
    "lifecycle": FROST_CYAN,
    "job": FROST_BLUE,
    "frame": PURPLE,
    "other": YELLOW,
}


class WorkerTree(Tree):
    """Tree of workers under their runner, with pipeline shape."""

    def __init__(self):
        """Create the tree."""
        super().__init__("runner", id="worker-tree")
        self.border_title = "Workers"
        self.show_root = True
        self.guide_depth = 3

    def sync(self, state: SessionState) -> None:
        """Rebuild the tree from the state."""
        self.clear()
        root_label = Text()
        root_label.append_text(status_dot(state.status))
        root_label.append(f" {state.runner or 'runner'}", style=STYLE_BRIGHT)
        root_label.append("  WorkerRunner", style=STYLE_DIM)
        self.root.set_label(root_label)

        names = set(state.workers) | set(state.pipeline_workers)
        infos = {name: state.workers.get(name) for name in names}
        by_parent: dict[Optional[str], list[str]] = {}
        for name, info in infos.items():
            parent = info.parent if info else None
            if parent not in infos:
                parent = None
            by_parent.setdefault(parent, []).append(name)
        for children in by_parent.values():
            children.sort()

        def add(node, name: str) -> None:
            info = infos.get(name)
            session = state.sessions.get(name)
            status = info.status if info else ("running" if session else "known")
            if session and session.finished:
                status = "finished"
            label = Text()
            label.append_text(status_dot(status))
            label.append(f" {name}", style=STYLE_BRIGHT)
            kind = "PipelineWorker" if session or (info and info.processors) else "Worker"
            label.append(f"  {kind}", style=STYLE_DIM)
            label.append(f"  {status}", style=_status_style(status))
            if name == state.selected_worker:
                label.append("  ◂ selected", style=STYLE_WARN)
            if info and info.error:
                label.append(f"  {truncate(info.error, 40)}", style=STYLE_ERROR)
            child = node.add(label, expand=True)
            processors = info.processors if info else []
            if processors:
                shape = Text(" → ".join(processors), style=STYLE_DIM)
                child.add_leaf(shape)
            for grandchild in by_parent.get(name, []):
                add(child, grandchild)

        for name in by_parent.get(None, []):
            add(self.root, name)
        self.root.expand_all()


def _status_style(status: str):
    if status in ("running", "ready"):
        return STYLE_OK
    if status in ("error", "failed"):
        return STYLE_ERROR
    if status in ("starting", "known"):
        return STYLE_WARN
    return STYLE_DIM


class JobsTable(DataTable):
    """Jobs requested over the bus, newest first."""

    def __init__(self):
        """Create the table."""
        super().__init__(id="jobs-table", cursor_type="row", zebra_stripes=True)
        self.border_title = "Jobs"

    def on_mount(self) -> None:
        """Add the columns."""
        self.add_columns("id", "name", "source", "worker", "status", "started", "elapsed", "last")

    def sync(self, state: SessionState) -> None:
        """Rebuild the rows from the state."""
        self.clear()
        jobs = sorted(state.jobs.values(), key=lambda j: j.requested_at, reverse=True)
        for job in jobs[:200]:
            status = Text(job.status, style=_job_style(job.status))
            self.add_row(
                Text(job.job_id[:8], style=STYLE_TEXT),
                job.job_name or "",
                job.source or "",
                job.target or "",
                status,
                fmt_clock(job.requested_at),
                fmt_elapsed(job.elapsed_secs),
                truncate(job.last or "", 40),
                key=job.job_id,
            )


def _job_style(status: str):
    if status == "running":
        return STYLE_OK
    if status in ("error", "failed"):
        return STYLE_ERROR
    if status == "cancelled":
        return STYLE_WARN
    return STYLE_DIM


class BusFeed(RichLog):
    """Bus messages as they happen."""

    def __init__(self):
        """Create the feed."""
        super().__init__(id="bus-feed", max_lines=MAX_BUS_LINES, wrap=False, highlight=False)
        self.border_title = "Bus messages"
        self.filter_index = 0
        self._count = 0
        self._hidden = 0
        self._update_subtitle()

    @property
    def filter_name(self) -> str:
        """The active filter."""
        return BUS_FILTERS[self.filter_index]

    def cycle_filter(self, state: SessionState) -> str:
        """Switch to the next filter and rebuild the feed."""
        self.filter_index = (self.filter_index + 1) % len(BUS_FILTERS)
        self.rebuild(state)
        return self.filter_name

    def accepts(self, record: BusRecord) -> bool:
        """Whether the record passes the active filter."""
        name = self.filter_name
        if name == "all":
            return True
        if name == "no frames":
            return record.category != "frame"
        if name == "jobs":
            return record.category == "job"
        if name == "lifecycle":
            return record.category == "lifecycle"
        return True

    def rebuild(self, state: SessionState) -> None:
        """Rewrite the feed from the state's bus records."""
        self.clear()
        self._count = 0
        self._hidden = 0
        for record in list(state.bus)[-MAX_BUS_LINES:]:
            self.append(record)
        self._update_subtitle()

    def append(self, record: BusRecord) -> None:
        """Add one record if it passes the filter."""
        if not self.accepts(record):
            self._hidden += 1
            self._update_subtitle()
            return
        self._count += 1
        self.write(_render_bus_record(record))
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        hidden = f" · {self._hidden} hidden" if self._hidden else ""
        self.border_subtitle = f"filter {self.filter_name}{hidden} · b to change"


def _render_bus_record(record: BusRecord) -> Text:
    text = Text()
    text.append(fmt_clock(record.timestamp), style=STYLE_DIM)
    text.append(f"  {record.source or '?':<12}", style=STYLE_TEXT)
    target = record.target or "broadcast"
    text.append(f"{target:<12}", style=STYLE_TEXT if record.target else STYLE_DIM)
    color = _CATEGORY_COLORS.get(record.category, YELLOW)
    if record.message_type.endswith("ErrorMessage"):
        color = RED
    elif record.message_type.endswith("ReadyMessage"):
        color = GREEN
    text.append(f"{record.name:<32}", style=f"{color}")
    text.append(_summarize_payload(record), style=STYLE_DIM)
    return text


def _summarize_payload(record: BusRecord, limit: int = 100) -> str:
    payload: dict[str, Any] = record.payload
    if record.category == "frame":
        frame = payload.get("frame") or payload.get("frame_type") or ""
        direction = payload.get("direction", "")
        return truncate(f"{frame} {direction}".strip(), limit)
    if "workers" in payload and isinstance(payload["workers"], list):
        names = [w.get("name", "?") if isinstance(w, dict) else str(w) for w in payload["workers"]]
        return truncate("workers: " + ", ".join(names), limit)
    parts = []
    for key in ("job_id", "job_name", "status", "error", "reason", "text", "worker"):
        if key in payload and payload[key] not in (None, ""):
            value = payload[key]
            if key == "job_id" and isinstance(value, str):
                value = value[:8]
            parts.append(f"{key} {value}")
    for key in ("payload", "response", "update", "data", "args"):
        if key in payload and payload[key] not in (None, {}):
            try:
                parts.append(json.dumps(payload[key], separators=(",", ":"), default=str))
            except (TypeError, ValueError):
                parts.append(str(payload[key]))
    return truncate(" · ".join(parts), limit)


class WorkersView(Vertical):
    """The Workers tab."""

    def __init__(self):
        """Create the tab."""
        super().__init__(id="workers-view")
        self.worker_tree = WorkerTree()
        self.jobs = JobsTable()
        self.bus = BusFeed()

    def compose(self):
        """Compose the tree and jobs side by side over the bus feed."""
        with Horizontal(id="workers-top"):
            yield self.worker_tree
            yield self.jobs
        yield self.bus
