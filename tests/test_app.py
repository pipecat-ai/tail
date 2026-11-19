"""Drive the Textual app headless with the demo session."""

import time

import pytest

from pipecat_tail import demo
from pipecat_tail.app import TailApp
from pipecat_tail.state import Backchannel
from pipecat_tail.widgets.inspector import TurnInspector
from pipecat_tail.widgets.splash import HelpScreen
from pipecat_tail.widgets.turns import TurnBlock


async def feed(app: TailApp, pilot, until: float | None = None) -> None:
    session = demo.build_session()
    end = until if until is not None else session[-1][0]
    base = time.time() - end
    await app.handle_status("connected")
    for offset, message in session:
        if offset > end:
            break
        await app.handle_message(demo._stamp(message, base + offset, base))
    await pilot.pause(0.3)


async def settled(pilot, done, timeout: float = 3.0) -> bool:
    """Wait for ``done()`` while the app lays out and scrolls, up to ``timeout``."""
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        await pilot.pause(0.05)
    return done()


def latest_in_view(conversation) -> bool:
    """Whether the conversation's latest line is on screen."""
    end = conversation.latest_line()
    top = conversation.scroll_y
    return end is not None and top < end <= top + conversation.scrollable_content_region.height


def as_text(renderable, width: int = 150) -> str:
    """A Rich renderable as plain text."""
    import io

    from rich.console import Console

    console = Console(width=width, record=True, file=io.StringIO())
    console.print(renderable)
    return console.export_text()


def rendered(widget) -> str:
    return (
        str(widget.render())
        if not hasattr(widget.render(), "renderables")
        else "\n".join(str(r) for r in widget.render().renderables)
    )


@pytest.mark.asyncio
async def test_waiting_screen_until_a_bot_arrives():
    app = TailApp(url="ws://localhost:9292")
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        assert app.live.splash.display
        await feed(app, pilot, until=3.0)
        assert not app.live.splash.display


@pytest.mark.asyncio
async def test_views_render_the_session():
    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        await feed(app, pilot)

        blocks = app.query(TurnBlock)
        assert any("Barcelona" in b.turn.user_text for b in blocks)

        title = rendered(app.title_bar)
        assert "bot-1" in title and "pipecat 1.16.0" in title
        hud = rendered(app.hud)
        assert "LISTENING" in hud and "voice-to-voice" in hud and "median" in hud
        assert "call" in title

        assert app.banner.has_class("visible")

        await pilot.press("2")
        assert app.view == "latency"
        assert "search_flights" in rendered(app.latency_view.waterfall)

        await pilot.press("3")
        assert app.view == "pipeline"
        tree = app.pipeline_view.worker_tree
        labels = [str(node.label) for node in tree.root.children]
        assert any("bot-1" in label for label in labels)
        assert "OpenAILLMService" in rendered(app.pipeline_view.flow)

        await pilot.press("4")
        from pipecat_tail.widgets.metrics import latency_rows, usage_rows

        latency = as_text(latency_rows(app.state.selected, 140))
        assert "OpenAILLMService" in latency and "ttfb" in latency and "median" in latency
        assert "tokens in" in as_text(usage_rows(app.state.selected, 140))

        await pilot.press("5")
        await pilot.pause(0.1)
        assert len(app.logs_view.lines.lines) > 5

        await pilot.press("x")
        assert not app.banner.has_class("visible")


@pytest.mark.asyncio
async def test_thinking_counts_up_live():
    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        # Stop right after the user asks about flights: the bot is calling a tool.
        session = demo.build_session()
        stop = next(
            offset
            for offset, m in session
            if m.get("type") == "tail-function-call"
            and m["data"].get("function_name") == "search_flights"
        )
        await feed(app, pilot, until=stop + 0.3)
        assert app.state.selected.bot_state_at(app.state.now()) == "thinking"
        hud = rendered(app.hud)
        assert "▸ THINKING" in hud
        # Where the wait is going, as it happens.
        assert "so far" in hud and "tool search_flights" in hud


