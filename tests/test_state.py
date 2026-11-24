from pipecat_tail import demo
from pipecat_tail.state import (
    CONVERSATION,
    LATENCY,
    LISTENING,
    SPEAKING,
    THINKING,
    SessionState,
    legacy_contributions,
)


def rtvi(msg_type, data=None, worker="bot-1", timestamp=None):
    message = {"label": "rtvi-ai", "type": msg_type, "worker": worker}
    if data is not None:
        message["data"] = data
    if timestamp is not None:
        message["timestamp"] = timestamp
    return message


def tail(msg_type, data=None, worker="bot-1"):
    message = {"label": "tail", "type": msg_type, "worker": worker}
    if data is not None:
        message["data"] = data
    return message


def demo_state() -> SessionState:
    state = SessionState()
    for message in demo.messages(base=1_000_000.0):
        state.apply(message)
    return state


def test_demo_session_folds_into_state():
    state = demo_state()
    assert state.status == "connected"
    assert state.runner == "runner-1"
    assert state.selected_worker == "bot-1"
    session = state.selected
    assert [t.number for t in session.turns] == [1, 2, 3, 4, 5, 6, 7, 8]

    greeting, weather, tomorrow, flights, hold, stay, photo, thanks = session.turns
    assert greeting.latency.first_bot_speech
    assert "travel assistant" in greeting.segments[0].spoken_text

    assert weather.user_text == "what's the weather like in Barcelona right now?"
    assert weather.tone == ("curious", 0.71)
    assert weather.function_calls[0].function_name == "get_weather"
    assert weather.function_calls[0].settled
    assert weather.interrupted
    spoken, total = weather.spoken_word_counts()
    assert 0 < spoken < total
    assert [b.text for b in weather.backchannels] == ["mhm"]
    assert weather.segments[0].deliveries[0][1] == ("warm",)

    assert tomorrow.user_text == "wait, and what about tomorrow?"
    assert not tomorrow.interrupted
    assert tomorrow.marker == {"text": "✓", "kind": "complete"}

    assert flights.function_calls[0].duration_secs > 1.3
    assert flights.latency_secs > 2.0
    assert hold.input_type == "chat"
    # The user paused after "Nice." and Pipecat ended the turn there.
    assert stay.user_text == "Nice. which neighborhood should I stay in?"
    assert stay.user_llm_parts == ["Nice.", "which neighborhood should I stay in?"]
    assert [(e.heard, e.llm_started) for e in stay.early_ends] == [("Nice.", True)]
    assert session.early_end_count == 1
    # The photo reaches the LLM as a thumbnail, new in its turn.
    sent = photo.context_items[photo.context_new_from :]
    assert any(m.get("kind") == "image" for item in sent for m in item.get("media", []))
    assert session.context["images"] == 1
    assert thanks.ended

    assert len(session.latencies) == 8
    assert session.prompt_tokens > 0 and session.cached_tokens > 0
    assert session.tts_characters > 0
    assert session.stt_seconds > 0
    assert session.llm_model == "gpt-4.1"
    assert session.interruptions == 1
    assert session.backchannel_count == 1
    assert session.flow["DailyInputTransport#0"].out_rates["audio"] > 0
    assert session.context["tools"] == ["get_weather", "search_flights", "hold_flight"]
    assert len(state.errors) == 1 and state.errors[0].category == "connectivity"
    assert state.workers["bot-1"].processors[0] == "DailyInputTransport#0"
    assert session.capabilities


def test_user_input_wins_over_user_transcription():
    # Pipecat 1.13+ sends both; the deprecated one must not double the text.
    state = SessionState()
    state.apply(rtvi("user-input", {"text": "hello", "input_type": "transcription", "final": True}))
    state.apply(rtvi("user-transcription", {"text": "hello", "final": True}))
    assert state.selected.current_turn.user_text == "hello"


def test_user_transcription_works_alone():
    # Pipecat before 1.13 only has user-transcription.
    state = SessionState()
    state.apply(rtvi("user-transcription", {"text": "hi", "final": False}))
    assert state.selected.current_turn.user_interim == "hi"
    state.apply(rtvi("user-transcription", {"text": "hi there", "final": True}))
    turn = state.selected.current_turn
    assert turn.user_text == "hi there" and turn.user_interim == ""


