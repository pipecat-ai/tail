#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The Tail Textual application.

``TailApp`` receives wire messages through ``handle_message()``, folds them
into a ``SessionState`` and refreshes the widgets that show what changed.
The status line, the HUD and the key line stay on every view; five views
hold the detail:

1. Live: the voice timeline over the conversation, every turn, following
   the latest; ``n``, ``p`` and ``/`` look back through it. Before a bot connects,
   the waiting screen.
2. Latency: where the time goes, turn by turn.
3. Pipeline: processors and frame flow, startup timing, workers, jobs and
   the bus.
4. Metrics: service latency and usage.
5. Logs.

The app knows nothing about where messages come from. The standalone CLI feeds
it from a websocket or the demo, and ``TailRunner`` from a queue.
"""

import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Input

from pipecat_tail.state import (
    BUS,
    CONVERSATION,
    ERRORS,
    FLOW,
    JOBS,
    LATENCY,
    LOGS,
    METRICS,
    STARTUP,
    STATUS,
    STRIP,
    THINKING,
    VOICE,
    WORKERS,
    SessionState,
    Turn,
)
from pipecat_tail.widgets import palette
from pipecat_tail.widgets.flow import PipelineFlow
from pipecat_tail.widgets.hud import ErrorBanner, Hud, KeyBar, TitleBar
from pipecat_tail.widgets.inspector import TurnInspector
from pipecat_tail.widgets.latency import LatencyView
from pipecat_tail.widgets.logs import LogsView
from pipecat_tail.widgets.metrics import MetricsView, StartupPanel
from pipecat_tail.widgets.splash import HelpScreen, Splash
from pipecat_tail.widgets.turns import ConversationView, TurnBlock
from pipecat_tail.widgets.voice import VoiceTimeline
from pipecat_tail.widgets.workers import BusFeed, JobsTable, WorkerTree

Hook = Callable[[], Awaitable[None]]

VIEW_IDS = {
    "live": "live",
    "latency": "latency-view",
    "pipeline": "pipeline",
    "metrics": "metrics-view",
    "logs": "logs-view",
}

FLUSH_INTERVAL_SECS = 0.05
TICK_INTERVAL_SECS = 1 / 12
SLOW_TICK_SECS = 0.5
SPLASH_FRAME_SECS = 0.2
FLOW_FRAME_SECS = 0.25


class LiveView(Vertical):
    """The waiting screen, or the voice timeline over the conversation.

    Looking back (following paused) or once the call is over, the timeline
    keeps only its lanes, so the conversation has the room.
    """

    DEFAULT_CSS = """
    LiveView { height: 1fr; }
    LiveView #live-body { height: 1fr; }
    LiveView #live-conversation { height: 1fr; }
    LiveView #live-search { height: auto; }
    """

    def __init__(self, state: SessionState, url: str, demo_hint: bool):
        """Create the view."""
        super().__init__(id="live")
        self.splash = Splash(state, url, demo_hint=demo_hint)
        self.voice = VoiceTimeline(state)
        self.conversation = ConversationView(
            id="live-conversation",
            now=state.now,
            errors=lambda: state.errors,
        )
        self.search = Input(
            placeholder="search what was said, Enter to find, Esc to close", id="live-search"
        )

    def compose(self) -> ComposeResult:
        """Lay out the view."""
        yield self.splash
        with Vertical(id="live-body"):
            yield self.voice
            yield self.conversation
            yield self.search

    def on_mount(self) -> None:
        """Start on the waiting screen."""
        self.show_splash(True)
        self.search.display = False

    def show_splash(self, show: bool) -> None:
        """Switch between the waiting screen and the live panels."""
        self.splash.display = show
        self.query_one("#live-body").display = not show

    def set_looking_back(self, looking_back: bool) -> None:
        """Keep only the timeline's lanes while looking back or after the call."""
        self.set_class(looking_back, "-looking-back")

    def open_search(self) -> None:
        """Show the search box with the current search in it."""
        self.search.value = self.conversation.search_text or ""
        self.search.display = True
        self.search.focus()

    def close_search(self) -> None:
        """Hide the search box; the search stays until ``esc`` ends it."""
        self.search.display = False
        self.app.set_focus(None)


