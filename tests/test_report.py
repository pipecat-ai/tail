"""Reports and the JSON stream, from the demo session."""

import io
import json

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