def test_llm_raw_text_wins_over_bot_llm_text():
    state = SessionState()
    state.apply(rtvi("llm-raw-text", {"text": "Hi"}))
    state.apply(rtvi("bot-llm-text", {"text": "Hi"}))
    assert state.selected.current_turn.llm_text == "Hi"


def test_backchannel_does_not_start_a_turn():
    state = SessionState()
    state.apply(rtvi("user-input", {"text": "hello", "input_type": "transcription", "final": True}))
    state.apply(rtvi("llm-raw-text", {"text": "Hi there, how can I help?"}))
    state.apply(rtvi("user-input", {"text": "mhm", "input_type": "backchannel", "final": True}))
    session = state.selected
    assert len(session.turns) == 1
    assert session.current_turn.backchannels[0].text == "mhm"


def test_turn_numbers_follow_turn_tracking():
    state = SessionState()
    state.apply(rtvi("user-started-speaking"))
    session = state.selected
    assert session.current_turn.number == 1 and session.current_turn.provisional
    state.apply(tail("tail-turn", {"kind": "started", "turn": 4}))
    assert [t.number for t in session.turns] == [4]
    assert not session.current_turn.provisional
    state.apply(rtvi("llm-raw-text", {"text": "Hello"}))
    state.apply(rtvi("user-started-speaking"))
    assert [t.number for t in session.turns] == [4, 5]


def test_bot_output_consumes_llm_text_and_tracks_delivery():
    state = SessionState()
    for token in ["Hello", " world", ".", " How", " are", " you", "?"]:
        state.apply(rtvi("llm-raw-text", {"text": token}))
    turn = state.selected.current_turn
    assert turn.pending_text == "Hello world. How are you?"
    state.apply(
        rtvi(
            "bot-output",
            {
                "text": "Hello world.",
                "output_type": "sentence",
                "segment_id": 1,
                "will_be_spoken": True,
                "spoken_status": "new",
            },
        )
    )
    assert turn.pending_text.strip() == "How are you?"
    assert turn.segments[0].output_type == "sentence"
    state.apply(
        rtvi(
            "bot-output",
            {
                "text": "Hello world.",
                "segment_id": 1,
                "spoken_status": "in-progress",
                "spoken_progress": {"accumulated_text": "Hello", "remaining_text": " world."},
                "delivery": ["warm"],
            },
        )
    )
    segment = turn.segments[0]
    assert segment.spoken_text == "Hello"
    assert segment.unspoken_text == " world."
    assert segment.deliveries == [(0, ("warm",))]
    areas = state.apply(rtvi("bot-interrupted"))
    assert turn.interrupted
    assert CONVERSATION in areas and LATENCY in areas


def test_aggregated_by_still_names_the_output_type():
    state = SessionState()
    state.apply(rtvi("bot-output", {"text": "x", "aggregated_by": "code", "segment_id": 1}))
    assert state.selected.current_turn.segments[0].output_type == "code"


def test_tts_text_is_the_fallback_when_no_bot_output():
    state = SessionState()
    state.apply(rtvi("bot-tts-text", {"text": "Hi"}))
    state.apply(rtvi("bot-tts-text", {"text": "there"}))
    assert state.selected.current_turn.tts_text == "Hi there"


def test_bot_state_follows_speech():
    state = SessionState()
    state.apply(rtvi("vad-user-started-speaking", timestamp=100.0))
    state.apply(rtvi("user-started-speaking", timestamp=100.1))
    session = state.selected
    assert session.bot_state_at(100.5) == LISTENING
    state.apply(rtvi("user-input", {"text": "Hi there.", "final": True}, timestamp=101.9))
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=102.0))
    state.apply(rtvi("user-stopped-speaking", timestamp=102.2))
    assert session.bot_state_at(102.5) == THINKING
    # The wait counts from when the user went silent, not from the ruling.
    assert session.waiting_since == 102.0
    state.apply(rtvi("bot-started-speaking", timestamp=103.0))
    assert session.bot_state_at(103.1) == SPEAKING
    state.apply(rtvi("bot-stopped-speaking", timestamp=104.0))
    assert session.bot_state_at(104.1) == LISTENING