@pytest.mark.asyncio
async def test_inspector_and_help_open_and_close():
    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.press("up")
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert isinstance(app.screen, TurnInspector)
        before = app.screen.turn
        await pilot.press("left")
        assert app.screen.turn is not before
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, TurnInspector)

        # Help scrolls with the arrows as soon as it opens, without moving the
        # turn selected behind it.
        selected = app.live.conversation.selected_turn
        await pilot.press("question_mark")
        await pilot.pause(0.1)
        assert isinstance(app.screen, HelpScreen)
        panel = app.screen.query_one("#help")
        assert panel.has_focus
        assert panel.max_scroll_y > 0
        await pilot.press("down", "down", "pagedown")
        await pilot.pause(0.1)
        assert panel.scroll_y > 0
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.live.conversation.selected_turn is selected


def test_metric_and_stage_tables_before_there_is_anything_to_show():
    # Right after start, processors report processing times of about 0 ms,
    # which the table leaves out, and a turn can come without a breakdown.
    from pipecat_tail.state import SessionState
    from pipecat_tail.widgets.latency import stage_table
    from pipecat_tail.widgets.metrics import latency_rows

    state = SessionState()
    processing = [{"processor": "DeepgramSTTService#0", "value": 0.00002}]
    state.apply({"label": "rtvi-ai", "type": "metrics", "data": {"processing": processing}})
    assert as_text(latency_rows(state.selected, 120)).strip() == "No service measurements yet."
    latency = {"latency_secs": 1.1, "contributions": []}
    state.apply({"label": "tail", "type": "tail-latency", "data": latency})
    assert "No breakdown by stage yet" in as_text(stage_table(state.selected, 120))


def test_a_turn_started_over_after_a_murmur_counts_from_the_restart():
    # A murmur Pipecat ended on its stop timeout, then the real question in
    # the same Pipecat turn: the inspector and the endpointing note leave the
    # murmur out.
    from pipecat_tail.state import SessionState
    from pipecat_tail.widgets.inspector import events_page, turn_window
    from pipecat_tail.widgets.latency import speech_lanes

    base = time.time() - 20

    def message(label, kind, at, data=None):
        return {
            "label": label,
            "type": kind,
            "worker": "bot-1",
            "timestamp": base + at,
            "data": data or {},
        }

    state = SessionState()
    murmur = [
        ("tail", "tail-turn", 0.0, {"turn": 1, "kind": "started"}),
        ("rtvi-ai", "vad-user-started-speaking", 0.0, None),
        ("rtvi-ai", "user-started-speaking", 0.0, None),
        ("rtvi-ai", "vad-user-stopped-speaking", 0.5, None),
        ("rtvi-ai", "user-stopped-speaking", 5.5, None),
    ]
    question = [
        ("rtvi-ai", "vad-user-started-speaking", 8.0, None),
        ("rtvi-ai", "user-started-speaking", 8.0, None),
        ("rtvi-ai", "user-input", 9.0, {"text": "What time is it?", "final": True}),
        ("rtvi-ai", "vad-user-stopped-speaking", 9.4, None),
        ("rtvi-ai", "user-stopped-speaking", 9.6, None),
    ]
    for label, kind, at, data in murmur:
        state.apply(message(label, kind, at, data))
    # The stop timeout isn't endpointing: no turn end to tell about yet.
    assert "Your turn ended" not in as_text(speech_lanes(state, state.selected, 120))
    for label, kind, at, data in question:
        state.apply(message(label, kind, at, data))
    session = state.selected
    (turn,) = session.turns
    assert turn_window(turn, session)[0] == base + 8.0
    events = as_text(events_page(turn, session, 120))
    assert "5 messages" in events and "tail-turn" not in events
    assert "Your turn ended 200 ms after" in as_text(speech_lanes(state, session, 120))


