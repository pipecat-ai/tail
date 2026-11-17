"""The layout at the widths Tail is used at, from a side pane to a full screen.

Every view, the turn inspector, help and the waiting screen are drawn with the
demo session and read back as text. Live values (the clock, the waves) move,
so these check how things fit rather than compare whole screens.
"""

import io
import re
import time

import pytest
from rich.console import Console

from pipecat_tail import demo
from pipecat_tail.app import TailApp
from pipecat_tail.widgets.inspector import TurnInspector

SIZES = [(48, 30), (64, 46), (100, 40), (160, 45)]
VIEWS = ["live", "latency", "pipeline", "metrics", "logs"]


def screen_text(app: TailApp) -> str:
    """The screen as plain text, the way a screenshot draws it."""
    width, height = app.size
    console = Console(
        width=width,
        height=height,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        record=True,
        legacy_windows=False,
        safe_box=False,
    )
    console.print(
        app.screen._compositor.render_update(full=True, screen_stack=app._background_screens)
    )
    return console.export_text()


async def screens(width: int, height: int) -> dict[str, str]:
    """Every screen of the demo session at one size."""
    session = demo.build_session()
    end = session[-1][0]
    base = time.time() - end
    shots: dict[str, str] = {}
    app = TailApp(url="ws://localhost:9292")
    async with app.run_test(size=(width, height)) as pilot:
        await app.handle_status("connecting")
        await pilot.pause(0.2)
        shots["waiting"] = screen_text(app)
        await app.handle_status("connected")
        for offset, message in session:
            await app.handle_message(demo._stamp(message, base + offset, base))
        await pilot.pause(0.4)
        for view in VIEWS:
            app.action_show_view(view)
            await pilot.pause(0.3)
            shots[view] = screen_text(app)
        app.action_show_view("live")
        turn = next(t for t in app.state.selected.turns if t.early_ends)
        app.push_screen(TurnInspector(app.state.selected, turn, app.state.now))
        await pilot.pause(0.4)
        shots["inspector"] = screen_text(app)
        await pilot.press("escape")
        await pilot.press("question_mark")
        await pilot.pause(0.3)
        shots["help"] = screen_text(app)
    return shots


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height", SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
async def test_nothing_important_is_cut(width, height):
    shots = await screens(width, height)
    for name, text in shots.items():
        lines = text.splitlines()
        assert len(lines) <= height, name
        assert all(len(line.rstrip()) <= width for line in lines), name
        # A number is never cut short.
        assert not re.search(r"\d…", text), (name, re.findall(r".{12}\d…", text))

    # The bot's state and the views, on every screen with a bot.
    assert all(word in shots["live"] for word in ("LISTENING", "THINKING", "SPEAKING"))
    assert re.search(r"live", shots["live"].splitlines()[-1])

    # Every stage keeps its name and its time.
    assert re.search(r"end of turn\s+\d+ ms", shots["latency"])
    assert "first sentence" in shots["latency"]

    # Log messages are shown, not pushed off the side.
    assert "Transcription:" in shots["logs"]

    # Processors keep their names.
    assert "DeepgramSTTService" in shots["pipeline"]

    # The inspector's header breaks between its pieces, never inside one.
    assert "not interrupted" in shots["inspector"]

    # A command too long for the line breaks where a shell can read it.
    waiting = shots["waiting"]
    assert "setup-file) python bot.py" in waiting
    if "$(pipecat tail setup-file)" not in waiting:
        assert "$(pipecat tail \\" in waiting


@pytest.mark.asyncio
async def test_side_pane_keeps_what_was_said_readable():
    shots = await screens(48, 40)
    # The notes go under the words instead of squeezing them.
    assert "perfect, thanks. that's all I" in shots["live"]
    # Help opens on what the marks mean, and wraps under its own text.
    help_lines = shots["help"].splitlines()
    index = next(i for i, line in enumerate(help_lines) if "what the bot is" in line)
    continuation = help_lines[index + 1]
    assert len(continuation) - len(continuation.lstrip(" │")) > 15
    # Past turns in Live take a line each for the user and the bot.
    live = shots["live"]
    assert "you Nice. which neighborhood should I stay …" in live
    assert "bot That's the Belém Tower" in live