def test_a_turn_with_nothing_heard_leaves_the_bot_listening():
    # A murmur the VAD hears and the transcription drops: Pipecat ends the
    # turn after its stop timeout and doesn't answer it.
    state = SessionState()
    session = state.session("bot-1")
    started = tail("tail-turn", {"turn": 1, "kind": "started"})
    started["timestamp"] = 100.0
    state.apply(started)
    state.apply(rtvi("vad-user-started-speaking", timestamp=100.0))
    state.apply(rtvi("user-started-speaking", timestamp=100.0))
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=100.6))
    state.apply(rtvi("user-stopped-speaking", timestamp=105.6))
    assert session.bot_state_at(106.0) == LISTENING
    assert session.waiting_since is None
    assert session.wait_phases(106.0) == []
    assert not session.speech[-1].heard

    # The user speaks: the turn starts over, and nothing ended early.
    state.apply(rtvi("vad-user-started-speaking", timestamp=106.5))
    state.apply(rtvi("user-started-speaking", timestamp=106.5))
    state.apply(rtvi("user-input", {"text": "What time is it?", "final": True}, timestamp=107.5))
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=107.6))
    state.apply(rtvi("user-stopped-speaking", timestamp=107.8))
    assert session.bot_state_at(107.9) == THINKING
    assert session.waiting_since == 107.6
    (turn,) = session.turns
    assert turn.user_text == "What time is it?"
    assert turn.opened_at == 100.0 and turn.restarted_at == 106.5
    assert session.early_end_count == 0

    # Pipecat's turn lasted from the murmur; Tail's counts from the restart.
    state.apply(rtvi("bot-started-speaking", timestamp=108.5))
    state.apply(rtvi("bot-stopped-speaking", timestamp=110.0))
    ended = tail("tail-turn", {"turn": 1, "kind": "ended", "duration_secs": 12.5})
    ended["timestamp"] = 112.5
    state.apply(ended)
    assert turn.duration_secs == 6.0


def test_the_wait_starts_when_the_bot_takes_up_a_turn_with_nothing_heard():
    state = SessionState()
    session = state.session("bot-1")
    # A murmur that cut the bot off: Pipecat answers it right away.
    state.apply(rtvi("user-started-speaking", timestamp=20.0))
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=20.4))
    state.apply(rtvi("user-stopped-speaking", timestamp=20.6))
    assert session.bot_state_at(20.65) == LISTENING
    state.apply(rtvi("bot-llm-started", timestamp=20.7))
    assert session.bot_state_at(20.8) == THINKING
    # Counted from the silence, like any other wait.
    assert session.waiting_since == 20.4
    assert [name for name, _, _ in session.wait_phases(20.8)] == ["end of turn", "waiting", "llm"]
    state.apply(rtvi("bot-started-speaking", timestamp=21.5))
    state.apply(rtvi("bot-llm-stopped", timestamp=21.6))
    state.apply(rtvi("bot-stopped-speaking", timestamp=23.0))

    # The LLM starting long after is the bot speaking on its own (the user
    # went idle), not an answer to the murmur: no wait to count.
    state.apply(rtvi("user-started-speaking", timestamp=30.0))
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=30.4))
    state.apply(rtvi("user-stopped-speaking", timestamp=35.4))
    state.apply(rtvi("bot-llm-started", timestamp=45.4))
    assert session.bot_state_at(45.5) == THINKING
    assert session.waiting_since is None


def test_rtvi_metrics_are_the_fallback_before_pipecat_1_9():
    state = SessionState()
    state.apply(
        rtvi(
            "metrics",
            {
                "ttfb": [{"processor": "OpenAILLMService#0", "model": "gpt-4.1", "value": 0.4}],
                "tokens": [
                    {
                        "processor": "OpenAILLMService#0",
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    }
                ],
                "characters": [{"processor": "CartesiaTTSService#0", "value": 42}],
            },
        )
    )
    session = state.selected
    assert session.metrics[("OpenAILLMService#0", "ttfb")][-1].seconds == 0.4
    assert session.prompt_tokens == 10
    assert session.tts_characters == 42
    # Once Tail's own metrics arrive, RTVI's are ignored.
    state.apply(
        tail(
            "tail-service-usage",
            {"kind": "llm", "processor": "OpenAILLMService#0", "prompt_tokens": 5},
        )
    )
    state.apply(rtvi("metrics", {"tokens": [{"processor": "x", "prompt_tokens": 100}]}))
    assert session.prompt_tokens == 15