@pytest.mark.asyncio
async def test_theme_and_zoom():
    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.press("t")
        assert app.theme == "tail-light"
        await pilot.press("t")
        assert app.theme != "tail-light"
        window = app.live.voice.window
        await pilot.press("plus")
        assert app.live.voice.window > window


@pytest.mark.asyncio
async def test_log_level_and_search_filters():
    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.press("5")
        await pilot.pause(0.2)
        total = len(app.logs_view.lines.lines)
        assert total > 0
        await pilot.press("l")
        await pilot.pause(0.1)
        assert len(app.logs_view.lines.lines) < total


@pytest.mark.asyncio
async def test_logs_keep_coming_once_the_record_list_is_full():
    from pipecat_tail.state import MAX_LOGS, LogRecord

    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await feed(app, pilot, until=5.0)
        # A long call: the state keeps only the latest records.
        old = LogRecord(time="", level="DEBUG", name="bot", function="", line=0, message="old")
        app.state.logs.extend([old] * MAX_LOGS)
        await app.handle_message(
            {
                "label": "tail",
                "type": "tail-log",
                "timestamp": time.time(),
                "data": {"level": "INFO", "name": "bot", "message": "still here"},
            }
        )
        await pilot.press("5")
        await pilot.pause(0.3)
        assert "still here" in "\n".join(str(line.text) for line in app.logs_view.lines.lines)


@pytest.mark.asyncio
async def test_logs_workers_and_startup_follow_the_theme():
    from pipecat_tail.widgets import palette
    from pipecat_tail.widgets.render import status_dot
    from pipecat_tail.widgets.workers import _render_bus_record

    def colors(text) -> set[str]:
        return {
            str(span.style.color.get_truecolor().hex)
            for span in text.spans
            if not isinstance(span.style, str) and span.style.color
        }

    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await feed(app, pilot)
        await pilot.press("t", "5")
        await pilot.pause(0.3)
        light = palette.current()
        assert not light.dark
        # Dark text on the light background, never the dark theme's light text.
        dark_only = {palette.DARK.text, palette.DARK.bright, palette.DARK.dim}
        log_colors = {
            str(segment.style.color.get_truecolor().hex)
            for line in app.logs_view.lines.lines
            for segment in line
            if segment.style and segment.style.color
        }
        assert light.text in log_colors and not log_colors & dark_only
        startup = colors(app.pipeline_view.startup._build())
        assert light.text in startup and not startup & dark_only
        assert status_dot("running").style.color.get_truecolor().hex == light.ok
        if app.state.bus:
            assert not colors(_render_bus_record(app.state.bus[-1])) & dark_only


@pytest.mark.asyncio
async def test_logs_step_through_errors():
    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await feed(app, pilot)
        logs = app.logs_view

        def on_screen(text: str) -> bool:
            top = int(logs.lines.scroll_y)
            visible = logs.lines.lines[top : top + logs.lines.size.height]
            return any(text in line.text for line in visible)

        # e, from anywhere, selects the latest error in the logs.
        await pilot.press("e")
        await pilot.pause(0.3)
        assert app.view == "logs" and not logs.following
        errors = logs.errors()
        assert errors and logs.selected is errors[-1]
        assert logs.lines.border_title.endswith(f"error {len(errors)} of {len(errors)}")
        assert await settled(pilot, lambda: on_screen(errors[-1].message))
        assert "n/p  errors" in rendered(app.key_bar)

        # n and p step through them, wrapping around.
        await pilot.press("n")
        await pilot.pause(0.3)
        assert logs.selected is errors[0]
        assert "error 1 of" in logs.lines.border_title
        await pilot.press("p")
        await pilot.pause(0.3)
        assert logs.selected is errors[-1]

        # f follows again and lets go of the line.
        await pilot.press("f")
        await pilot.pause(0.2)
        assert logs.following and logs.selected is None
        assert "error" not in logs.lines.border_title


