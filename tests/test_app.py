"""Drive the Textual app headless with the synthetic session."""

import pytest
from synthetic import messages_only
from textual.widgets import TabbedContent

from pipecat_tail.app import TailApp
from pipecat_tail.widgets.conversation import TurnPanel


async def feed(app: TailApp, pilot) -> None:
    for message in messages_only():
        await app.handle_message(message)
    await pilot.pause(0.2)


@pytest.mark.asyncio
async def test_tabs_render_the_session():
    app = TailApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await feed(app, pilot)

        panels = app.query(TurnPanel)
        assert len(panels) == 3
        rendered = str(panels[1].turn.user_text)
        assert "Barcelona" in rendered

        strip = str(app.strip.content)
        assert "connected" in strip
        assert "bot-1" in strip
        assert "turn #3" in strip
        assert "2.70s" in strip

        assert app.banner.display
        assert "DailyOutputTransport#0" in str(app.banner.content)

        await pilot.press("2")
        assert app.query_one("#tabs", TabbedContent).active == "latency"
        bars = str(app.latency_view.bars.content)
        assert "p50" in bars and "greeting" in bars

        await pilot.press("3")
        tree = app.workers_view.worker_tree
        labels = [str(node.label) for node in tree.root.children]
        assert any("bot-1" in label for label in labels)
        assert app.workers_view.jobs.row_count == 1

        await pilot.press("4")
        assert app.metrics_view.summary.row_count >= 4
        assert "DailyInputTransport#0" in str(app.metrics_view.startup.content)

        await pilot.press("5")
        await pilot.pause(0.1)
        assert len(app.logs_view.lines.lines) == 6

        await pilot.press("x")
        assert not app.banner.display


@pytest.mark.asyncio
async def test_log_level_and_search_filters():
    app = TailApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.press("5")
        await pilot.pause(0.1)
        assert len(app.logs_view.lines.lines) == 6
        # DEBUG -> INFO hides the one debug line.
        await pilot.press("l")
        await pilot.pause(0.1)
        assert app.logs_view.level == "INFO"
        assert len(app.logs_view.lines.lines) == 5
        app.logs_view.set_pattern("ttfb")
        await pilot.pause(0.1)
        assert len(app.logs_view.lines.lines) == 1
        assert app.logs_view.set_pattern("(") is not None


@pytest.mark.asyncio
async def test_bus_filter_cycles():
    app = TailApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.press("3")
        feed_widget = app.workers_view.bus
        assert feed_widget.filter_name == "no frames"
        assert len(feed_widget.lines) == 3
        await pilot.press("b")
        await pilot.pause(0.1)
        assert feed_widget.filter_name == "all"
        await pilot.press("b")
        await pilot.pause(0.1)
        assert feed_widget.filter_name == "jobs"
        assert len(feed_widget.lines) == 1


@pytest.mark.asyncio
async def test_status_updates_strip():
    app = TailApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.handle_status("error", "Connection refused")
        await pilot.pause(0.2)
        strip = str(app.strip.content)
        assert "error" in strip and "Connection refused" in strip


@pytest.mark.asyncio
async def test_save_records_messages(tmp_path):
    path = tmp_path / "s.jsonl"
    app = TailApp(save_path=str(path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await feed(app, pilot)
        await pilot.press("s")
        await pilot.pause(0.1)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == len(messages_only())