def test_rtvi_function_calls_are_the_fallback_before_pipecat_1_9():
    state = SessionState()
    state.apply(
        rtvi(
            "llm-function-call-started",
            {"function_name": "get_weather", "tool_call_id": "c1"},
            timestamp=10.0,
        )
    )
    state.apply(
        rtvi(
            "llm-function-call-in-progress",
            {"function_name": "get_weather", "tool_call_id": "c1", "arguments": {"city": "BCN"}},
            timestamp=10.1,
        )
    )
    state.apply(
        rtvi(
            "llm-function-call-stopped",
            {
                "function_name": "get_weather",
                "tool_call_id": "c1",
                "cancelled": False,
                "result": {"t": 24},
            },
            timestamp=10.6,
        )
    )
    call = state.selected.current_turn.function_calls[0]
    assert call.arguments == {"city": "BCN"}
    assert call.result == {"t": 24}
    assert call.settled and not call.failed
    assert abs(call.duration_secs - 0.5) < 1e-6


def test_legacy_latency_breakdown_gets_contributions():
    data = {
        "ttfb": [
            {"processor": "DeepgramSTTService#0", "start_time": 1.0, "duration_secs": 0.1},
            {"processor": "OpenAILLMService#0", "start_time": 1.2, "duration_secs": 0.4},
            {"processor": "CartesiaTTSService#0", "start_time": 1.7, "duration_secs": 0.2},
        ],
        "user_turn_start_time": 0.8,
        "user_turn_secs": 0.3,
        "function_calls": [{"function_name": "f", "start_time": 1.5, "duration_secs": 0.1}],
    }
    keys = [c["key"] for c in legacy_contributions(data)]
    assert keys == [
        "endpointing_wait",
        "transcription",
        "llm_inference",
        "function_handler",
        "speech_synthesis",
    ]
    state = SessionState()
    state.apply(tail("tail-latency", dict(data, latency_secs=1.2)))
    record = state.selected.last_latency
    assert record.latency_secs == 1.2 and len(record.contributions) == 5


def test_flow_rates_per_second():
    state = SessionState()
    state.apply(
        tail(
            "tail-flow",
            {"interval_secs": 0.25, "processors": {"A": {"out": {"audio": 12, "data": 2}}}},
        )
    )
    stats = state.selected.flow["A"]
    assert stats.out_rates == {"audio": 48.0, "data": 8.0}
    state.apply(tail("tail-flow", {"interval_secs": 0.25, "processors": {}}))
    assert stats.out_total == 0.0


def test_flow_stops_when_the_call_ends():
    # The last interval's rates would otherwise stay up after the bot left:
    # nothing sends a newer count once the pipeline stops.
    moving = {"interval_secs": 0.25, "processors": {"A": {"out": {"audio": 12}, "in": {"data": 1}}}}
    for end in (tail("tail-pipeline-finished"), None):
        state = SessionState()
        state.set_status("connected")
        state.apply(tail("tail-flow", moving))
        stats = state.selected.flow["A"]
        assert stats.out_total == 48.0
        if end is not None:
            state.apply(end)
        else:
            state.set_status("disconnected")
        assert stats.out_total == 0.0 and stats.in_total == 0.0
        # What moved before stays in the history.
        assert list(stats.history) == [48.0]


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


def test_clock_offset_follows_the_bot_clock():
    import time

    state = SessionState()
    state.apply(rtvi("user-audio-level", {"value": 0.1}, timestamp=time.time() - 30.0))
    # The bot's clock is 30 seconds behind; now() follows it.
    assert abs(time.time() - state.now() - 30.0) < 0.5


def test_legacy_system_log_is_parsed():
    state = SessionState()
    line = "2026-09-16 09:41:03.112 | INFO     | pipecat.services.x:go:12 - Hello there\n"
    state.apply(rtvi("system-log", {"text": line}))
    record = state.logs[-1]
    assert record.level == "INFO"
    assert record.name == "pipecat.services.x"
    assert record.function == "go"
    assert record.message == "Hello there"