class PipelineView(Vertical):
    """Processors and their flow, startup timing, workers, jobs and the bus."""

    DEFAULT_CSS = """
    PipelineView { height: 1fr; }
    PipelineView #pipeline-top { height: 3fr; }
    PipelineView #pipeline-flow { width: 3fr; }
    PipelineView #pipeline-side { width: 2fr; }
    PipelineView #startup { height: auto; max-height: 50%; }
    PipelineView #worker-tree { height: 1fr; }
    PipelineView #jobs-table { height: 1fr; }
    PipelineView #bus-feed { height: 2fr; }
    """

    def __init__(self, state: SessionState):
        """Create the view."""
        super().__init__(id="pipeline")
        self.flow = PipelineFlow(state, id="pipeline-flow")
        self.startup = StartupPanel()
        self.worker_tree = WorkerTree()
        self.jobs = JobsTable()
        # Shown once there are jobs: an empty table says nothing.
        self.jobs.display = False
        self.bus = BusFeed()

    def compose(self) -> ComposeResult:
        """Lay out the view."""
        with Horizontal(id="pipeline-top"):
            yield self.flow
            with Vertical(id="pipeline-side"):
                yield self.startup
                yield self.worker_tree
                yield self.jobs
        yield self.bus


def _newest(records: Sequence[Any]) -> Any:
    return records[-1] if records else None


