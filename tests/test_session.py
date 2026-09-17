import asyncio
import time

import pytest
from synthetic import build_messages

from pipecat_tail.session import SessionWriter, read_session, replay_session


def test_writer_and_reader_round_trip(tmp_path):
    path = tmp_path / "session.jsonl"
    writer = SessionWriter(path)
    entries = build_messages(start=1000.0)
    for t, message in entries:
        writer.write(message, t=t)
    writer.close()
    assert writer.count == len(entries)

    loaded = read_session(path)
    assert len(loaded) == len(entries)
    assert loaded[0][0] == 1000.0
    assert loaded[0][1]["type"] == "tail-ready"
    assert [m["type"] for _, m in loaded] == [m["type"] for _, m in entries]


def test_reader_skips_malformed_lines(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text('{"t": 1, "message": {"type": "a"}}\nnot json\n\n{"t": 2}\n')
    assert [m["type"] for _, m in read_session(path)] == ["a"]


@pytest.mark.asyncio
async def test_replay_keeps_order_and_caps_gaps(tmp_path):
    path = tmp_path / "session.jsonl"
    writer = SessionWriter(path)
    writer.write({"type": "a"}, t=0.0)
    writer.write({"type": "b"}, t=0.05)
    writer.write({"type": "c"}, t=100.0)
    writer.close()

    started = time.monotonic()
    types = [m["type"] async for m in replay_session(path, speed=10.0, max_gap_secs=0.2)]
    elapsed = time.monotonic() - started
    assert types == ["a", "b", "c"]
    # The 100 s gap is capped to 0.2 s, then divided by the speed.
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_replay_at_zero_speed_does_not_wait(tmp_path):
    path = tmp_path / "session.jsonl"
    writer = SessionWriter(path)
    writer.write({"type": "a"}, t=0.0)
    writer.write({"type": "b"}, t=5.0)
    writer.close()
    started = time.monotonic()
    types = [m["type"] async for m in replay_session(path, speed=0)]
    assert types == ["a", "b"]
    assert time.monotonic() - started < 0.1
    await asyncio.sleep(0)