def test_job_lifecycle():
    state = SessionState()
    state.apply(
        tail(
            "tail-job",
            {
                "kind": "requested",
                "job_id": "j1",
                "job_name": "research",
                "source": "a",
                "target": "b",
            },
            worker=None,
        )
    )
    state.apply(
        tail("tail-job", {"kind": "updated", "job_id": "j1", "update": {"step": 1}}, worker=None)
    )
    state.apply(
        tail(
            "tail-job",
            {"kind": "responded", "job_id": "j1", "status": "completed", "response": {"ok": True}},
            worker=None,
        )
    )
    job = state.jobs["j1"]
    assert job.job_name == "research"
    assert job.updates == 1
    assert job.status == "completed"


def test_interruptions_count_only_when_the_bot_is_cut_off():
    state = SessionState()
    # Pipecat interrupts at every user turn start, speaking or not.
    state.apply(rtvi("bot-interrupted", timestamp=100.0))
    assert state.selected.interruptions == 0
    state.apply(rtvi("bot-started-speaking", timestamp=101.0))
    state.apply(rtvi("bot-interrupted", timestamp=102.0))
    assert state.selected.interruptions == 1
    # Right after the bot stops still counts: the stop and the cut race.
    state.apply(rtvi("bot-started-speaking", timestamp=103.0))
    state.apply(rtvi("bot-stopped-speaking", timestamp=104.0))
    state.apply(rtvi("bot-interrupted", timestamp=104.2))
    assert state.selected.interruptions == 2
    state.apply(rtvi("bot-interrupted", timestamp=110.0))
    assert state.selected.interruptions == 2


def test_context_messages_fold_by_start_index():
    state = SessionState()
    state.apply(rtvi("user-started-speaking"))
    state.apply(rtvi("user-input", {"text": "hi", "final": True, "input_type": "transcription"}))
    state.apply(
        tail(
            "tail-llm-context",
            {
                "llm": "llm",
                "messages": 2,
                "items": [{"role": "system", "text": "Be brief."}, {"role": "user", "text": "hi"}],
                "system": "You are Ada.",
                "tool_schemas": [{"name": "get_weather", "parameters": {}}],
            },
        )
    )
    session = state.selected
    turn = session.current_turn
    assert [i["role"] for i in session.context_items] == ["system", "user"]
    assert session.system_instruction == "You are Ada."
    assert turn.context_new_from == 0 and len(turn.context_items) == 2
    assert turn.context_tools[0]["name"] == "get_weather"
    # The summary keeps the counts, not the messages.
    assert "items" not in session.context

    # Every context comes whole, and replaces the last.
    state.apply(tail("tail-llm-context", {"items": [{"role": "user", "text": "y"}]}))
    assert [i["text"] for i in session.context_items] == ["y"]


def test_events_are_kept_without_audio_levels():
    state = SessionState()
    state.apply(rtvi("user-started-speaking", timestamp=100.0))
    state.apply(rtvi("user-audio-level", {"value": 0.5}, timestamp=100.1))
    state.apply(rtvi("user-stopped-speaking", timestamp=101.0))
    kinds = [kind for _, kind, _ in state.selected.events]
    assert kinds == ["user-started-speaking", "user-stopped-speaking"]


def test_user_llm_text_keeps_every_part_of_a_split_turn():
    # Recorded with a real bot: Pipecat ended the user's turn at a pause, the
    # LLM started on "Nice.", and the user went on before the bot spoke.
    state = SessionState()

    def say(text, at):
        state.apply(rtvi("user-started-speaking", timestamp=at))
        state.apply(rtvi("bot-interrupted", timestamp=at))
        state.apply(
            rtvi("user-input", {"text": text, "input_type": "transcription", "final": True})
        )
        state.apply(rtvi("user-llm-text", {"text": text}, timestamp=at + 0.2))
        state.apply(rtvi("user-stopped-speaking", timestamp=at + 0.2))
        state.apply(rtvi("bot-llm-started", timestamp=at + 0.2))

    say("Nice.", 75.3)
    turn = state.selected.current_turn
    assert turn.user_llm_parts == []
    say("What neighborhoods should I explore?", 76.2)
    assert state.selected.current_turn is turn
    assert turn.user_text == "Nice. What neighborhoods should I explore?"
    assert turn.user_llm_parts == ["Nice.", "What neighborhoods should I explore?"]

    from pipecat_tail.widgets.turns import render_llm_input

    line = render_llm_input(turn)
    assert line is not None
    assert (
        line.plain == "llm got it as 2 messages: “Nice.” + “What neighborhoods should I explore?”"
    )