@pytest.mark.asyncio
async def test_layout_fits_small_terminals():
    app = TailApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert not app.hud.display
        await feed(app, pilot)
        await pilot.pause(0.2)
        assert app.hud.display
        # A side pane keeps the conversation; the audio gives way.
        assert not app.query_one("#voice").display
        assert app.live.conversation.size.height >= 8

    app = TailApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.pause(0.2)
        assert app.query_one("#voice").display
        # Live is the voice timeline over the conversation, full width.
        assert app.live.conversation.size.width >= 130


@pytest.mark.asyncio
async def test_inspector_pages_show_the_context_and_events():
    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.press("enter")
        await pilot.pause(0.2)
        inspector = app.screen
        assert isinstance(inspector, TurnInspector)
        assert inspector.page == "turn"
        await pilot.press("tab")
        assert inspector.page == "context"
        await pilot.press("tab")
        assert inspector.page == "events"
        await pilot.press("tab")
        assert inspector.page == "turn"


@pytest.mark.asyncio
async def test_save_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = TailApp()
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.press("s")
        await pilot.pause(0.2)
    assert len(list(tmp_path.glob("tail-report-*.md"))) == 1
    assert len(list(tmp_path.glob("tail-report-*.json"))) == 1


def test_turn_events_start_with_the_users_own_words():
    from pipecat_tail.state import SessionState
    from pipecat_tail.widgets.inspector import turn_window

    state = SessionState()
    for message in demo.messages(base=1_000_000.0):
        state.apply(message)
    session = state.selected
    for turn in session.turns:
        if not turn.user_text:
            continue
        start, end = turn_window(turn, session)
        said = next(
            data.get("text")
            for ts, kind, data in session.events
            if kind == "user-input" and ts >= start and (end is None or ts < end)
        )
        assert turn.user_text.startswith(said.split()[0])


def test_speech_lanes_stop_where_the_call_ended():
    # A call that ended a minute ago: the lanes still show it, up to its end.
    from pipecat_tail.state import SessionState
    from pipecat_tail.widgets.latency import speech_lanes

    base = time.time() - 70

    def message(label, kind, at, data=None):
        return {
            "label": label,
            "type": kind,
            "worker": "bot-1",
            "timestamp": base + at,
            "data": data or {},
        }

    state = SessionState()
    state.set_status("connected")
    for label, kind, at in [
        ("tail", "tail-pipeline-started", 0.0),
        ("rtvi-ai", "vad-user-started-speaking", 1.0),
        ("rtvi-ai", "user-started-speaking", 1.0),
        ("rtvi-ai", "vad-user-stopped-speaking", 2.0),
        ("rtvi-ai", "user-stopped-speaking", 2.2),
        ("rtvi-ai", "bot-started-speaking", 3.0),
        ("rtvi-ai", "bot-stopped-speaking", 6.0),
    ]:
        state.apply(
            message(label, kind, at, {"processors": []} if kind.startswith("tail") else None)
        )
    session = state.selected
    # The bot's clock agrees with this one: the call was a minute ago.
    state.clock_offset = 0.0
    # While the call is on, a minute later the lanes have scrolled past it.
    assert state.call_end(session) is None
    lanes = as_text(speech_lanes(state, session, 120))
    assert "now╵" in lanes and "━" not in lanes

    state.apply(message("tail", "tail-pipeline-finished", 10.0))
    assert state.call_end(session) == pytest.approx(base + 10.0)
    lanes = as_text(speech_lanes(state, session, 120))
    assert "end╵" in lanes and "now╵" not in lanes
    assert "━" in lanes and "▆" in lanes


@pytest.mark.asyncio
async def test_the_voice_timeline_keeps_the_call_once_it_is_over():
    app = TailApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await feed(app, pilot)
        voice = app.live.voice
        assert voice.border_title == "voice · last 20 s"
        # Already looking back, so the timeline keeps its size when the call
        # ends: it redraws because what it shows changed.
        await pilot.press("p")
        await pilot.pause(0.2)
        await app.handle_status("disconnected")
        await pilot.pause(0.2)
        # Past the dialog that says the bot went away.
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert voice.border_title == "voice · the call's last 20 s"
        # A minute later nothing moves, and the end of the call is still there.
        app.state.clock_offset = (app.state.clock_offset or 0.0) - 60
        assert not voice.has_motion(app.state.now())
        assert "╰" in rendered(voice)


