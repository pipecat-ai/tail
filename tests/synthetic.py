"""A synthetic session: the wire messages a weather bot would produce.

Used by the tests and by the headless smoke check. Timestamps are relative to
``start`` so the same script can be replayed at any wall-clock time.
"""

import time
from typing import Any

RTVI = "rtvi-ai"
TAIL = "tail"
WORKER = "bot-1"


def rtvi(msg_type: str, data: Any = None, worker: str = WORKER) -> dict:
    message = {"label": RTVI, "type": msg_type, "worker": worker}
    if data is not None:
        message["data"] = data
    return message


def tail(msg_type: str, data: Any = None, worker: str | None = WORKER, t: float = 0.0) -> dict:
    message = {"label": TAIL, "type": msg_type, "timestamp": t}
    if worker:
        message["worker"] = worker
    if data is not None:
        message["data"] = data
    return message


def build_messages(start: float | None = None) -> list[tuple[float, dict]]:
    """Return ``(timestamp, message)`` pairs for a short conversation."""
    t0 = start if start is not None else time.time() - 40.0
    out: list[tuple[float, dict]] = []

    def at(offset: float, message: dict) -> None:
        if message.get("label") == TAIL:
            message["timestamp"] = t0 + offset
        out.append((t0 + offset, message))

    at(
        0.0,
        tail(
            "tail-ready",
            {"pipecat": "1.10.0", "tail": "0.1.0", "python": "3.12.3", "runner": "runner-1"},
            worker=None,
        ),
    )
    at(
        0.0,
        tail(
            "tail-workers",
            {
                "runner": "runner-1",
                "workers": [
                    {
                        "name": "bot-1",
                        "parent": None,
                        "active": True,
                        "bridged": False,
                        "started_at": t0,
                    },
                    {
                        "name": "tail",
                        "parent": None,
                        "active": True,
                        "bridged": False,
                        "started_at": t0,
                    },
                ],
            },
            worker=None,
        ),
    )
    at(
        0.1,
        tail(
            "tail-bus",
            {
                "name": "BusWorkerRegistryMessage#2",
                "message_type": "BusWorkerRegistryMessage",
                "category": "lifecycle",
                "source": "runner-1",
                "target": None,
                "payload": {"runner": "runner-1", "workers": [{"name": "bot-1"}, {"name": "tail"}]},
            },
            worker=None,
        ),
    )
    at(
        0.2,
        tail(
            "tail-job",
            {
                "kind": "requested",
                "job_id": "a91f2c3d4e",
                "job_name": "call",
                "source": "runner-1",
                "target": "bot-1",
                "payload": {"room": "https://x.daily.co/abc"},
            },
            worker=None,
        ),
    )
    at(
        0.2,
        tail(
            "tail-bus",
            {
                "name": "BusJobRequestMessage#7",
                "message_type": "BusJobRequestMessage",
                "category": "job",
                "source": "runner-1",
                "target": "bot-1",
                "payload": {"job_id": "a91f2c3d4e", "job_name": "call"},
            },
            worker=None,
        ),
    )
    at(
        0.5,
        tail(
            "tail-pipeline-started",
            {
                "processors": [
                    "DailyInputTransport#0",
                    "DeepgramSTTService#0",
                    "LLMUserAggregator#0",
                    "OpenAILLMService#0",
                    "CartesiaTTSService#0",
                    "DailyOutputTransport#0",
                    "LLMAssistantAggregator#0",
                ]
            },
        ),
    )
    at(
        0.5,
        tail(
            "tail-startup",
            {
                "start_time": t0,
                "total_duration_secs": 0.98,
                "setup_phase_secs": 0.93,
                "start_phase_secs": 0.05,
                "processor_timings": [
                    {
                        "processor_name": "DailyInputTransport#0",
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.42,
                        "setup_duration_secs": 0.412,
                        "start_duration_secs": 0.008,
                    },
                    {
                        "processor_name": "DeepgramSTTService#0",
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.209,
                        "setup_duration_secs": 0.188,
                        "start_duration_secs": 0.021,
                    },
                    {
                        "processor_name": "LLMUserAggregator#0",
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.004,
                        "setup_duration_secs": 0.003,
                        "start_duration_secs": 0.001,
                    },
                    {
                        "processor_name": "OpenAILLMService#0",
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.043,
                        "setup_duration_secs": 0.041,
                        "start_duration_secs": 0.002,
                    },
                    {
                        "processor_name": "CartesiaTTSService#0",
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.247,
                        "setup_duration_secs": 0.233,
                        "start_duration_secs": 0.014,
                    },
                    {
                        "processor_name": "DailyOutputTransport#0",
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.103,
                        "setup_duration_secs": 0.097,
                        "start_duration_secs": 0.006,
                    },
                ],
            },
        ),
    )
    at(
        1.8,
        tail(
            "tail-transport-timing",
            {"start_time": t0, "bot_connected_secs": 1.12, "client_connected_secs": 1.84},
        ),
    )
    at(
        0.6,
        tail(
            "tail-log",
            {
                "time": "2026-09-16T09:40:12.981",
                "level": "INFO",
                "name": "pipecat.pipeline.worker",
                "function": "_setup",
                "line": 1283,
                "message": "Pipeline ready",
            },
        ),
    )
    at(
        1.9,
        tail(
            "tail-worker-ready",
            {
                "runner": "runner-1",
                "parent": None,
                "active": True,
                "bridged": False,
                "started_at": t0,
            },
            worker="bot-1",
        ),
    )

    # Turn 1: greeting from the bot.
    at(2.0, tail("tail-turn", {"kind": "started", "turn": 1}))
    at(2.1, rtvi("bot-llm-started"))
    for i, token in enumerate(
        ["Hi", " there", "!", " I", " can", " tell", " you", " the", " weather", "."]
    ):
        at(2.1 + i * 0.05, rtvi("bot-llm-text", {"text": token}))
    at(2.7, rtvi("bot-llm-stopped"))
    at(
        2.7,
        rtvi(
            "bot-output",
            {
                "text": "Hi there!",
                "aggregated_by": "sentence",
                "segment_id": 11,
                "will_be_spoken": True,
                "spoken_status": "new",
            },
        ),
    )
    at(
        2.7,
        rtvi(
            "bot-output",
            {
                "text": "I can tell you the weather.",
                "aggregated_by": "sentence",
                "segment_id": 12,
                "will_be_spoken": True,
                "spoken_status": "new",
            },
        ),
    )
    at(3.2, rtvi("bot-started-speaking"))
    at(3.2, tail("tail-speech", {"kind": "bot_speech_started", "timestamp": t0 + 3.2}))
    at(
        3.2,
        tail(
            "tail-latency",
            {
                "latency_secs": 1.31,
                "first_bot_speech": True,
                "measured_from": "client_connected",
                "total_secs": 1.31,
                "contributions": [
                    {
                        "key": "first_request",
                        "label": "first request",
                        "owner": "bot",
                        "owner_kind": "bot",
                        "start_time": t0 + 1.9,
                        "duration_secs": 0.2,
                    },
                    {
                        "key": "llm_inference",
                        "label": "LLM inference",
                        "owner": "OpenAILLMService#0",
                        "owner_kind": "service",
                        "start_time": t0 + 2.1,
                        "duration_secs": 0.6,
                    },
                    {
                        "key": "speech_synthesis",
                        "label": "speech synthesis",
                        "owner": "CartesiaTTSService#0",
                        "owner_kind": "service",
                        "start_time": t0 + 2.7,
                        "duration_secs": 0.5,
                    },
                ],
            },
        ),
    )
    at(
        3.2,
        tail(
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": "OpenAILLMService#0",
                "model": "gpt-4o",
                "timestamp": t0 + 3.2,
                "seconds": 0.55,
            },
        ),
    )
    at(
        3.2,
        tail(
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": "CartesiaTTSService#0",
                "model": "sonic-2",
                "timestamp": t0 + 3.2,
                "seconds": 0.14,
            },
        ),
    )
    at(
        3.2,
        tail(
            "tail-service-usage",
            {
                "kind": "llm",
                "processor": "OpenAILLMService#0",
                "model": "gpt-4o",
                "timestamp": t0 + 3.2,
                "prompt_tokens": 180,
                "completion_tokens": 12,
                "total_tokens": 192,
            },
        ),
    )
    at(
        3.2,
        tail(
            "tail-service-usage",
            {
                "kind": "tts",
                "processor": "CartesiaTTSService#0",
                "model": "sonic-2",
                "timestamp": t0 + 3.2,
                "characters": 37,
            },
        ),
    )
    at(
        3.3,
        rtvi(
            "bot-output",
            {
                "text": "Hi there!",
                "aggregated_by": "sentence",
                "segment_id": 11,
                "will_be_spoken": True,
                "spoken_status": "completed",
                "spoken_progress": {"accumulated_text": "Hi there!", "remaining_text": ""},
            },
        ),
    )
    at(
        3.8,
        rtvi(
            "bot-output",
            {
                "text": "I can tell you the weather.",
                "aggregated_by": "sentence",
                "segment_id": 12,
                "will_be_spoken": True,
                "spoken_status": "completed",
                "spoken_progress": {
                    "accumulated_text": "I can tell you the weather.",
                    "remaining_text": "",
                },
            },
        ),
    )
    for i in range(8):
        at(3.2 + i * 0.15, rtvi("bot-audio-level", {"value": 0.3 + 0.08 * (i % 4)}))
    at(4.4, rtvi("bot-stopped-speaking"))
    at(
        4.4,
        tail(
            "tail-speech",
            {"kind": "bot_speech_stopped", "timestamp": t0 + 4.4, "started_at": t0 + 3.2},
        ),
    )

    # Turn 2: user asks, bot calls a tool.
    at(6.0, tail("tail-speech", {"kind": "user_speech_started", "timestamp": t0 + 6.0}))
    at(6.1, rtvi("user-started-speaking"))
    at(6.1, tail("tail-speech", {"kind": "user_turn_started", "timestamp": t0 + 6.1}))
    at(
        6.1,
        tail(
            "tail-turn",
            {"kind": "ended", "turn": 1, "duration_secs": 4.1, "was_interrupted": False},
        ),
    )
    at(6.1, tail("tail-turn", {"kind": "started", "turn": 2}))
    for i in range(10):
        at(6.1 + i * 0.15, rtvi("user-audio-level", {"value": 0.4 + 0.1 * (i % 5)}))
    at(
        6.6,
        rtvi(
            "user-transcription",
            {"text": "what's the weather", "user_id": "u", "timestamp": "", "final": False},
        ),
    )
    at(
        7.4,
        rtvi(
            "user-transcription",
            {
                "text": "what's the weather like in Barcelona",
                "user_id": "u",
                "timestamp": "",
                "final": False,
            },
        ),
    )
    at(
        7.9,
        tail(
            "tail-speech",
            {"kind": "user_speech_stopped", "timestamp": t0 + 7.9, "started_at": t0 + 6.0},
        ),
    )
    at(8.1, rtvi("user-stopped-speaking"))
    at(
        8.1,
        tail(
            "tail-speech",
            {"kind": "user_turn_stopped", "timestamp": t0 + 8.1, "started_at": t0 + 6.1},
        ),
    )
    at(
        8.2,
        rtvi(
            "user-transcription",
            {
                "text": "what's the weather like in Barcelona right now",
                "user_id": "u",
                "timestamp": "",
                "final": True,
            },
        ),
    )
    at(8.2, rtvi("user-llm-text", {"text": "what's the weather like in Barcelona right now"}))
    at(
        8.2,
        tail(
            "tail-log",
            {
                "time": "2026-09-16T09:40:20.112",
                "level": "INFO",
                "name": "pipecat.services.deepgram.stt",
                "function": "_on_message",
                "line": 240,
                "message": 'Transcription [final]: "what\'s the weather like in Barcelona right now"',
            },
        ),
    )
    at(
        8.3,
        tail(
            "tail-log",
            {
                "time": "2026-09-16T09:40:20.119",
                "level": "DEBUG",
                "name": "pipecat.processors.aggregators.llm_response",
                "function": "push",
                "line": 88,
                "message": "LLMUserAggregator pushing LLMMessagesFrame (4 messages)",
            },
        ),
    )
    at(
        8.8,
        tail(
            "tail-function-call",
            {
                "kind": "function_call_started",
                "function_name": "get_weather",
                "tool_call_id": "call_1",
                "timestamp": t0 + 8.8,
                "arguments": {"location": "Barcelona"},
                "started_at": t0 + 8.8,
            },
        ),
    )
    at(
        8.85,
        tail(
            "tail-function-call",
            {
                "kind": "function_call_in_progress",
                "function_name": "get_weather",
                "tool_call_id": "call_1",
                "timestamp": t0 + 8.85,
                "arguments": {"location": "Barcelona"},
                "started_at": t0 + 8.8,
                "in_progress_at": t0 + 8.85,
            },
        ),
    )
    at(
        9.3,
        tail(
            "tail-function-call",
            {
                "kind": "function_call_completed",
                "function_name": "get_weather",
                "tool_call_id": "call_1",
                "timestamp": t0 + 9.26,
                "arguments": {"location": "Barcelona"},
                "result": {"temp_c": 24, "sky": "sunny", "wind_kph": 11},
                "started_at": t0 + 8.8,
                "in_progress_at": t0 + 8.85,
            },
        ),
    )
    at(9.4, rtvi("bot-llm-started"))
    reply = "Right now in Barcelona it is 24 degrees and sunny, with a light breeze off the sea. A good evening for a walk along the beach."
    for i, word in enumerate(reply.split(" ")):
        at(9.4 + i * 0.04, rtvi("bot-llm-text", {"text": (" " if i else "") + word}))
    at(10.5, rtvi("bot-llm-stopped"))
    at(
        10.3,
        rtvi(
            "bot-output",
            {
                "text": "Right now in Barcelona it is 24 degrees and sunny, with a light breeze off the sea.",
                "aggregated_by": "sentence",
                "segment_id": 21,
                "will_be_spoken": True,
                "spoken_status": "new",
            },
        ),
    )
    at(
        10.6,
        rtvi(
            "bot-output",
            {
                "text": "A good evening for a walk along the beach.",
                "aggregated_by": "sentence",
                "segment_id": 22,
                "will_be_spoken": True,
                "spoken_status": "new",
            },
        ),
    )
    at(10.8, rtvi("bot-started-speaking"))
    at(10.8, tail("tail-speech", {"kind": "bot_speech_started", "timestamp": t0 + 10.8}))
    at(
        10.8,
        tail(
            "tail-latency",
            {
                "latency_secs": 2.7,
                "first_bot_speech": False,
                "measured_from": "user_silence",
                "total_secs": 2.7,
                "contributions": [
                    {
                        "key": "endpointing_wait",
                        "label": "endpointing wait",
                        "owner": "config: VAD stop_secs",
                        "owner_kind": "setting",
                        "start_time": t0 + 7.9,
                        "duration_secs": 0.2,
                    },
                    {
                        "key": "transcription",
                        "label": "transcription",
                        "owner": "DeepgramSTTService#0",
                        "owner_kind": "service",
                        "start_time": t0 + 8.1,
                        "duration_secs": 0.1,
                    },
                    {
                        "key": "llm_inference",
                        "label": "LLM inference",
                        "owner": "OpenAILLMService#0",
                        "owner_kind": "service",
                        "start_time": t0 + 8.2,
                        "duration_secs": 0.6,
                    },
                    {
                        "key": "function_handler",
                        "label": "function handler",
                        "owner": "get_weather",
                        "owner_kind": "bot",
                        "start_time": t0 + 8.8,
                        "duration_secs": 0.45,
                    },
                    {
                        "key": "llm_inference",
                        "label": "LLM inference",
                        "owner": "OpenAILLMService#0",
                        "owner_kind": "service",
                        "start_time": t0 + 9.3,
                        "duration_secs": 0.95,
                    },
                    {
                        "key": "speech_synthesis",
                        "label": "speech synthesis",
                        "owner": "CartesiaTTSService#0",
                        "owner_kind": "service",
                        "start_time": t0 + 10.3,
                        "duration_secs": 0.4,
                    },
                ],
                "function_calls": [
                    {"function_name": "get_weather", "start_time": t0 + 8.8, "duration_secs": 0.45}
                ],
            },
        ),
    )
    at(
        10.8,
        tail(
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": "OpenAILLMService#0",
                "model": "gpt-4o",
                "timestamp": t0 + 10.8,
                "seconds": 0.61,
            },
        ),
    )
    at(
        10.8,
        tail(
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": "DeepgramSTTService#0",
                "model": "nova-3",
                "timestamp": t0 + 10.8,
                "seconds": 0.28,
            },
        ),
    )
    at(
        10.8,
        tail(
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": "CartesiaTTSService#0",
                "model": "sonic-2",
                "timestamp": t0 + 10.8,
                "seconds": 0.17,
            },
        ),
    )
    at(
        10.8,
        tail(
            "tail-service-latency",
            {
                "kind": "processing",
                "processor": "OpenAILLMService#0",
                "model": "gpt-4o",
                "timestamp": t0 + 10.8,
                "seconds": 1.92,
            },
        ),
    )
    at(
        10.8,
        tail(
            "tail-service-usage",
            {
                "kind": "llm",
                "processor": "OpenAILLMService#0",
                "model": "gpt-4o",
                "timestamp": t0 + 10.8,
                "prompt_tokens": 1180,
                "completion_tokens": 64,
                "total_tokens": 1244,
            },
        ),
    )
    at(
        10.8,
        tail(
            "tail-service-usage",
            {
                "kind": "stt",
                "processor": "DeepgramSTTService#0",
                "model": "nova-3",
                "timestamp": t0 + 10.8,
                "audio_seconds": 2.1,
            },
        ),
    )
    at(
        10.8,
        tail(
            "tail-service-usage",
            {
                "kind": "tts",
                "processor": "CartesiaTTSService#0",
                "model": "sonic-2",
                "timestamp": t0 + 10.8,
                "characters": 132,
            },
        ),
    )
    at(
        10.9,
        tail(
            "tail-log",
            {
                "time": "2026-09-16T09:40:22.739",
                "level": "INFO",
                "name": "pipecat.services.openai.llm",
                "function": "_process_context",
                "line": 301,
                "message": "OpenAILLMService#0 TTFB: 0.617",
            },
        ),
    )
    at(
        11.2,
        rtvi(
            "bot-output",
            {
                "text": "Right now in Barcelona it is 24 degrees and sunny, with a light breeze off the sea.",
                "aggregated_by": "sentence",
                "segment_id": 21,
                "will_be_spoken": True,
                "spoken_status": "in-progress",
                "spoken_progress": {
                    "accumulated_text": "Right now in Barcelona it is 24 degrees",
                    "remaining_text": " and sunny, with a light breeze off the sea.",
                },
            },
        ),
    )

    # User interrupts.
    at(12.4, tail("tail-speech", {"kind": "user_speech_started", "timestamp": t0 + 12.4}))
    at(
        12.5,
        tail(
            "tail-log",
            {
                "time": "2026-09-16T09:40:24.008",
                "level": "WARNING",
                "name": "pipecat.audio.vad.silero",
                "function": "_handle",
                "line": 120,
                "message": "User started speaking while bot speaking, interrupting",
            },
        ),
    )
    at(12.5, rtvi("bot-interrupted"))
    at(12.5, tail("tail-speech", {"kind": "interruption", "timestamp": t0 + 12.5}))
    at(12.5, rtvi("bot-stopped-speaking"))
    at(
        12.5,
        tail(
            "tail-speech",
            {"kind": "bot_speech_stopped", "timestamp": t0 + 12.5, "started_at": t0 + 10.8},
        ),
    )
    at(12.5, rtvi("user-started-speaking"))
    at(12.5, tail("tail-speech", {"kind": "user_turn_started", "timestamp": t0 + 12.5}))
    at(
        12.5,
        tail(
            "tail-turn", {"kind": "ended", "turn": 2, "duration_secs": 6.4, "was_interrupted": True}
        ),
    )
    at(12.5, tail("tail-turn", {"kind": "started", "turn": 3}))
    at(
        13.0,
        rtvi(
            "user-transcription",
            {"text": "okay thanks, that's all I", "user_id": "u", "timestamp": "", "final": False},
        ),
    )
    at(
        13.1,
        tail(
            "tail-error",
            {
                "message": "audio track lost, reconnecting",
                "category": "connectivity",
                "processor": "DailyOutputTransport#0",
                "processor_usable": True,
                "timestamp": t0 + 13.1,
            },
        ),
    )
    at(
        13.1,
        tail(
            "tail-log",
            {
                "time": "2026-09-16T09:40:24.610",
                "level": "ERROR",
                "name": "pipecat.transports.daily",
                "function": "_on_error",
                "line": 512,
                "message": "ErrorFrame: audio track lost, reconnecting",
            },
        ),
    )
    at(
        13.4,
        tail(
            "tail-bus",
            {
                "name": "BusWorkerErrorMessage#1",
                "message_type": "BusWorkerErrorMessage",
                "category": "lifecycle",
                "source": "bot-1",
                "target": None,
                "payload": {"error": "audio track lost, reconnecting"},
            },
            worker=None,
        ),
    )
    at(
        13.5,
        tail(
            "tail-job",
            {
                "kind": "updated",
                "job_id": "a91f2c3d4e",
                "source": "bot-1",
                "target": "runner-1",
                "update": {"state": "audio_restored"},
            },
            worker=None,
        ),
    )
    # The user finishes, the bot signs off.
    at(
        13.9,
        tail(
            "tail-speech",
            {"kind": "user_speech_stopped", "timestamp": t0 + 13.9, "started_at": t0 + 12.4},
        ),
    )
    at(14.1, rtvi("user-stopped-speaking"))
    at(
        14.1,
        tail(
            "tail-speech",
            {"kind": "user_turn_stopped", "timestamp": t0 + 14.1, "started_at": t0 + 12.5},
        ),
    )
    at(
        14.2,
        rtvi(
            "user-transcription",
            {
                "text": "okay thanks, that's all I needed",
                "user_id": "u",
                "timestamp": "",
                "final": True,
            },
        ),
    )
    at(14.2, rtvi("user-llm-text", {"text": "okay thanks, that's all I needed"}))
    at(14.7, rtvi("bot-llm-started"))
    closing = "You're welcome. Enjoy your evening in Barcelona."
    for i, word in enumerate(closing.split(" ")):
        at(14.7 + i * 0.04, rtvi("bot-llm-text", {"text": (" " if i else "") + word}))
    at(15.0, rtvi("bot-llm-stopped"))
    at(
        14.9,
        rtvi(
            "bot-output",
            {
                "text": "You're welcome.",
                "aggregated_by": "sentence",
                "segment_id": 31,
                "will_be_spoken": True,
                "spoken_status": "new",
            },
        ),
    )
    at(
        15.0,
        rtvi(
            "bot-output",
            {
                "text": "Enjoy your evening in Barcelona.",
                "aggregated_by": "sentence",
                "segment_id": 32,
                "will_be_spoken": True,
                "spoken_status": "new",
            },
        ),
    )
    at(15.3, rtvi("bot-started-speaking"))
    at(15.3, tail("tail-speech", {"kind": "bot_speech_started", "timestamp": t0 + 15.3}))
    at(
        15.3,
        tail(
            "tail-latency",
            {
                "latency_secs": 1.4,
                "first_bot_speech": False,
                "measured_from": "user_silence",
                "total_secs": 1.4,
                "contributions": [
                    {
                        "key": "endpointing_wait",
                        "label": "endpointing wait",
                        "owner": "config: VAD stop_secs",
                        "owner_kind": "setting",
                        "start_time": t0 + 13.9,
                        "duration_secs": 0.2,
                    },
                    {
                        "key": "transcription",
                        "label": "transcription",
                        "owner": "DeepgramSTTService#0",
                        "owner_kind": "service",
                        "start_time": t0 + 14.1,
                        "duration_secs": 0.1,
                    },
                    {
                        "key": "llm_inference",
                        "label": "LLM inference",
                        "owner": "OpenAILLMService#0",
                        "owner_kind": "service",
                        "start_time": t0 + 14.2,
                        "duration_secs": 0.7,
                    },
                    {
                        "key": "speech_synthesis",
                        "label": "speech synthesis",
                        "owner": "CartesiaTTSService#0",
                        "owner_kind": "service",
                        "start_time": t0 + 14.9,
                        "duration_secs": 0.4,
                    },
                ],
            },
        ),
    )
    at(
        15.3,
        tail(
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": "OpenAILLMService#0",
                "model": "gpt-4o",
                "timestamp": t0 + 15.3,
                "seconds": 0.48,
            },
        ),
    )
    at(
        15.3,
        tail(
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": "DeepgramSTTService#0",
                "model": "nova-3",
                "timestamp": t0 + 15.3,
                "seconds": 0.25,
            },
        ),
    )
    at(
        15.3,
        tail(
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": "CartesiaTTSService#0",
                "model": "sonic-2",
                "timestamp": t0 + 15.3,
                "seconds": 0.15,
            },
        ),
    )
    at(
        15.3,
        tail(
            "tail-service-usage",
            {
                "kind": "llm",
                "processor": "OpenAILLMService#0",
                "model": "gpt-4o",
                "timestamp": t0 + 15.3,
                "prompt_tokens": 1260,
                "completion_tokens": 14,
                "total_tokens": 1274,
            },
        ),
    )
    at(
        15.3,
        tail(
            "tail-service-usage",
            {
                "kind": "stt",
                "processor": "DeepgramSTTService#0",
                "model": "nova-3",
                "timestamp": t0 + 15.3,
                "audio_seconds": 1.5,
            },
        ),
    )
    at(
        15.3,
        tail(
            "tail-service-usage",
            {
                "kind": "tts",
                "processor": "CartesiaTTSService#0",
                "model": "sonic-2",
                "timestamp": t0 + 15.3,
                "characters": 48,
            },
        ),
    )
    at(
        15.8,
        rtvi(
            "bot-output",
            {
                "text": "You're welcome.",
                "aggregated_by": "sentence",
                "segment_id": 31,
                "will_be_spoken": True,
                "spoken_status": "completed",
                "spoken_progress": {"accumulated_text": "You're welcome.", "remaining_text": ""},
            },
        ),
    )
    at(
        17.2,
        rtvi(
            "bot-output",
            {
                "text": "Enjoy your evening in Barcelona.",
                "aggregated_by": "sentence",
                "segment_id": 32,
                "will_be_spoken": True,
                "spoken_status": "completed",
                "spoken_progress": {
                    "accumulated_text": "Enjoy your evening in Barcelona.",
                    "remaining_text": "",
                },
            },
        ),
    )
    for i in range(12):
        at(15.3 + i * 0.15, rtvi("bot-audio-level", {"value": 0.25 + 0.1 * (i % 5)}))
    at(17.3, rtvi("bot-stopped-speaking"))
    at(
        17.3,
        tail(
            "tail-speech",
            {"kind": "bot_speech_stopped", "timestamp": t0 + 17.3, "started_at": t0 + 15.3},
        ),
    )
    at(
        19.8,
        tail(
            "tail-turn",
            {"kind": "ended", "turn": 3, "duration_secs": 7.3, "was_interrupted": False},
        ),
    )
    # Levels keep flowing while the bot replies, as the input transport does.
    for i in range(int((17.3 - 8.2) / 0.15)):
        at(8.2 + i * 0.15, rtvi("user-audio-level", {"value": 0.03 + 0.02 * (i % 3)}))
    for i in range(int((12.5 - 10.8) / 0.15)):
        at(10.8 + i * 0.15, rtvi("bot-audio-level", {"value": 0.35 + 0.1 * (i % 4)}))
    # Deliver in time order; the calls above are grouped by topic, not time.
    out.sort(key=lambda entry: entry[0])
    return out


def messages_only(start: float | None = None) -> list[dict]:
    """Return just the messages, in order."""
    return [m for _, m in build_messages(start)]