def test_user_llm_text_shows_only_when_it_differs():
    state = SessionState()
    state.apply(rtvi("user-input", {"text": "hi", "input_type": "transcription", "final": True}))
    state.apply(rtvi("user-llm-text", {"text": "hi"}))
    # The same context sent again (a retry) is not another part.
    state.apply(rtvi("user-llm-text", {"text": "hi"}))
    turn = state.selected.current_turn
    assert turn.user_llm_texts == ["hi"] and turn.user_llm_parts == []
    turn.user_llm_texts = ["hi, filtered"]
    assert turn.user_llm_parts == ["hi, filtered"]


def test_wait_phases_follow_the_bot_from_silence_to_speech():
    state = SessionState()
    session = state.session("bot-1")
    state.apply(rtvi("user-started-speaking", timestamp=8.0))
    state.apply(rtvi("user-input", {"text": "Hi there.", "final": True}, timestamp=9.7))
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=9.8))
    state.apply(rtvi("user-stopped-speaking", timestamp=10.0))
    state.apply(rtvi("bot-llm-started", timestamp=10.02))
    phases = [(name, round(b - a, 2)) for name, a, b in session.wait_phases(10.5)]
    assert phases == [("end of turn", 0.2), ("waiting", 0.02), ("llm", 0.48)]

    state.apply(rtvi("bot-llm-text", {"text": "Sure"}, timestamp=10.6))
    state.apply(rtvi("bot-tts-started", timestamp=10.7))
    state.apply(rtvi("bot-started-speaking", timestamp=10.9))
    phases = [(name, round(b - a, 2)) for name, a, b in session.wait_phases(99.0)]
    assert phases == [
        ("end of turn", 0.2),
        ("waiting", 0.02),
        ("llm", 0.58),
        ("first sentence", 0.1),
        ("tts", 0.2),
    ]

    # The next wait starts over.
    state.apply(rtvi("bot-llm-stopped", timestamp=11.0))
    state.apply(rtvi("bot-stopped-speaking", timestamp=11.5))
    state.apply(rtvi("user-started-speaking", timestamp=12.0))
    state.apply(rtvi("user-input", {"text": "Hi there.", "final": True}, timestamp=12.7))
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=12.8))
    state.apply(rtvi("user-stopped-speaking", timestamp=13.0))
    assert [name for name, _, _ in session.wait_phases(13.1)] == ["end of turn", "waiting"]


def test_wait_phases_show_tool_calls_and_end_when_the_user_goes_on():
    state = SessionState()
    session = state.session("bot-1")
    state.apply(rtvi("user-stopped-speaking", timestamp=10.0))
    state.apply(rtvi("bot-llm-started", timestamp=10.0))
    state.apply(
        tail(
            "tail-function-call",
            {"tool_call_id": "a", "function_name": "get_weather", "started_at": 10.4},
        )
    )
    names = [name for name, _, _ in session.wait_phases(10.8)]
    assert names == ["llm", "tool"]
    # The user goes on before the bot answers: that wait is over.
    state.apply(rtvi("user-started-speaking", timestamp=11.0))
    assert session.wait_phases(11.5) == []


def test_live_wait_counts_from_the_silence_once_endpointing_is_known():
    # Pipecat measures from the user going silent, stop_secs before the VAD
    # says so. Once a breakdown tells Tail how long that is, the live clock
    # starts there too and agrees with the measured answer.
    state = SessionState()
    session = state.session("bot-1")
    state.apply(rtvi("user-input", {"text": "Hi there.", "final": True}, timestamp=9.9))
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=10.0))
    state.apply(rtvi("user-stopped-speaking", timestamp=10.15))
    assert session.waiting_since == 10.0
    state.apply(rtvi("bot-started-speaking", timestamp=11.0))
    state.apply(
        tail(
            "tail-latency",
            {
                "latency_secs": 1.2,
                "contributions": [{"key": "endpointing_wait", "duration_secs": 0.2}],
            },
        )
    )
    state.apply(rtvi("user-started-speaking", timestamp=12.0))
    state.apply(rtvi("user-input", {"text": "Hi there.", "final": True}, timestamp=12.9))
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=13.0))
    state.apply(rtvi("user-stopped-speaking", timestamp=13.15))
    assert round(session.waiting_since, 2) == 12.8