@pytest.mark.asyncio
async def test_pipeline_view_says_a_lone_observer_cannot_see_the_runner():
    app = TailApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await app.handle_status("connected")

        async def ready(**data):
            data.setdefault("capabilities", ["conversation", "workers"])
            await app.handle_message(
                {"label": "tail", "type": "tail-ready", "timestamp": time.time(), "data": data}
            )
            await app.handle_message(
                {
                    "label": "tail",
                    "type": "tail-pipeline-started",
                    "worker": "bot-1",
                    "timestamp": time.time(),
                    "data": {"processors": ["stt", "llm", "tts"]},
                }
            )
            await pilot.pause(0.2)

        await ready(standalone=True)
        view = app.pipeline_view
        assert view.worker_tree.border_title == "workers · needs a TailServer"
        assert view.bus.border_title.endswith("· needs a TailServer")
        # The worker is listed on its own, without a made-up runner above it.
        assert not view.worker_tree.show_root
        assert "bot-1" in str(view.worker_tree.root.children[0].label)

        # The bot comes back with a TailServer: the runner is there again.
        await ready(runner="runner-1")
        assert view.worker_tree.border_title == "workers"
        assert "needs" not in view.bus.border_title
        assert view.worker_tree.show_root


@pytest.mark.asyncio
async def test_title_says_how_the_call_is_going():
    app = TailApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "waiting for a bot" in rendered(app.title_bar)
        await feed(app, pilot, until=10.0)
        title = rendered(app.title_bar)
        assert "● call" in title and "LIVE" not in title
        # The bot goes away: Tail says so, and how to retry now.
        await app.handle_status("disconnected")
        await pilot.pause(0.1)
        title = rendered(app.title_bar)
        assert "bot gone" in title and "c now" in title


@pytest.mark.asyncio
async def test_the_call_clock_runs_on_the_bots_clock():
    # The bot's clock is 100 s behind this one, and its pipeline started 5 s ago.
    app = TailApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await app.handle_status("connected")
        behind = time.time() - 100
        for kind, at in (("tail-pipeline-started", behind - 5), ("tail-ready", behind)):
            await app.handle_message(
                {"label": "tail", "type": kind, "worker": "bot-1", "timestamp": at, "data": {}}
            )
        await pilot.pause(0.2)
        assert "● call 0:05" in rendered(app.title_bar)


@pytest.mark.asyncio
async def test_a_dialog_says_the_call_ended_and_how_it_went():
    from pipecat_tail.demo import WORKER
    from pipecat_tail.widgets.ended import CallEnded

    def pipeline(kind: str) -> dict:
        return {"label": "tail", "type": kind, "worker": WORKER, "timestamp": time.time()}

    app = TailApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await feed(app, pilot)
        assert not isinstance(app.screen, CallEnded)
        await app.handle_message(pipeline("tail-pipeline-finished"))
        await pilot.pause(0.3)
        assert isinstance(app.screen, CallEnded)
        body = as_text(app.screen.body)
        assert app.screen.title_text == "Call ended"
        assert "turns" in body and "answers on target" in body and "early end" in body
        # Enter closes it, and the bot then going away is still the same call.
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, CallEnded)
        await app.handle_status("disconnected")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, CallEnded)

        # The next call: the bot goes away mid-call this time.
        await app.handle_status("connected")
        await app.handle_message(pipeline("tail-pipeline-started"))
        await pilot.pause(0.2)
        await app.handle_status("disconnected")
        await pilot.pause(0.3)
        assert isinstance(app.screen, CallEnded)
        assert app.screen.title_text == "The bot went away"
        # n closes it and steps to the first turn where something went wrong.
        await pilot.press("n")
        await pilot.pause(0.3)
        assert not isinstance(app.screen, CallEnded)
        assert app.live.conversation.selected_turn is not None


