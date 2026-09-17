#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Tail Textual application.

``TailApp`` receives wire messages through ``handle_message()``, folds them
into a ``SessionState`` and refreshes the widgets that show what changed. A
persistent strip under the header answers the questions that matter on every
tab; five tabs hold the detail: Conversation, Latency, Workers, Metrics and
Logs.

The app knows nothing about where messages come from. The standalone CLI feeds
it from a websocket or a session file, and ``TailRunner`` from a queue.
"""

from typing import Any, Awaitable, Callable, Optional

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from pipecat_tail.session import SessionWriter
from pipecat_tail.state import (
    BUS,
    CONVERSATION,
    ERRORS,
    JOBS,
    LATENCY,
    LOGS,
    METRICS,
    STARTUP,
    STATUS,
    STRIP,
    WORKERS,
    SessionState,
)
from pipecat_tail.widgets.conversation import ConversationView
from pipecat_tail.widgets.latency import LatencyView
from pipecat_tail.widgets.logs import LogsView
from pipecat_tail.widgets.metrics import MetricsView
from pipecat_tail.widgets.strip import ErrorBanner, TopStrip
from pipecat_tail.widgets.workers import WorkersView

Hook = Callable[[], Awaitable[None]]

TABS = ("conversation", "latency", "workers", "metrics", "logs")
FLUSH_INTERVAL_SECS = 0.05


class TailApp(App):
    """Main Textual application."""

    CSS_PATH = "tail.tcss"
    TITLE = "Tail"
    SUB_TITLE = "A terminal dashboard for Pipecat"

    BINDINGS = [
        Binding("1", "show_tab('conversation')", "Conversation"),
        Binding("2", "show_tab('latency')", "Latency"),
        Binding("3", "show_tab('workers')", "Workers"),
        Binding("4", "show_tab('metrics')", "Metrics"),
        Binding("5", "show_tab('logs')", "Logs"),
        Binding("f", "follow", "Follow"),
        Binding("slash", "search", "Search", key_display="/"),
        Binding("l", "level", "Level"),
        Binding("b", "bus_filter", "Bus filter"),
        Binding("e", "next_error", "Error"),
        Binding("x", "dismiss_error", "Dismiss", show=False),
        Binding("w", "next_worker", "Worker"),
        Binding("s", "save", "Save"),
        Binding("c", "connect", "Connect"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        on_mount: Optional[Hook] = None,
        on_shutdown: Optional[Hook] = None,
        action_connect: Optional[Hook] = None,
        save_path: Optional[str] = None,
    ):
        """Initialize the app.

        Args:
            on_mount: Called when the UI has mounted.
            on_shutdown: Called before the app quits.
            action_connect: Called when the user asks to (re)connect.
            save_path: Session file to record to from the start.
        """
        super().__init__()
        self._on_mount = on_mount
        self._on_shutdown = on_shutdown
        self._action_connect = action_connect
        self._save_path = save_path

        self.state = SessionState()
        self._writer: Optional[SessionWriter] = None
        self._dirty: set[str] = set()
        self._workers_dirty_full = False
        self._new_bus: list = []
        self._new_logs: list = []
        self._error_cursor = 0

        self.strip = TopStrip()
        self.banner = ErrorBanner()
        self.conversation_view = ConversationView()
        self.latency_view = LatencyView()
        self.workers_view = WorkersView()
        self.metrics_view = MetricsView()
        self.logs_view = LogsView()

    #
    # Layout and lifecycle
    #

    def compose(self) -> ComposeResult:
        """Compose the header, strip, banner, tabs and footer."""
        yield Header(show_clock=True)
        yield self.banner
        yield self.strip
        with TabbedContent(initial="conversation", id="tabs"):
            with TabPane("1 Conversation", id="conversation"):
                yield self.conversation_view
            with TabPane("2 Latency", id="latency"):
                yield self.latency_view
            with TabPane("3 Workers", id="workers"):
                yield self.workers_view
            with TabPane("4 Metrics", id="metrics"):
                yield self.metrics_view
            with TabPane("5 Logs", id="logs"):
                yield self.logs_view
        yield Footer()

    async def on_mount(self) -> None:
        """Apply the theme, bind widgets to the state and start flushing."""
        self.theme = "nord"
        self.logs_view.bind_state(self.state)
        self._bind_session()
        self.strip.update_from(self.state)
        self.set_interval(FLUSH_INTERVAL_SECS, self._flush_dirty)
        if self._save_path:
            self._start_saving(self._save_path)
        if self._on_mount:
            await self._on_mount()

    async def action_quit(self) -> None:
        """Run the shutdown hook, close the session file and quit."""
        if self._on_shutdown:
            await self._on_shutdown()
        if self._writer:
            self._writer.close()
            self._writer = None
        await super().action_quit()

    def _bind_session(self) -> None:
        session = self.state.selected
        self.conversation_view.sync(session)
        self.latency_view.set_session(session)
        self.metrics_view.set_session(session)

    #
    # Messages
    #

    async def handle_message(self, message: dict[str, Any]) -> None:
        """Fold one wire message into the state and schedule a refresh.

        Args:
            message: A decoded wire message.
        """
        if self._writer:
            self._writer.write(message)
        before_bus = len(self.state.bus)
        before_logs = len(self.state.logs)
        before_workers = set(self.state.sessions)
        areas = self.state.apply(message)
        if not areas:
            return
        if BUS in areas and len(self.state.bus) > before_bus:
            self._new_bus.append(self.state.bus[-1])
        if LOGS in areas and len(self.state.logs) > before_logs:
            self._new_logs.append(self.state.logs[-1])
        if set(self.state.sessions) != before_workers:
            areas.add(WORKERS)
            areas.add(STRIP)
            if len(before_workers) == 0 or self.state.selected_worker not in before_workers:
                self._bind_session()
        self._dirty |= areas

    async def handle_status(self, status: str, detail: Optional[str] = None) -> None:
        """Update the connection status shown in the strip.

        Args:
            status: ``connecting``, ``connected``, ``disconnected`` or ``error``.
            detail: Optional detail, for example the error text.
        """
        self._dirty |= self.state.set_status(status, detail)

    def _flush_dirty(self) -> None:
        if not self._dirty:
            return
        dirty, self._dirty = self._dirty, set()
        session = self.state.selected
        if self._writer:
            self.strip.set_recording(str(self._writer.path), self._writer.count)
        if STRIP in dirty or STATUS in dirty:
            self.strip.update_from(self.state)
        if ERRORS in dirty:
            self.banner.update_from(self.state)
        if CONVERSATION in dirty:
            self.conversation_view.sync(session)
        if LATENCY in dirty:
            self.latency_view.refresh_view()
        if METRICS in dirty:
            self.metrics_view.refresh_view()
        if STARTUP in dirty:
            self.metrics_view.refresh_startup()
        if WORKERS in dirty or STATUS in dirty:
            self.workers_view.worker_tree.sync(self.state)
        if JOBS in dirty:
            self.workers_view.jobs.sync(self.state)
        if BUS in dirty:
            for record in self._new_bus:
                self.workers_view.bus.append(record)
            self._new_bus.clear()
        if LOGS in dirty:
            for record in self._new_logs:
                self.logs_view.append(record)
            self._new_logs.clear()
        if LOGS in dirty or ERRORS in dirty:
            self.logs_view.refresh_pinned()

    #
    # Actions
    #

    def action_show_tab(self, tab: str) -> None:
        """Switch to a tab by id."""
        self.query_one("#tabs", TabbedContent).active = tab

    @property
    def active_tab(self) -> str:
        """Id of the active tab."""
        return self.query_one("#tabs", TabbedContent).active

    def action_follow(self) -> None:
        """Toggle auto-scroll on the conversation or the logs."""
        if self.active_tab == "logs":
            following = self.logs_view.toggle_follow()
        else:
            following = self.conversation_view.toggle_follow()
        self.notify("Following" if following else "Paused", timeout=1.5)

    def action_search(self) -> None:
        """Open the log search box."""
        self.action_show_tab("logs")
        self.logs_view.open_search()

    def action_level(self) -> None:
        """Cycle the minimum log level."""
        self.action_show_tab("logs")
        level = self.logs_view.cycle_level()
        self.notify(f"Log level {level}", timeout=1.5)

    def action_bus_filter(self) -> None:
        """Cycle the bus feed filter."""
        self.action_show_tab("workers")
        name = self.workers_view.bus.cycle_filter(self.state)
        self.notify(f"Bus filter: {name}", timeout=1.5)

    def action_next_error(self) -> None:
        """Jump to the logs and show the next error."""
        errors = self.state.errors
        if not errors:
            self.notify("No errors", timeout=1.5)
            return
        self._error_cursor = (self._error_cursor + 1) % len(errors)
        error = errors[self._error_cursor]
        self.action_show_tab("logs")
        self.logs_view.refresh_pinned()
        self.notify(
            f"{error.processor}: {error.message}",
            title=f"Error {self._error_cursor + 1} of {len(errors)} · {error.category}",
            severity="error",
            timeout=6,
        )

    def action_dismiss_error(self) -> None:
        """Hide the error banner."""
        self.state.dismiss_errors()
        self.banner.update_from(self.state)

    def action_next_worker(self) -> None:
        """Scope the views to the next pipeline worker."""
        name = self.state.select_next_worker()
        if name is None:
            self.notify("No pipeline workers yet", timeout=1.5)
            return
        self._bind_session()
        self._dirty |= {STRIP, WORKERS, LATENCY, METRICS, STARTUP, CONVERSATION}
        self.notify(f"Worker {name}", timeout=1.5)

    def action_save(self) -> None:
        """Start or stop recording the session to a file."""
        if self._writer:
            path = self._writer.path
            count = self._writer.count
            self._writer.close()
            self._writer = None
            self.strip.set_recording(None)
            self.notify(f"Saved {count} messages to {path}", timeout=4)
            return
        import datetime

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self._start_saving(f"tail-session-{stamp}.jsonl")

    def _start_saving(self, path: str) -> None:
        try:
            self._writer = SessionWriter(path)
        except OSError as e:
            self.notify(f"Unable to write {path}: {e}", severity="error")
            return
        self.strip.set_recording(path, 0)
        self.notify(f"Recording to {path} (s to stop)", timeout=4)

    async def action_connect(self) -> None:
        """Ask the host to (re)connect."""
        if self._action_connect:
            await self._action_connect()

    def on_key(self, event: events.Key) -> None:
        """Close the log search on Escape."""
        if event.key == "escape" and self.logs_view.search.has_focus:
            self.logs_view.close_search()
            event.stop()