def test_vad_messages_place_speech_where_it_happened():
    # The VAD says the user stopped after stop_secs of silence; Tail's own
    # message says when the silence began, so the wait starts there.
    state = SessionState()
    session = state.session("bot-1")
    message = tail("tail-vad", {"speaking": False, "secs": 0.2, "at": 9.8})
    message["timestamp"] = 10.0
    # RTVI's copy of the same event can arrive first; it is corrected, not doubled.
    state.apply(rtvi("vad-user-stopped-speaking", timestamp=10.0))
    state.apply(message)
    stops = [e for e in session.speech if e.kind == "user_speech_stopped"]
    assert len(stops) == 1 and stops[0].started_at == 9.8
    state.apply(rtvi("user-input", {"text": "Hi there.", "final": True}, timestamp=10.1))
    state.apply(rtvi("user-stopped-speaking", timestamp=10.15))
    assert session.waiting_since == 9.8
    # From now on RTVI's raw VAD messages are left to Tail's.
    state.apply(rtvi("vad-user-started-speaking", timestamp=12.0))
    assert [e.kind for e in session.speech].count("user_speech_started") == 0


def test_early_end_when_the_user_goes_on_before_the_bot_speaks():
    state = SessionState()
    session = state.session("bot-1")
    state.apply(rtvi("user-started-speaking", timestamp=9.0))
    state.apply(rtvi("user-input", {"text": "Nice.", "final": True}, timestamp=9.5))
    state.apply(rtvi("user-stopped-speaking", timestamp=10.0))
    state.apply(rtvi("user-llm-text", {"text": "Nice."}, timestamp=10.0))
    state.apply(rtvi("bot-llm-started", timestamp=10.01))
    state.apply(rtvi("vad-user-started-speaking", timestamp=10.2))
    state.apply(rtvi("user-started-speaking", timestamp=10.2))
    turn = session.current_turn
    assert len(turn.early_ends) == 1
    early = turn.early_ends[0]
    assert early.heard == "Nice." and early.llm_started
    assert round(early.gap_secs, 2) == 0.2
    assert session.early_end_count == 1


def test_no_early_end_when_the_bot_answered_or_the_user_waited():
    state = SessionState()
    session = state.session("bot-1")
    # The bot answered before the user spoke again: a new turn, not an early end.
    state.apply(rtvi("user-started-speaking", timestamp=9.0))
    state.apply(rtvi("user-stopped-speaking", timestamp=10.0))
    state.apply(rtvi("bot-started-speaking", timestamp=10.8))
    state.apply(rtvi("bot-stopped-speaking", timestamp=11.0))
    state.apply(rtvi("user-started-speaking", timestamp=11.2))
    # Nobody answered for a long while: the user had finished.
    state.apply(rtvi("user-stopped-speaking", timestamp=12.0))
    state.apply(rtvi("user-started-speaking", timestamp=16.0))
    assert session.early_end_count == 0


def test_a_wait_the_bot_never_answers_ends_however_it_is_read():
    # Reading the state doesn't change it: the wait ends after the give-up
    # whether or not something asked what the bot was doing first.
    from pipecat_tail.state import LISTENING, THINKING, WAITING_GIVE_UP_SECS

    state = SessionState()
    t = 1_000_000.0
    for msg_type, data, at in [
        ("user-started-speaking", None, t),
        ("user-input", {"text": "Hello?", "final": True}, t + 1.0),
        ("user-stopped-speaking", None, t + 1.5),
    ]:
        state.apply(rtvi(msg_type, data, timestamp=at))
    session = state.selected
    assert session.bot_state_at(t + 2.0) == THINKING
    later = t + 1.5 + WAITING_GIVE_UP_SECS + 1
    assert not session.is_active(later)
    assert session.bot_state_at(later) == LISTENING
    assert not session.is_active(later)


def test_a_processor_error_is_also_a_line_of_the_logs():
    # Pipecat logs a processor's error as a warning, if at all.
    state = SessionState()
    state.apply(
        tail(
            "tail-error",
            {
                "message": "connection lost",
                "category": "connectivity",
                "processor": "DailyOutputTransport#0",
                "timestamp": 1_000_000.0,
            },
        )
    )
    (line,) = state.logs
    assert line.level == "ERROR" and line.name == "DailyOutputTransport#0"
    assert line.message == "connectivity: connection lost" and line.worker == "bot-1"