@pytest.mark.asyncio
async def test_a_new_call_closes_the_call_ended_dialog():
    from pipecat_tail.demo import WORKER
    from pipecat_tail.widgets.ended import CallEnded

    app = TailApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await feed(app, pilot, until=20.0)
        await app.handle_status("disconnected")
        await pilot.pause(0.3)
        assert isinstance(app.screen, CallEnded)
        await app.handle_status("connected")
        await app.handle_message(
            {
                "label": "tail",
                "type": "tail-pipeline-started",
                "worker": WORKER,
                "timestamp": time.time() + 1,
                "data": {"processors": []},
            }
        )
        await pilot.pause(0.3)
        assert not isinstance(app.screen, CallEnded)


@pytest.mark.asyncio
async def test_key_bar_lists_the_views_and_folds_when_narrow():
    app = TailApp()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await feed(app, pilot, until=10.0)
        keys = rendered(app.key_bar)
        assert "1  live" in keys and "5  logs" in keys and "q  quit" in keys
        assert "n/p  issues" in keys and "/  search" in keys
        await pilot.press("2")
        assert "↑↓  turns" in rendered(app.key_bar)

    app = TailApp()
    async with app.run_test(size=(48, 30)) as pilot:
        await pilot.pause()
        await feed(app, pilot, until=10.0)
        keys = rendered(app.key_bar)
        assert "1-5  live" in keys and "? " in keys
        assert len(keys.rstrip()) <= 48


