import time

from synthetic import build_messages, rtvi, tail

from pipecat_tail.state import CONVERSATION, LATENCY, SessionState


def test_synthetic_session_folds_into_state():
    state = SessionState()
    for _, message in build_messages():
        state.apply(message)

    assert state.status == "connected"
    assert state.runner == "runner-1"
    assert state.selected_worker == "bot-1"
    session = state.selected
    assert [t.number for t in session.turns] == [1, 2, 3]

    turn1, turn2, turn3 = session.turns
    assert turn1.latency_secs == 1.31
    assert turn1.ended and not turn1.interrupted
    assert turn1.segments[0].spoken_text == "Hi there!"

    assert turn2.user_text == "what's the weather like in Barcelona right now"
    assert turn2.user_interim == ""
    assert turn2.interrupted
    assert turn2.function_calls[0].function_name == "get_weather"
    assert turn2.function_calls[0].result == {"temp_c": 24, "sky": "sunny", "wind_kph": 11}
    assert turn2.function_calls[0].settled
    spoken, total = turn2.spoken_word_counts()
    assert spoken == 8 and total == 26

    assert turn3.user_text == "okay thanks, that's all I needed"
    assert turn3.user_interim == ""
    assert turn3.ended and not turn3.interrupted
    assert turn3.latency_secs == 1.4
    assert [seg.spoken_text for seg in turn3.segments] == [
        "You're welcome.",
        "Enjoy your evening in Barcelona.",
    ]
    assert turn3.pending_text.strip() == ""

    assert len(session.latencies) == 3
    assert session.latencies[0].first_bot_speech
    assert session.prompt_tokens == 2620
    assert session.completion_tokens == 90
    assert session.tts_characters == 217
    assert ("OpenAILLMService#0", "ttfb") in session.metrics
    assert session.startup["total_duration_secs"] == 0.98
    assert session.transport_timing["client_connected_secs"] == 1.84

    assert len(state.errors) == 1
    assert state.errors[0].category == "connectivity"
    assert state.jobs["a91f2c3d4e"].status == "running"
    assert state.jobs["a91f2c3d4e"].updates == 1
    assert len(state.bus) == 3
    assert state.workers["bot-1"].processors[0] == "DailyInputTransport#0"
    assert len(state.logs) == 6


def test_turn_numbers_follow_turn_tracking():
    state = SessionState()
    state.apply(rtvi("user-started-speaking"))
    session = state.selected
    assert session.current_turn.number == 1 and session.current_turn.provisional
    # The tracking observer reports the real number right after.
    state.apply(tail("tail-turn", {"kind": "started", "turn": 4}))
    assert [t.number for t in session.turns] == [4]
    assert not session.current_turn.provisional
    state.apply(rtvi("bot-llm-text", {"text": "Hello"}))
    # A new user turn while the bot has content starts another turn.
    state.apply(rtvi("user-started-speaking"))
    assert [t.number for t in session.turns] == [4, 5]


def test_bot_output_consumes_llm_text():
    state = SessionState()
    for token in ["Hello", " world", ".", " How", " are", " you", "?"]:
        state.apply(rtvi("bot-llm-text", {"text": token}))
    turn = state.selected.current_turn
    assert turn.pending_text == "Hello world. How are you?"
    state.apply(
        rtvi(
            "bot-output",
            {
                "text": "Hello world.",
                "aggregated_by": "sentence",
                "segment_id": 1,
                "will_be_spoken": True,
                "spoken_status": "new",
            },
        )
    )
    assert turn.pending_text.strip() == "How are you?"
    state.apply(
        rtvi(
            "bot-output",
            {
                "text": "Hello world.",
                "aggregated_by": "sentence",
                "segment_id": 1,
                "spoken_status": "in-progress",
                "spoken_progress": {"accumulated_text": "Hello", "remaining_text": " world."},
            },
        )
    )
    assert turn.segments[0].spoken_text == "Hello"
    assert turn.segments[0].unspoken_text == " world."
    areas = state.apply(rtvi("bot-interrupted"))
    assert turn.interrupted
    assert CONVERSATION in areas and LATENCY in areas


def test_tts_text_is_the_fallback_when_no_bot_output():
    state = SessionState()
    state.apply(rtvi("bot-tts-text", {"text": "Hi"}))
    state.apply(rtvi("bot-tts-text", {"text": "there"}))
    assert state.selected.current_turn.tts_text == "Hi there"


def test_worker_selection_cycles():
    state = SessionState()
    state.apply(rtvi("user-started-speaking", worker="a"))
    state.apply(rtvi("user-started-speaking", worker="b"))
    assert state.selected_worker == "a"
    assert state.select_next_worker() == "b"
    assert state.select_next_worker() == "a"


def test_status_change_resets_levels():
    state = SessionState()
    state.apply(rtvi("user-audio-level", {"value": 0.8}))
    assert state.selected.user_level == 0.8
    state.set_status("disconnected")
    assert state.selected.user_level == 0.0


def test_legacy_system_log_is_parsed():
    state = SessionState()
    line = "2026-09-16 09:41:03.112 | INFO     | pipecat.services.x:go:12 - Hello there\n"
    state.apply(rtvi("system-log", {"text": line}))
    record = state.logs[-1]
    assert record.level == "INFO"
    assert record.name == "pipecat.services.x"
    assert record.line == "12"
    assert record.message == "Hello there"
    assert record.clock == "09:41:03.112"


def test_job_lifecycle():
    state = SessionState()
    now = time.time()
    state.apply(
        tail(
            "tail-job",
            {"kind": "requested", "job_id": "j1", "job_name": "n", "source": "r", "target": "w"},
            worker=None,
            t=now,
        )
    )
    state.apply(
        tail(
            "tail-job",
            {"kind": "updated", "job_id": "j1", "update": {"p": 1}},
            worker=None,
            t=now + 1,
        )
    )
    state.apply(
        tail(
            "tail-job",
            {"kind": "responded", "job_id": "j1", "status": "completed", "response": {"ok": True}},
            worker=None,
            t=now + 2,
        )
    )
    job = state.jobs["j1"]
    assert job.status == "completed"
    assert job.updates == 1
    assert job.last == '{"ok":true}'
    assert abs(job.elapsed_secs - 2.0) < 0.01