class TailApp(App):
    """Main Textual application."""

    CSS_PATH = "tail.tcss"
    AUTO_FOCUS = None
    # Classes the screen gets by terminal size; tail.tcss lays views out by them.
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (110, "-wide")]
    VERTICAL_BREAKPOINTS = [(0, "-tiny"), (27, "-short"), (36, "-tall"), (48, "-huge")]
    TITLE = "Tail"
    SUB_TITLE = "A terminal for voice agents"

    BINDINGS = [
        Binding("1", "show_view('live')", "Live", show=False),
        Binding("2", "show_view('latency')", "Latency", show=False),
        Binding("3", "show_view('pipeline')", "Pipeline", show=False),
        Binding("4", "show_view('metrics')", "Metrics", show=False),
        Binding("5", "show_view('logs')", "Logs", show=False),
        Binding("enter", "inspect", "Inspect", show=False),
        Binding("up", "move(-1)", "Previous turn", show=False),
        Binding("down", "move(1)", "Next turn", show=False),
        Binding("f", "follow", "Follow", show=False),
        Binding("n", "jump(1)", "Next issue", show=False),
        Binding("p", "jump(-1)", "Previous issue", show=False),
        Binding("escape", "end_search", "End search", show=False),
        Binding("plus,equals_sign", "zoom(1)", "More time", show=False),
        Binding("minus", "zoom(-1)", "Less time", show=False),
        Binding("slash", "search", "Search", show=False),
        Binding("l", "level", "Level", show=False),
        Binding("b", "bus_filter", "Bus filter", show=False),
        Binding("e", "latest_error", "Error", show=False),
        Binding("x", "dismiss_error", "Dismiss", show=False),
        Binding("w", "next_worker", "Worker", show=False),
        Binding("t", "next_theme", "Theme", show=False),
        Binding("question_mark", "help", "Help", show=False),
        Binding("c", "connect", "Connect", show=False),
        Binding("s", "save_report", "Save report", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        *,
        on_mount: Optional[Hook] = None,
        on_shutdown: Optional[Hook] = None,
        action_connect: Optional[Hook] = None,
        url: str = "",
        demo_hint: bool = True,
        report_path: Optional[Path] = None,
    ):
        """Initialize the app.

        Args:
            on_mount: Called when the UI has mounted.
            on_shutdown: Called before the app quits.
            action_connect: Called when the user asks to (re)connect.
            url: Where the app is listening for a bot, for the waiting screen.
            demo_hint: Whether the waiting screen mentions the demo.
            report_path: Where to write a report of the session on quit.
        """
        super().__init__()
        self._on_mount = on_mount
        self._on_shutdown = on_shutdown
        self._action_connect = action_connect
        self._report_path = report_path

        self.state = SessionState()
        self._dirty: set[str] = set()
        self._new_bus: list = []
        self._new_logs: list = []
        self._last_slow_tick = 0.0
        self._last_frame: dict[str, float] = {}
        self.view = "live"

        self.title_bar = TitleBar(self.state)
        self.hud = Hud(self.state)
        self.banner = ErrorBanner(self.state)
        self.key_bar = KeyBar()
        self.live = LiveView(self.state, url, demo_hint)
        self.latency_view = LatencyView(self.state)
        self.pipeline_view = PipelineView(self.state)
        self.metrics_view = MetricsView()
        self.logs_view = LogsView()

    #
    # Layout and lifecycle
    #

    def compose(self) -> ComposeResult:
        """Compose the chrome around the views."""
        yield self.title_bar
        yield self.hud
        yield self.banner
        with ContentSwitcher(initial="live", id="views"):
            yield self.live
            yield self.latency_view
            yield self.pipeline_view
            yield self.metrics_view
            yield self.logs_view
        yield self.key_bar

    async def on_mount(self) -> None:
        """Apply the theme, bind widgets to the state and start ticking."""
        for theme in palette.THEMES:
            self.register_theme(theme)
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        self.theme = "tail"
        self.hud.display = False
        self.live.conversation.can_focus = False
        self.logs_view.bind_state(self.state)
        self._bind_session()
        self.set_interval(FLUSH_INTERVAL_SECS, self._flush_dirty)
        self.set_interval(TICK_INTERVAL_SECS, self._tick)
        if self._on_mount:
            await self._on_mount()

    async def action_quit(self) -> None:
        """Write the report if asked to, run the shutdown hook and quit."""
        if self._report_path is not None and self.state.has_bot:
            from pipecat_tail.report import write_report

            try:
                write_report(self.state, self._report_path)
            except OSError:
                pass
        if self._on_shutdown:
            await self._on_shutdown()
        await super().action_quit()

    def _on_theme_changed(self, theme) -> None:
        palette.use_dark(theme.dark)
        self.live.splash.redraw()
        # Plain widgets keep the colors they were drawn with until redrawn.
        self.key_bar.refresh()
        self.banner.refresh()
        self.live.conversation.update_legend()
        self.refresh(layout=True)
        self._dirty |= {CONVERSATION, LATENCY, METRICS, STRIP, FLOW, VOICE, STARTUP, LOGS}
        self.live.conversation.sync(self.state.selected, full=True)
        # The log and bus feeds are written once, in the colors of the time.
        self.logs_view.rebuild()
        self.pipeline_view.bus.rebuild(self.state)

    def _bind_session(self) -> None:
        session = self.state.selected
        self.live.conversation.sync(session, full=True)
        self.metrics_view.set_session(session)
        self.pipeline_view.startup.set_session(session)
        self.latency_view.waterfall.cursor = None
        self.latency_view.refresh_view()

    #
    # Messages
    #

    async def handle_message(self, message: dict[str, Any]) -> None:
        """Fold one wire message into the state and schedule a refresh.

        Args:
            message: A decoded wire message.
        """
        last_bus = _newest(self.state.bus)
        last_log = _newest(self.state.logs)
        before_workers = set(self.state.sessions)
        areas = self.state.apply(message)
        if not areas:
            return
        # The record lists are capped, so a new record shows as a new last one.
        if BUS in areas and _newest(self.state.bus) is not last_bus:
            self._new_bus.append(self.state.bus[-1])
        if LOGS in areas and _newest(self.state.logs) is not last_log:
            self._new_logs.append(self.state.logs[-1])
        if set(self.state.sessions) != before_workers:
            areas |= {WORKERS, STRIP, FLOW}
            if len(before_workers) == 0 or self.state.selected_worker not in before_workers:
                self._bind_session()
        self._dirty |= areas

    async def handle_status(self, status: str, detail: Optional[str] = None) -> None:
        """Update the connection status.

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
        self.live.show_splash(not self.state.has_bot)
        # The HUD has nothing to say before a bot shows up.
        self.hud.display = self.state.has_bot
        if STRIP in dirty or STATUS in dirty:
            self.title_bar.refresh()
            self.hud.refresh()
            self._show_looking_back()
        if ERRORS in dirty:
            self.banner.update_from()
        if CONVERSATION in dirty:
            self.live.conversation.sync(session)
        if VOICE in dirty:
            self.live.voice.refresh()
        if LATENCY in dirty:
            self.latency_view.refresh_view()
            self.hud.refresh()
        if METRICS in dirty:
            self.metrics_view.refresh_view()
        if STARTUP in dirty:
            self.pipeline_view.startup.refresh_view()
        if (FLOW in dirty or METRICS in dirty or ERRORS in dirty) and self.view == "pipeline":
            self.pipeline_view.flow.refresh()
        if WORKERS in dirty or STATUS in dirty:
            self.pipeline_view.worker_tree.sync(self.state)
            self._explain_pipeline_panels()
        if JOBS in dirty:
            self.pipeline_view.jobs.sync(self.state)
            self.pipeline_view.jobs.display = bool(self.state.jobs)
        if BUS in dirty:
            for record in self._new_bus:
                self.pipeline_view.bus.append(record)
            self._new_bus.clear()
        if LOGS in dirty:
            for record in self._new_logs:
                self.logs_view.append(record)
            self._new_logs.clear()
        if LOGS in dirty or ERRORS in dirty:
            self.logs_view.refresh_pinned()

    def _explain_pipeline_panels(self) -> None:
        caps = self.state.capabilities
        if caps and "workers" not in caps:
            # Pipecat before 1.3 runs pipelines as tasks: no workers, jobs or bus.
            note = "needs Pipecat 1.3+ (WorkerRunner)"
        elif self.state.info.get("standalone"):
            # A TailObserver on its own sees its pipeline, not the runner.
            note = "needs a TailServer"
        else:
            note = ""
        view = self.pipeline_view
        view.worker_tree.border_title = f"workers · {note}" if note else "workers"
        view.jobs.border_title = f"jobs · {note}" if note else "jobs"
        view.bus.note = note
        view.bus._update_subtitle()

    def _tick(self) -> None:
        """Animate what moves with time, and only that.

        While nobody speaks and the bot isn't thinking, the screen only
        changes with the clock, so most widgets redraw twice a second. The
        voice timeline scrolls one column at a time, and only while something
        is in its window.
        """
        tick = time.monotonic()
        slow = tick - self._last_slow_tick >= SLOW_TICK_SECS
        if slow:
            self._last_slow_tick = tick
            self.title_bar.refresh()
        session = self.state.selected
        now = self.state.now()
        active = self.state.has_bot and session.is_active(now)
        if active or slow:
            self.hud.refresh()
        if self.view == "live":
            if self.live.splash.display:
                if tick - self._last_frame.get("splash", 0.0) >= SPLASH_FRAME_SECS:
                    self._last_frame["splash"] = tick
                    self.live.splash.next_frame()
            else:
                voice = self.live.voice
                if (
                    voice.display
                    and tick - self._last_frame.get("voice", 0.0) >= voice.frame_secs()
                    and voice.has_motion(now)
                ):
                    self._last_frame["voice"] = tick
                    voice.refresh()
                if self._animating(session):
                    self.live.conversation.tick()
        elif self.view == "pipeline" and (active or slow):
            if tick - self._last_frame.get("flow", 0.0) >= FLOW_FRAME_SECS:
                self._last_frame["flow"] = tick
                self.pipeline_view.flow.refresh()
        if slow and self.view == "latency":
            self.latency_view.refresh_lanes()

    def _animating(self, session) -> bool:
        if session.bot_state_at(self.state.now()) == THINKING:
            return True
        turn = session.current_turn
        return bool(turn and any(not c.settled for c in turn.function_calls))

    #
    # Actions
    #

    def action_show_view(self, view: str) -> None:
        """Switch to a view."""
        self.query_one("#views", ContentSwitcher).current = VIEW_IDS[view]
        # Keys belong to the app unless a widget of this view takes them.
        focused = self.focused
        if (
            focused is not None
            and not self.query_one(f"#{VIEW_IDS[view]}") in focused.ancestors_with_self
        ):
            self.set_focus(None)
        self.view = view
        self.key_bar.set_view(view)
        self._show_following()
        # Logs written while their view was hidden wait for it to have a size.
        self.call_after_refresh(self.logs_view.lines.render_deferred)
        self.call_after_refresh(self.pipeline_view.bus.render_deferred)
        if view == "latency":
            self.latency_view.refresh_view()
        elif view == "pipeline":
            self.pipeline_view.flow.refresh()
        elif view == "metrics":
            self.metrics_view.refresh_view()

    @on(KeyBar.ViewSelected)
    def _on_view_selected(self, event: KeyBar.ViewSelected) -> None:
        self.action_show_view(event.view)

    @on(TurnBlock.Picked)
    def _on_turn_picked(self, event: TurnBlock.Picked) -> None:
        self._inspect(event.turn)

    def _inspect(self, turn: Optional[Turn]) -> None:
        if turn is None:
            self.notify("No turns yet", timeout=1.5)
            return
        self.push_screen(TurnInspector(self.state.selected, turn, self.state.now))

    def action_inspect(self) -> None:
        """Open the turn inspector on the selected turn, or the last one."""
        if self.view == "latency":
            record = self.latency_view.waterfall.selected()
            turn = self.state.selected.turn_by_number(record.turn) if record else None
        else:
            turn = self.live.conversation.current_or_last()
        self._inspect(turn)

    def action_move(self, step: int) -> None:
        """Select the previous or next turn.

        Selecting an earlier turn pauses following; coming back to the last
        one resumes it.
        """
        if self.view == "latency":
            self.latency_view.waterfall.move(step)
        elif self.view == "live":
            self.live.conversation.move_selection(step)
        self._show_following()

    def action_jump(self, step: int) -> None:
        """Jump to the next or previous issue (a match while searching), or error in the logs."""
        if self.view == "logs":
            if self.logs_view.jump(step) is None:
                self.notify("No errors shown", timeout=1.5)
            self._show_following()
            return
        if self.view != "live":
            return
        conversation = self.live.conversation
        if conversation.jump(step) is None:
            if conversation.search_text:
                self.notify(f"Nothing mentions “{conversation.search_text}”", timeout=1.5)
            else:
                self.notify("Nothing went wrong so far", timeout=1.5)
        self._show_following()

    def action_follow(self) -> None:
        """Toggle auto-scroll on the conversation or the logs."""
        if self.view == "logs":
            following = self.logs_view.toggle_follow()
        else:
            following = self.live.conversation.toggle_follow()
            if following:
                # Back to the latest turn: ⏎, ↑ and n start from there again.
                self.live.conversation.select(None)
        self._show_following()
        self.notify("Following" if following else "Paused", timeout=1.5)

    def _show_following(self) -> None:
        """Tell the key bar whether the current view follows what's new and searches."""
        following = {
            "live": self.live.conversation.following,
            "logs": self.logs_view.following,
        }.get(self.view, True)
        self.key_bar.set_following(following)
        self.key_bar.set_searching(bool(self.live.conversation.search_text))
        self._show_looking_back()

    def _show_looking_back(self) -> None:
        """Give the conversation the room while looking back or after the call."""
        session = self.state.selected
        call_over = self.state.call_end(session) is not None
        self.live.set_looking_back(not self.live.conversation.following or call_over)

    def action_zoom(self, direction: int) -> None:
        """Show more or less time in the voice timeline."""
        window = self.live.voice.zoom(direction)
        self.notify(f"Voice timeline: last {window:.0f} s", timeout=1.2)

    def action_search(self) -> None:
        """Search what was said in Live, or the logs from any other view."""
        if self.view == "live" and self.state.has_bot:
            self.live.open_search()
            return
        self.action_show_view("logs")
        self.logs_view.open_search()

    @on(Input.Submitted, "#live-search")
    def _on_live_search(self, event: Input.Submitted) -> None:
        conversation = self.live.conversation
        self.live.close_search()
        count = conversation.set_search(event.value)
        if conversation.search_text and count:
            conversation.jump(0)
        elif conversation.search_text:
            self.notify(f"Nothing mentions “{conversation.search_text}”", timeout=1.5)
        self._show_following()

    def action_end_search(self) -> None:
        """``esc`` closes the search box, or else ends the conversation search."""
        if self.live.search.has_focus:
            self.live.close_search()
        elif self.view == "live" and self.live.conversation.search_text:
            self.live.conversation.set_search(None)
            self._show_following()

    def action_level(self) -> None:
        """Cycle the minimum log level."""
        self.action_show_view("logs")
        level = self.logs_view.cycle_level()
        self.notify(f"Log level {level}", timeout=1.5)

    def action_bus_filter(self) -> None:
        """Cycle the bus feed filter."""
        self.action_show_view("pipeline")
        name = self.pipeline_view.bus.cycle_filter(self.state)
        self.notify(f"Bus filter: {name}", timeout=1.5)

    def action_latest_error(self) -> None:
        """Show the latest error in the logs: n and p go on from there."""
        self.action_show_view("logs")
        if self.logs_view.select_latest_error() is None:
            self.notify("No errors shown", timeout=1.5)
        self._show_following()

    def action_dismiss_error(self) -> None:
        """Hide the error banner."""
        self.state.dismiss_errors()
        self.banner.update_from()

    def action_next_worker(self) -> None:
        """Scope the views to the next pipeline worker."""
        name = self.state.select_next_worker()
        if name is None:
            self.notify("No pipeline workers yet", timeout=1.5)
            return
        self._bind_session()
        self._dirty |= {STRIP, WORKERS, LATENCY, METRICS, STARTUP, CONVERSATION, FLOW, VOICE}
        self.notify(f"Worker {name}", timeout=1.5)

    def action_next_theme(self) -> None:
        """Switch to the next theme."""
        names = [n for n in palette.THEME_CYCLE if n in self.available_themes]
        index = names.index(self.theme) + 1 if self.theme in names else 0
        self.theme = names[index % len(names)]
        self.notify(f"Theme {self.theme}", timeout=1.2)

    def action_save_report(self) -> None:
        """Save the session as a JSON and a Markdown report in the current directory."""
        if not self.state.has_bot:
            self.notify("Nothing to save yet", timeout=1.5)
            return
        from pipecat_tail.report import save_report

        try:
            json_path, md_path = save_report(self.state)
        except OSError as e:
            self.notify(str(e), title="Could not save the report", severity="error", timeout=4)
            return
        self.notify(f"{md_path}\n{json_path}", title="Saved the session report", timeout=4)

    def action_help(self) -> None:
        """Show the keys and colors."""
        self.push_screen(HelpScreen())

    async def action_connect(self) -> None:
        """Ask the host to (re)connect."""
        if self._action_connect:
            await self._action_connect()

    def on_key(self, event: events.Key) -> None:
        """Close the log search on Escape."""
        if event.key == "escape" and self.logs_view.search.has_focus:
            self.logs_view.close_search()
            event.stop()


__all__ = ["TailApp"]