@pytest.mark.asyncio
async def test_hud_stacks_in_a_side_pane():
    app = TailApp()
    async with app.run_test(size=(60, 40)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.pause(0.2)
        assert app.hud.size.height == 6
        lines = rendered(app.hud).splitlines()
        assert "LISTENING" in lines[0] and "turn" in lines[3]
        assert all(len(line.rstrip()) <= 60 for line in lines)


@pytest.mark.asyncio
async def test_early_end_of_turn_shows_where_it_happened():
    app = TailApp()
    async with app.run_test(size=(160, 46)) as pilot:
        await pilot.pause()
        # Right after the user went on past "Nice.", while the bot thinks.
        session = demo.build_session()
        cut = next(
            offset
            for offset, m in session
            if m.get("type") == "user-llm-text" and "neighborhood" in m["data"]["text"]
        )
        await feed(app, pilot, until=cut + 0.3)
        await pilot.pause(0.2)
        from pipecat_tail.widgets.inspector import inspect_turn
        from pipecat_tail.widgets.turns import turn_table

        session = app.state.selected
        turn = next(t for t in session.turns if t.early_ends)
        text = as_text(turn_table(turn, session, app.state.now()))
        assert "▲ early end" in text
        assert "llm got it as 2 messages" in text and "why" in text
        assert "1 early end" in rendered(app.hud)
        assert "early" in rendered(app.live.voice)

        page = as_text(inspect_turn(turn, 140, app.state.now()))
        assert "Pipecat ended your turn after “Nice.” and the LLM started on it" in page


@pytest.mark.asyncio
async def test_conversation_follows_the_latest_turn():
    app = TailApp()
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        conv = app.live.conversation
        height = conv.scrollable_content_region.height
        assert await settled(pilot, lambda: conv.max_scroll_y > 0 and latest_in_view(conv))
        # Following is normal, so the key line doesn't offer it.
        assert "f  " not in rendered(app.key_bar)

        # The text stays put while it fits, then the view jumps so the latest
        # line is in the middle, even when a turn grows on a redraw with no
        # new message (a tool call's line while the bot thinks).
        turn = app.state.selected.turns[-1]
        top = conv.scroll_y
        turn.backchannels.append(Backchannel("user", "mhm", time.time()))
        conv.tick()
        assert await settled(pilot, lambda: latest_in_view(conv))
        assert conv.scroll_y == top
        for i in range(height):
            turn.backchannels.append(Backchannel("user", f"mhm {i}", time.time()))
        conv.tick()
        assert await settled(pilot, lambda: conv.scroll_y > top and latest_in_view(conv))
        assert abs(conv.latest_line() - conv.scroll_y - height // 2) <= 1

        # So does a narrower terminal, which lays the turns out again.
        await pilot.resize_terminal(116, 42)
        await pilot.pause(0.1)
        assert await settled(pilot, lambda: latest_in_view(conv))

        # f pauses, and the key bar says how to follow again.
        await pilot.press("f")
        assert not conv.following
        assert "f  follow" in rendered(app.key_bar)
        assert conv.border_title == "conversation · paused"
        await pilot.press("f")
        assert conv.following and "f  " not in rendered(app.key_bar)

        # Selecting an earlier turn pauses; coming back to the last resumes.
        await pilot.press("up", "up")
        assert not conv.following and "f  follow" in rendered(app.key_bar)
        await pilot.press("down")
        assert conv.following and await settled(pilot, lambda: latest_in_view(conv))

        # f resumes from an earlier turn and lets go of it.
        await pilot.press("up", "up")
        assert not conv.following and conv.selected_turn is not None
        await pilot.press("f")
        assert conv.following and conv.selected_turn is None
        assert await settled(pilot, lambda: latest_in_view(conv))


@pytest.mark.asyncio
async def test_switching_worker_leaves_the_other_workers_place_behind():
    app = TailApp()
    async with app.run_test(size=(140, 42)) as pilot:
        await feed(app, pilot)
        conv = app.live.conversation
        await pilot.press("n")
        await pilot.pause(0.2)
        assert "issue 1 of 5" in conv.border_title
        # A second worker, with nothing wrong in it.
        await app.handle_message(
            {
                "label": "rtvi-ai",
                "type": "user-started-speaking",
                "worker": "bot-2",
                "timestamp": time.time(),
            }
        )
        await pilot.press("w")
        await pilot.pause(0.2)
        assert app.state.selected_worker == "bot-2"
        assert conv.border_title == "conversation · paused"


@pytest.mark.asyncio
async def test_live_looks_back_through_issues_and_a_search():
    app = TailApp()
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        conv = app.live.conversation
        tall = app.live.voice.size.height

        # n starts at the first turn where something went wrong.
        issues = conv.issues()
        assert [turn.number for turn, _ in issues] == [2, 4, 5, 6, 7]
        assert "DailyOutputTransport#0 error" in dict((t.number, l) for t, l in issues)[4]
        await pilot.press("n")
        await pilot.pause(0.2)
        assert conv.selected_turn.number == 2 and not conv.following
        assert conv.border_title == "conversation · issue 1 of 5 · ↯ cut · ▲ slow"
        assert "f  follow" in rendered(app.key_bar)
        # Looking back, the timeline keeps only its lanes.
        assert await settled(pilot, lambda: app.live.voice.size.height < tall)
        await pilot.press("p")
        assert conv.selected_turn.number == 7

        # / searches what was said; n then steps through the matches.
        await pilot.press("slash", *"lisbon", "enter")
        await pilot.pause(0.2)
        assert conv.search_text == "lisbon" and conv.selected_turn.number == 8
        assert "n/p  matches" in rendered(app.key_bar)
        await pilot.press("n")
        assert conv.selected_turn.number == 4
        assert conv.border_title == "conversation · “lisbon” 1 of 2"

        # esc ends the search, and f follows the latest turn again.
        await pilot.press("escape")
        assert conv.search_text is None and "n/p  issues" in rendered(app.key_bar)
        await pilot.press("f")
        assert conv.following and conv.selected_turn is None
        assert await settled(
            pilot, lambda: app.live.voice.size.height == tall and latest_in_view(conv)
        )