def test_a_new_call_takes_over_from_one_that_is_over():
    state = SessionState()
    state.apply(tail("tail-pipeline-started", {}, worker="call-1"))
    state.apply(tail("tail-pipeline-started", {}, worker="other"))
    # A call still going keeps its place.
    assert state.selected_worker == "call-1"
    state.apply(tail("tail-pipeline-finished", worker="call-1"))
    state.apply(tail("tail-pipeline-started", {}, worker="call-2"))
    assert state.selected_worker == "call-2"


def test_a_worker_that_starts_again_is_a_restarted_bot():
    """`pipecat tail` outlives the bot: a bot that restarts reuses the worker
    names, and its new run must not be mixed with the last one."""
    state = SessionState()
    started = tail("tail-pipeline-started", {"processors": ["stt", "llm"]})
    state.apply({**started, "timestamp": 100.0})
    state.apply(rtvi("user-started-speaking", timestamp=101.0))
    first = state.selected
    assert first.speech
    # The same message again, replayed to a client coming back, changes nothing.
    state.apply({**started, "timestamp": 100.0})
    assert state.selected is first and first.speech
    # The worker starting again is a new run, from scratch.
    state.apply({**started, "timestamp": 200.0})
    assert state.selected is not first
    assert state.selected.started_at == 200.0 and not state.selected.speech


def test_a_call_settling_after_its_turn_stays_in_it():
    """It used to open an empty turn of its own before finding where it began."""
    state = SessionState()
    state.apply(tail("tail-turn", {"turn": 1, "kind": "started"}))
    call = {"tool_call_id": "call_1", "function_name": "get_weather"}
    state.apply(tail("tail-function-call", {**call, "kind": "function_call_started"}))
    state.apply(tail("tail-turn", {"turn": 1, "kind": "ended", "duration_secs": 2.0}))
    state.apply(
        tail("tail-function-call", {**call, "kind": "function_call_completed", "result": 1})
    )
    session = state.selected
    assert len(session.turns) == 1
    assert session.turns[0].function_calls[0].result == 1


def test_errors_and_jobs_are_capped_and_errors_still_counted(monkeypatch):
    from pipecat_tail import state as state_module

    monkeypatch.setattr(state_module, "MAX_ERRORS", 3)
    monkeypatch.setattr(state_module, "MAX_JOBS", 2)
    state = SessionState()
    for n in range(5):
        state.apply(tail("tail-error", {"message": f"boom {n}", "processor": "tts"}))
        state.apply(tail("tail-job", {"job_id": f"job-{n}", "kind": "requested"}, worker=None))
    assert [e.message for e in state.errors] == ["boom 2", "boom 3", "boom 4"]
    assert state.errors_seen == 5
    assert list(state.jobs) == ["job-3", "job-4"]


def test_interruptions_count_on_a_pipecat_that_doesnt_say(monkeypatch):
    """RTVI says when the bot is cut off from Pipecat 1.8; before, the turn
    it was cut off in does, and it counts once either way."""
    for says in (False, True):
        state = SessionState()
        caps = ["conversation"] + (["interruptions"] if says else [])
        state.apply(tail("tail-pipeline-started", {"capabilities": caps}))
        state.apply(tail("tail-turn", {"turn": 1, "kind": "started"}))
        state.apply(rtvi("bot-llm-text", {"text": "Let me tell you about"}))
        state.apply(rtvi("bot-started-speaking"))
        if says:
            state.apply(rtvi("bot-interrupted"))
        state.apply(tail("tail-turn", {"turn": 1, "kind": "ended", "was_interrupted": True}))
        session = state.selected
        assert session.interruptions == 1
        assert session.turns[0].interrupted


def test_the_answers_on_target_are_counted_not_rounded():
    """51 of 101 is a share of 0.50 to two places: 50.5 answers, rounded to 50."""
    from pipecat_tail.report import session_summary

    state = SessionState()
    for n in range(101):
        state.apply(tail("tail-latency", {"latency_secs": 0.5 if n < 51 else 2.0}))
    summary = session_summary(state, state.selected)
    assert summary["latency"]["on_target_answers"] == 51
