"""Reports and the JSON stream, from the demo session."""

import io
import json
import re

import pytest

from pipecat_tail import demo
from pipecat_tail.report import JsonStream, markdown, report, save_report
from pipecat_tail.state import SessionState


def demo_state() -> SessionState:
    state = SessionState()
    for message in demo.messages(base=1_000_000.0):
        state.apply(message)
    return state


def test_report_describes_the_session():
    data = report(demo_state())
    session = data["sessions"][0]
    assert session["worker"] == "bot-1"
    assert session["turn_count"] == 8
    assert session["early_ends"] == 1
    assert session["latency"]["p50"] is not None
    assert "endpointing_wait" in session["stages"]
    assert session["interruptions"] == 1
    assert len(session["errors"]) == 1

    weather = next(t for t in session["turns"] if "Barcelona" in t["user"]["text"])
    assert weather["tools"][0]["name"] == "get_weather"
    assert weather["bot"]["interrupted"]
    assert weather["stages"][0]["name"] == "end of turn"
    stay = next(t for t in session["turns"] if "neighborhood" in t["user"]["text"])
    assert stay["early_ends"][0]["after"] == "Nice."


def test_markdown_reads_well():
    text = markdown(demo_state())
    assert text.startswith("# Tail report · bot-1")
    assert "## Where the time goes" in text and "## Turns" in text
    assert "**ƒ get_weather**" in text
    assert "interrupted after" in text
    assert "Pipecat ended the turn after “Nice.”" in text


def test_save_report_writes_json_and_markdown(tmp_path):
    json_path, md_path = save_report(demo_state(), tmp_path)
    assert json.loads(json_path.read_text())["sessions"][0]["turn_count"] == 8
    assert md_path.read_text().startswith("# Tail report")


async def test_json_stream_prints_turns_errors_and_a_summary(tmp_path):
    out = io.StringIO()
    stream = JsonStream("ws://unused", out=out, report_path=tmp_path / "report.md")
    for message in demo.messages(base=1_000_000.0):
        await stream.on_message(message)
    stream.finish()

    events = [json.loads(line) for line in out.getvalue().splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "connected"
    assert "session_started" in kinds
    assert kinds.count("turn") == 8
    assert kinds.count("error") == 1
    assert kinds[-1] == "session_finished"
    assert events[-1]["turn_count"] == 8
    assert (tmp_path / "report.md").read_text().startswith("# Tail report")


@pytest.mark.asyncio
async def test_json_stream_prints_every_turn_whatever_python_ids_it_gets(monkeypatch):
    # Turns a long session dropped free their ids for new turns: pretend every
    # turn gets the same one.
    from pipecat_tail import report as report_module

    monkeypatch.setattr(report_module, "id", lambda _obj: 1, raising=False)
    out = io.StringIO()
    stream = JsonStream("ws://unused", out=out)
    for message in demo.messages(base=1_000_000.0):
        await stream.on_message(message)
    stream.finish()
    kinds = [json.loads(line)["event"] for line in out.getvalue().splitlines()]
    assert kinds.count("turn") == 8


@pytest.mark.asyncio
async def test_json_stream_prints_every_error_past_the_ones_tail_keeps(monkeypatch):
    from pipecat_tail import state as state_module

    monkeypatch.setattr(state_module, "MAX_ERRORS", 2)
    out = io.StringIO()
    stream = JsonStream("ws://unused", out=out)
    for n in range(4):
        await stream.on_message(
            {"label": "tail", "type": "tail-error", "worker": "bot", "data": {"message": str(n)}}
        )
    errors = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [e["message"] for e in errors if e["event"] == "error"] == ["0", "1", "2", "3"]


def test_reports_are_utf_8_whatever_the_locale(tmp_path):
    """On Windows the locale encoding can't write → or ▲, and saving crashed."""
    import os
    import subprocess
    import sys

    script = (
        "import sys; from pathlib import Path; from pipecat_tail import demo; "
        "from pipecat_tail.report import write_report; from pipecat_tail.state import SessionState; "
        "state = SessionState(); [state.apply(m) for m in demo.messages(base=1_000_000.0)]; "
        "write_report(state, Path(sys.argv[1]))"
    )
    env = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    path = tmp_path / "report.md"
    done = subprocess.run([sys.executable, "-c", script, str(path)], env=env, capture_output=True)
    assert done.returncode == 0, done.stderr.decode()[-400:]
    assert "→" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_json_stream_waits_on_a_busy_bot(monkeypatch):
    """A bot busy for a while (a model loading) mustn't end the stream."""
    import asyncio

    from pipecat_tail import client as client_module

    seen = {}

    class Client:
        def __init__(self, url, **kwargs):
            seen.update(kwargs)

        def start(self):
            pass

        async def stop(self):
            pass

    monkeypatch.setattr(client_module, "TailClient", Client)
    stream = JsonStream("ws://unused", out=io.StringIO())
    asyncio.get_running_loop().call_later(0.1, stream._done.set)
    await stream.run()
    assert seen["ping_timeout_secs"] is None


def test_the_demo_doesnt_go_with_json():
    from typer.testing import CliRunner

    from pipecat_tail.cli import entrypoint_cli_typer

    result = CliRunner().invoke(entrypoint_cli_typer, ["--demo", "--json"])
    # A usage error, said in color where the terminal (or CI) allows it.
    assert result.exit_code == 2
    assert "--json" in re.sub(r"\x1b\[[0-9;]*m", "", result.output)
