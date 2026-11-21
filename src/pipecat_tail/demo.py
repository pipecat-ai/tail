#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""A scripted session that shows what Tail does without running a bot.

``pipecat tail --demo`` plays it live: a travel assistant answers questions
about the weather and flights, calls tools, gets interrupted, hears a
backchannel, ends a turn too early when the user pauses mid-sentence, takes a
typed message and hits a transport error. It loops until you quit.

The messages are the ones a real bot sends, RTVI and Tail alike, so the demo
also serves the tests and the screenshots.
"""

import asyncio
import base64
import json
import math
import random
import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator, Optional

WORKER = "bot-1"
RUNNER = "runner-1"

PROCESSORS = [
    "DailyInputTransport#0",
    "DeepgramSTTService#0",
    "LLMUserAggregator#0",
    "OpenAILLMService#0",
    "CartesiaTTSService#0",
    "DailyOutputTransport#0",
    "LLMAssistantAggregator#0",
]

STT, LLM, TTS = "DeepgramSTTService#0", "OpenAILLMService#0", "CartesiaTTSService#0"
MODEL = "gpt-4.1"
WORD_SECS = 0.27
LEVEL_PERIOD = 0.08

SYSTEM_PROMPT = (
    "You are a friendly travel assistant on a voice call. Keep answers short and "
    "conversational: one or two sentences, no lists, no markdown. Use get_weather for "
    "the weather and search_flights to find flights; never make up prices or times. "
    "Before holding a flight, confirm which one the user wants."
)

TOOL_SCHEMAS = [
    {
        "name": "get_weather",
        "description": "Current weather or the forecast for a city.",
        "parameters": {
            "location": {"type": "string", "description": "City name"},
            "day": {"type": "string", "enum": ["today", "tomorrow"]},
        },
        "required": ["location"],
    },
    {
        "name": "search_flights",
        "description": "Search flights from the user's home airport.",
        "parameters": {
            "destination": {"type": "string", "description": "IATA airport code"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "time_window": {"type": "string", "enum": ["morning", "afternoon", "evening"]},
        },
        "required": ["destination", "date"],
    },
    {
        "name": "hold_flight",
        "description": "Hold a seat on a flight for 24 hours.",
        "parameters": {"flight": {"type": "string", "description": "Flight number"}},
        "required": ["flight"],
    },
]


@dataclass
class Bot:
    """What the bot says in a turn."""

    sentences: list[str]
    deliveries: dict[int, list[str]] = field(default_factory=dict)
    """Delivery tags by word index into the whole reply."""


@dataclass
class Call:
    """A tool call in a turn."""

    name: str
    arguments: dict[str, Any]
    result: Any
    secs: float


@dataclass
class Scene:
    """One turn of the script."""

    user: str
    bot: Bot
    tone: Optional[tuple[str, float]] = None
    typed: bool = False
    calls: list[Call] = field(default_factory=list)
    llm_ttfb: float = 0.42
    tts_ttfb: float = 0.19
    backchannel_at: Optional[int] = None
    """Word index of the reply after which the user says "mhm"."""
    interrupt_at: Optional[int] = None
    """Word index of the reply at which the user cuts the bot off."""
    error: Optional[str] = None
    photo: bool = False
    """Whether the user sends a photo with the message."""
    early: Optional[str] = None
    """What the user says before a pause that Pipecat takes for the end of the turn."""
    pause_after: float = 2.2


GREETING = Bot(
    ["Hi! I'm your travel assistant.", "Ask me about the weather, or find you a flight."]
)

SCRIPT = [
    Scene(
        user="what's the weather like in Barcelona right now?",
        tone=("curious", 0.71),
        calls=[
            Call(
                "get_weather",
                {"location": "Barcelona"},
                {"temp_c": 24, "sky": "sunny", "wind_kph": 11},
                0.41,
            )
        ],
        bot=Bot(
            [
                "Right now in Barcelona it's 24 degrees and sunny, with a light breeze off the sea.",
                "A perfect evening for a walk along the beach, and the sunset is at 8:12.",
            ],
            deliveries={5: ["warm"]},
        ),
        backchannel_at=9,
        interrupt_at=17,
        pause_after=0.0,
    ),
    Scene(
        user="wait, and what about tomorrow?",
        tone=("eager", 0.64),
        bot=Bot(
            ["Tomorrow looks sunny too, around 22 degrees.", "Clouds roll in after six."],
        ),
        llm_ttfb=0.33,
    ),
    Scene(
        user="can you find me a flight to Lisbon on Friday morning?",
        tone=("neutral", 0.58),
        calls=[
            Call(
                "search_flights",
                {"destination": "LIS", "date": "2026-11-20", "time_window": "morning"},
                [
                    {"flight": "TP1033", "dep": "07:05", "price": 89},
                    {"flight": "VY8460", "dep": "09:40", "price": 64},
                ],
                1.35,
            )
        ],
        bot=Bot(
            [
                "I found two morning flights to Lisbon on Friday.",
                "TAP at 7:05 for 89 euros, and Vueling at 9:40 for 64.",
                "Want me to hold one?",
            ],
            deliveries={18: ["upbeat"]},
        ),
        tts_ttfb=0.42,
        error="audio track lost, reconnecting",
    ),
    Scene(
        user="hold the Vueling one please",
        typed=True,
        calls=[
            Call(
                "hold_flight",
                {"flight": "VY8460", "passengers": 1},
                {"status": "held", "expires_in_h": 24},
                0.62,
            )
        ],
        bot=Bot(
            ["Done!", "Your seat on Vueling at 9:40 is held for 24 hours."],
            deliveries={0: ["excited"]},
        ),
        llm_ttfb=0.95,
    ),
    Scene(
        early="Nice.",
        user="which neighborhood should I stay in?",
        tone=("curious", 0.66),
        bot=Bot(
            [
                "Stay in Alfama or Chiado.",
                "Both are central and walkable, with plenty of places to eat.",
            ]
        ),
        llm_ttfb=0.52,
    ),
    Scene(
        user="a friend sent me this, what is it?",
        typed=True,
        photo=True,
        bot=Bot(
            [
                "That's the Belém Tower, on the river west of the center.",
                "Go early, the line gets long by eleven.",
            ]
        ),
        llm_ttfb=0.88,
    ),
    Scene(
        user="perfect, thanks. that's all I needed",
        tone=("pleased", 0.82),
        bot=Bot(["You're welcome.", "Have a great trip to Lisbon!"], deliveries={0: ["warm"]}),
        llm_ttfb=0.37,
        pause_after=4.0,
    ),
]


def _png(width: int, height: int, pixels: list[list[tuple[int, int, int]]]) -> bytes:
    """Encode RGB pixels as a PNG, with nothing but the standard library."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _photo() -> list[list[tuple[int, int, int]]]:
    """A tower by a river at golden hour, drawn in code for the demo."""
    width, height = 160, 120
    pixels = []
    for y in range(height):
        row = []
        for x in range(width):
            t = y / height
            # Sky from blue to a warm horizon, then the river.
            color = (
                int(110 + 140 * t),
                int(160 + 60 * t),
                int(225 - 90 * t),
            )
            if y > 86:
                ripple = 12 if (x + y * 3) % 17 < 3 else 0
                color = (40 + ripple, 84 + ripple, 112 + ripple)
            # The tower: a body, a narrower top with battlements, windows.
            body = 62 <= x < 98 and 40 <= y <= 88
            top = 70 <= x < 90 and 22 <= y < 40
            crenel = (62 <= x < 98 and 34 <= y < 40 and (x // 4) % 2 == 0) or (
                70 <= x < 90 and 16 <= y < 22 and (x // 4) % 2 == 0
            )
            if body or top or crenel:
                shade = 0 if x < 84 else 26
                color = (236 - shade, 222 - shade, 190 - shade)
                window = (x - 66) % 10 < 3 and (y - 46) % 14 < 5 and 44 <= y < 84
                if window or (77 <= x < 83 and 72 <= y <= 88):
                    color = (70, 64, 60)
            row.append(color)
        pixels.append(row)
    return pixels


_PHOTO_URL: Optional[str] = None


def photo_url() -> str:
    """The demo photo as a data URL, the way a client sends an image to the LLM."""
    global _PHOTO_URL
    if _PHOTO_URL is None:
        png = _png(160, 120, _photo())
        _PHOTO_URL = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return _PHOTO_URL


class _Timeline:
    """Messages at offsets from the start of the session."""

    def __init__(self, seed: int = 7):
        self.events: list[tuple[float, int, dict[str, Any]]] = []
        self._seq = 0
        self.rnd = random.Random(seed)

    def at(self, t: float, message: dict[str, Any]) -> None:
        self._seq += 1
        self.events.append((t, self._seq, message))

    def rtvi(self, t: float, msg_type: str, data: Any = None) -> None:
        message: dict[str, Any] = {"label": "rtvi-ai", "type": msg_type, "worker": WORKER}
        if data is not None:
            message["data"] = data
        self.at(t, message)

    def tail(
        self, t: float, msg_type: str, data: Any = None, worker: Optional[str] = WORKER
    ) -> None:
        message: dict[str, Any] = {"label": "tail", "type": msg_type}
        if worker:
            message["worker"] = worker
        if data is not None:
            message["data"] = data
        self.at(t, message)

    def log(self, t: float, level: str, name: str, function: str, message: str) -> None:
        self.tail(
            t,
            "tail-log",
            {
                "time": "",
                "level": level,
                "name": name,
                "function": function,
                "line": 0,
                "message": message,
            },
        )


def _iso(ts: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(ts).isoformat()


def _speech_levels(tl: _Timeline, start: float, end: float, kind: str, loud: float = 0.6) -> None:
    t = start
    phase = tl.rnd.random() * 6
    while t < end:
        edge = min(t - start, end - t)
        ramp = min(1.0, edge / 0.2) ** 0.7
        syll = 0.35 + 0.65 * abs(math.sin((t * 2.4 + phase) * math.pi)) ** 1.4
        phrase = 0.75 + 0.25 * math.sin(t * 0.9)
        gap = 0.2 if tl.rnd.random() < 0.05 else 1.0
        level = loud * ramp * syll * phrase * gap * (0.7 + 0.3 * tl.rnd.random())
        tl.rtvi(t, kind, {"value": round(min(1.0, level), 3)})
        t += LEVEL_PERIOD


def _words(text: str) -> list[str]:
    return text.split()


class _Writer:
    """Builds the timeline of a whole session."""

    def __init__(self, seed: int = 7):
        self.tl = _Timeline(seed)
        self.turn = 0
        self.segment_id = 100
        self.tool_id = 0
        self.speaking_user: list[tuple[float, float]] = []
        self.generating: list[tuple[float, float]] = []
        self.synthesizing: list[tuple[float, float]] = []
        self.speaking_bot: list[tuple[float, float]] = []
        self.messages = 2
        self.context: list[dict[str, Any]] = []

    def _send_context(self, t: float) -> None:
        """Send the LLM context as the observer would: whole."""
        from pipecat_tail import context as ctx

        data: dict[str, Any] = {"llm": LLM, **ctx.summary(self.context)}
        data["tools"] = [schema["name"] for schema in TOOL_SCHEMAS]
        data["items"] = [ctx.item(m) for m in self.context]
        data["system"] = SYSTEM_PROMPT
        data["tool_schemas"] = TOOL_SCHEMAS
        self.tl.tail(t, "tail-llm-context", data)

    # Session start

    def start(self, t: float) -> float:
        tl = self.tl
        tl.tail(
            t,
            "tail-ready",
            {
                "pipecat": "1.16.0",
                "tail": "demo",
                "python": "3.12",
                "runner": RUNNER,
                "capabilities": [
                    "conversation",
                    "audio-levels",
                    "metrics",
                    "function-calls",
                    "errors",
                    "flow",
                    "logs",
                    "latency-breakdown",
                    "turns",
                    "startup",
                    "vad",
                    "user-input",
                    "llm-marker",
                    "workers",
                ],
            },
            worker=None,
        )
        tl.tail(
            t,
            "tail-workers",
            {
                "runner": RUNNER,
                "workers": [
                    {
                        "name": WORKER,
                        "parent": None,
                        "active": True,
                        "bridged": False,
                        "started_at": 0,
                    },
                    {
                        "name": "tail",
                        "parent": None,
                        "active": True,
                        "bridged": False,
                        "started_at": 0,
                    },
                ],
            },
            worker=None,
        )
        tl.tail(
            t + 0.02,
            "tail-bus",
            {
                "name": "BusWorkerReadyMessage#1",
                "message_type": "BusWorkerReadyMessage",
                "category": "lifecycle",
                "source": WORKER,
                "target": None,
                "payload": {"runner": RUNNER, "active": True},
            },
            worker=None,
        )
        tl.log(
            t + 0.03,
            "INFO",
            "pipecat.workers.runner",
            "run",
            f"WorkerRunner '{RUNNER}': starting workers [{WORKER}, tail]",
        )
        tl.log(
            t + 0.05,
            "DEBUG",
            "pipecat.transports.daily.transport",
            "join",
            "Joining https://demo.daily.co/tail-demo",
        )
        tl.tail(
            t + 0.4,
            "tail-pipeline-started",
            {
                "processors": PROCESSORS,
                "capabilities": [
                    "conversation",
                    "latency-breakdown",
                    "user-input",
                    "vad",
                    "workers",
                ],
            },
        )
        tl.tail(
            t + 0.45,
            "tail-startup",
            {
                "total_duration_secs": 0.94,
                "setup_phase_secs": 0.73,
                "start_phase_secs": 0.21,
                "processor_timings": [
                    {
                        "processor_name": "DailyInputTransport#0",
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.71,
                        "setup_duration_secs": 0.66,
                        "start_duration_secs": 0.05,
                    },
                    {
                        "processor_name": STT,
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.32,
                        "setup_duration_secs": 0.31,
                        "start_duration_secs": 0.01,
                    },
                    {
                        "processor_name": LLM,
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.05,
                        "setup_duration_secs": 0.04,
                        "start_duration_secs": 0.01,
                    },
                    {
                        "processor_name": TTS,
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.27,
                        "setup_duration_secs": 0.12,
                        "start_duration_secs": 0.15,
                    },
                    {
                        "processor_name": "DailyOutputTransport#0",
                        "start_offset_secs": 0.0,
                        "duration_secs": 0.02,
                        "setup_duration_secs": 0.01,
                        "start_duration_secs": 0.01,
                    },
                ],
            },
        )
        tl.tail(
            t + 0.5,
            "tail-transport-timing",
            {"bot_connected_secs": 0.71, "client_connected_secs": 2.1},
        )
        tl.log(
            t + 0.6,
            "INFO",
            "pipecat.transports.daily.transport",
            "on_joined",
            "Joined room tail-demo",
        )
        tl.log(t + 2.1, "INFO", "bot", "on_client_connected", "Client connected: participant 7c1f")
        return t + 2.2

    # Turns

    def greeting(self, t: float) -> float:
        tl = self.tl
        self.turn += 1
        tl.tail(t, "tail-turn", {"kind": "started", "turn": self.turn})
        end = self._bot_reply(
            t, GREETING, llm_ttfb=0.61, tts_ttfb=0.2, first=True, waiting_since=t - 0.5
        )
        tl.tail(
            end + 0.3,
            "tail-turn",
            {
                "kind": "ended",
                "turn": self.turn,
                "duration_secs": end - t,
                "was_interrupted": False,
            },
        )
        return end + 1.6

    def scene(self, t: float, scene: Scene) -> float:
        tl = self.tl
        self.turn += 1
        turn = self.turn
        if scene.typed:
            tl.tail(t, "tail-turn", {"kind": "started", "turn": turn})
            tl.rtvi(
                t,
                "user-input",
                {
                    "text": scene.user,
                    "input_type": "chat",
                    "timestamp": _iso(t),
                    "final": True,
                    "user_id": "7c1f",
                    "msg_id": f"m{turn}",
                },
            )
            user_stop = t
            ruled = t
        else:
            if scene.early:
                # The user pauses after a few words; Pipecat ends the turn, the
                # LLM starts on it, and the user goes on before the bot speaks.
                _, first_ruled = self._user_speech(t, scene.early, None, start_turn=True)
                self.context.append({"role": "user", "content": scene.early})
                self.messages += 1
                self._send_context(first_ruled + 0.01)
                tl.rtvi(first_ruled + 0.01, "user-llm-text", {"text": scene.early})
                tl.rtvi(first_ruled + 0.02, "bot-llm-started")
                resumed = first_ruled + 0.25
                tl.rtvi(resumed + 0.18, "bot-interrupted")
                tl.rtvi(resumed + 0.2, "bot-llm-stopped")
                user_stop, ruled = self._user_speech(
                    resumed, scene.user, scene.tone, start_turn=False
                )
            else:
                user_stop, ruled = self._user_speech(t, scene.user, scene.tone, start_turn=True)
        self._user_stop = user_stop
        end = self._bot_reply(
            ruled,
            scene.bot,
            scene=scene,
            llm_ttfb=scene.llm_ttfb,
            tts_ttfb=scene.tts_ttfb,
            waiting_since=user_stop,
        )
        interrupted = scene.interrupt_at is not None
        tl.tail(
            end + 0.05,
            "tail-turn",
            {
                "kind": "ended",
                "turn": turn,
                "duration_secs": end - t,
                "was_interrupted": interrupted,
            },
        )
        return end + scene.pause_after

    def _user_speech(
        self, t: float, text: str, tone: Optional[tuple[str, float]], *, start_turn: bool
    ) -> tuple[float, float]:
        """The user saying ``text`` from ``t``; returns when they stopped and when the turn ended."""
        tl = self.tl
        words = _words(text)
        speak_secs = 0.35 + len(words) * 0.26
        tl.rtvi(t, "vad-user-started-speaking")
        tl.rtvi(t + 0.18, "user-started-speaking")
        if start_turn:
            tl.tail(t + 0.18, "tail-turn", {"kind": "started", "turn": self.turn})
        _speech_levels(tl, t, t + speak_secs, "user-audio-level", loud=0.62)
        self.speaking_user.append((t, t + speak_secs))
        # Interim transcriptions grow as the user speaks.
        for i in range(1, len(words)):
            at = t + 0.3 + i * 0.26
            if i % 2 == 0 and at < t + speak_secs:
                tl.rtvi(
                    at,
                    "user-input",
                    {
                        "text": " ".join(words[:i]),
                        "input_type": "transcription",
                        "timestamp": _iso(at),
                        "final": False,
                        "user_id": "7c1f",
                    },
                )
        user_stop = t + speak_secs
        tl.rtvi(user_stop, "vad-user-stopped-speaking")
        final: dict[str, Any] = {
            "text": text,
            "input_type": "transcription",
            "timestamp": _iso(user_stop),
            "final": True,
            "user_id": "7c1f",
        }
        if tone:
            final["perception"] = [
                {
                    "name": "tone",
                    "labels": [{"label": tone[0], "confidence": tone[1]}],
                }
            ]
        tl.rtvi(user_stop + 0.09, "user-input", final)
        ruled = user_stop + 0.2
        tl.rtvi(ruled, "user-stopped-speaking")
        tl.tail(
            ruled,
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": STT,
                "model": "nova-3",
                "seconds": 0.09 + self.tl.rnd.random() * 0.03,
                "timestamp": ruled,
            },
        )
        tl.tail(
            ruled + 0.01,
            "tail-service-usage",
            {"kind": "stt", "processor": STT, "model": "nova-3", "audio_seconds": speak_secs},
        )
        tl.log(
            ruled + 0.01,
            "DEBUG",
            "pipecat.services.deepgram.stt",
            "_on_message",
            f'Transcription: "{text}"',
        )
        return user_stop, ruled

    def _bot_reply(
        self,
        t: float,
        bot: Bot,
        *,
        scene: Optional[Scene] = None,
        llm_ttfb: float,
        tts_ttfb: float,
        first: bool = False,
        waiting_since: float,
    ) -> float:
        tl = self.tl
        self.messages += 2
        if scene is not None and scene.photo:
            self.context.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": scene.user},
                        {"type": "image_url", "image_url": {"url": photo_url()}},
                    ],
                }
            )
        elif scene is not None:
            self.context.append({"role": "user", "content": scene.user})
        self._send_context(t + 0.01)
        if scene is not None:
            # Pipecat says what the LLM got of the user each time it sends it.
            tl.rtvi(t + 0.01, "user-llm-text", {"text": scene.user})
        cursor = t + 0.02
        tl.rtvi(cursor, "bot-llm-started")
        gen_start = cursor
        contributions: list[dict[str, Any]] = []
        endpointing = max(0.0, t - waiting_since)
        if not first:
            contributions.append(
                {
                    "key": "endpointing_wait",
                    "label": "endpointing wait",
                    "owner": "turn",
                    "start_time": waiting_since,
                    "duration_secs": round(endpointing, 3),
                }
            )
        # Tool calls: the LLM asks, the handler runs, the LLM answers.
        if scene and scene.calls:
            ask = llm_ttfb * 0.9
            contributions.append(
                {
                    "key": "llm_tool_call",
                    "label": "LLM tool call",
                    "owner": LLM,
                    "start_time": cursor,
                    "duration_secs": round(ask, 3),
                }
            )
            tl.tail(
                cursor + ask,
                "tail-service-latency",
                {
                    "kind": "ttfb",
                    "processor": LLM,
                    "model": MODEL,
                    "seconds": ask,
                    "timestamp": cursor + ask,
                },
            )
            cursor += ask
            for call in scene.calls:
                self.tool_id += 1
                call_id = f"call_{self.tool_id:03d}"
                tl.tail(
                    cursor,
                    "tail-function-call",
                    {
                        "kind": "function_call_started",
                        "tool_call_id": call_id,
                        "function_name": call.name,
                        "started_at": cursor,
                    },
                )
                tl.tail(
                    cursor + 0.01,
                    "tail-function-call",
                    {
                        "kind": "function_call_in_progress",
                        "tool_call_id": call_id,
                        "function_name": call.name,
                        "arguments": call.arguments,
                        "in_progress_at": cursor + 0.01,
                    },
                )
                tl.log(
                    cursor + 0.01,
                    "DEBUG",
                    "pipecat.services.llm_service",
                    "_run_function_call",
                    f"{LLM} Calling function [{call.name}:{call_id}] with arguments {call.arguments}",
                )
                done = cursor + 0.01 + call.secs
                tl.tail(
                    done,
                    "tail-function-call",
                    {
                        "kind": "function_call_completed",
                        "tool_call_id": call_id,
                        "function_name": call.name,
                        "result": call.result,
                        "timestamp": done,
                    },
                )
                contributions.append(
                    {
                        "key": "function_handler",
                        "label": "function handler",
                        "owner": call.name,
                        "start_time": cursor + 0.01,
                        "duration_secs": round(call.secs, 3),
                    }
                )
                cursor = done
            # The LLM asked for the tools; their results go back to it.
            self.context.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": f"call_{self.tool_id - len(scene.calls) + i + 1:03d}",
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for i, c in enumerate(scene.calls)
                    ],
                }
            )
            for i, c in enumerate(scene.calls):
                self.context.append(
                    {
                        "role": "tool",
                        "tool_call_id": f"call_{self.tool_id - len(scene.calls) + i + 1:03d}",
                        "content": json.dumps(c.result),
                    }
                )
            self._send_context(cursor)
            llm_ttfb = llm_ttfb * 0.25
        contributions.append(
            {
                "key": "llm_inference",
                "label": "LLM inference",
                "owner": LLM,
                "start_time": cursor,
                "duration_secs": round(llm_ttfb, 3),
            }
        )
        tl.tail(
            cursor + llm_ttfb,
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": LLM,
                "model": MODEL,
                "seconds": llm_ttfb,
                "timestamp": cursor + llm_ttfb,
            },
        )
        cursor += llm_ttfb

        # The LLM streams; the TTS speaks a sentence at a time.
        text = " ".join(bot.sentences)
        all_words = _words(text)
        stream = cursor
        for i, chunk in enumerate(_chunks(text)):
            tl.rtvi(stream + i * 0.03, "llm-raw-text", {"text": chunk})
        gen_end = stream + len(_chunks(text)) * 0.03
        tl.rtvi(gen_end, "bot-llm-stopped")
        self.generating.append((gen_start, gen_end))
        prompt = 900 + self.messages * 260
        tl.tail(
            gen_end + 0.01,
            "tail-service-usage",
            {
                "kind": "llm",
                "processor": LLM,
                "model": MODEL,
                "prompt_tokens": prompt,
                "completion_tokens": len(all_words) + 6,
                "total_tokens": prompt + len(all_words) + 6,
                "cache_read_input_tokens": int(prompt * 0.8),
            },
        )

        tl.rtvi(cursor + 0.02, "bot-tts-started")
        agg = 0.06
        contributions.append(
            {
                "key": "sentence_aggregation",
                "label": "sentence aggregation",
                "owner": TTS,
                "start_time": cursor,
                "duration_secs": agg,
            }
        )
        contributions.append(
            {
                "key": "speech_synthesis",
                "label": "speech synthesis",
                "owner": TTS,
                "start_time": cursor + agg,
                "duration_secs": round(tts_ttfb, 3),
            }
        )
        tl.tail(
            cursor + agg + tts_ttfb,
            "tail-service-latency",
            {
                "kind": "ttfb",
                "processor": TTS,
                "model": "sonic-2",
                "seconds": tts_ttfb,
                "timestamp": cursor + agg + tts_ttfb,
            },
        )
        out = 0.04
        contributions.append(
            {
                "key": "output_transport",
                "label": "output transport",
                "owner": "DailyOutputTransport#0",
                "start_time": cursor + agg + tts_ttfb,
                "duration_secs": out,
            }
        )
        speak = cursor + agg + tts_ttfb + out
        latency = speak - waiting_since
        tl.rtvi(speak, "bot-started-speaking")
        tl.tail(
            speak + 0.005,
            "tail-latency",
            {
                "latency_secs": round(latency, 3),
                "total_secs": round(latency, 3),
                "first_bot_speech": first,
                "contributions": contributions,
                "user_turn_start_time": waiting_since,
                "user_turn_secs": endpointing,
            },
        )

        # Speak word by word, a segment per sentence.
        word_index = 0
        cut: Optional[float] = None
        at = speak
        deliveries = bot.deliveries
        for sentence in bot.sentences:
            self.segment_id += 1
            seg = self.segment_id
            words = _words(sentence)
            tl.rtvi(
                at - 0.05 if at > speak else speak - 0.01,
                "bot-output",
                {
                    "text": sentence,
                    "output_type": "sentence",
                    "segment_id": seg,
                    "will_be_spoken": True,
                    "spoken_status": "new",
                    "spoken_progress": {"accumulated_text": "", "remaining_text": sentence},
                },
            )
            spoken_words: list[str] = []
            for w_i, word in enumerate(words):
                if scene and scene.interrupt_at is not None and word_index >= scene.interrupt_at:
                    cut = at
                    break
                spoken_words.append(word)
                accumulated = " ".join(spoken_words)
                rest = words[w_i + 1 :]
                remaining = (" " + " ".join(rest)) if rest else ""
                data: dict[str, Any] = {
                    "text": sentence,
                    "output_type": "sentence",
                    "segment_id": seg,
                    "will_be_spoken": True,
                    "spoken_status": "in-progress",
                    "spoken_progress": {
                        "accumulated_text": accumulated,
                        "remaining_text": remaining,
                    },
                }
                tags = _delivery_at(deliveries, word_index)
                if tags is not None:
                    data["delivery"] = tags
                tl.rtvi(at, "bot-output", data)
                tl.rtvi(at, "tts-raw-text", {"text": word})
                if scene and scene.backchannel_at == word_index:
                    tl.rtvi(at + 0.1, "vad-user-started-speaking")
                    _speech_levels(tl, at + 0.1, at + 0.45, "user-audio-level", loud=0.3)
                    tl.rtvi(at + 0.45, "vad-user-stopped-speaking")
                    tl.rtvi(
                        at + 0.6,
                        "user-input",
                        {
                            "text": "mhm",
                            "input_type": "backchannel",
                            "timestamp": _iso(at + 0.6),
                            "final": True,
                            "user_id": "7c1f",
                        },
                    )
                word_index += 1
                at += WORD_SECS * (1.0 + 0.25 * (len(word) > 6))
            if cut is not None:
                break
            tl.rtvi(
                at,
                "bot-output",
                {
                    "text": sentence,
                    "output_type": "sentence",
                    "segment_id": seg,
                    "will_be_spoken": True,
                    "spoken_status": "completed",
                    "spoken_progress": {"accumulated_text": sentence, "remaining_text": ""},
                },
            )
            at += 0.25
        end = cut if cut is not None else at
        _speech_levels(tl, speak, end, "bot-audio-level", loud=0.55)
        self.speaking_bot.append((speak, end))
        self.synthesizing.append((cursor + agg, end - 0.4 if cut is None else end))
        tl.rtvi(end, "bot-audio-level", {"value": 0.0})
        tl.rtvi(end + 0.01, "bot-stopped-speaking")
        tl.rtvi(end + 0.02, "bot-tts-stopped")
        tl.tail(
            end + 0.03,
            "tail-service-usage",
            {"kind": "tts", "processor": TTS, "model": "sonic-2", "characters": len(text)},
        )
        if scene and scene.error:
            tl.tail(
                speak + 1.4,
                "tail-error",
                {
                    "message": scene.error,
                    "category": "connectivity",
                    "processor": "DailyOutputTransport#0",
                    "processor_usable": True,
                    "timestamp": speak + 1.4,
                },
            )
            tl.log(
                speak + 1.4,
                "WARNING",
                "pipecat.transports.daily.transport",
                "on_track_stopped",
                f"DailyOutputTransport#0: {scene.error}",
            )
        # What the bot said goes into the context: only the spoken words if it was cut off.
        said = all_words[: scene.interrupt_at] if cut is not None and scene else all_words
        self.context.append({"role": "assistant", "content": " ".join(said)})
        if cut is not None:
            # The user barges in: VAD, interruption, a new turn.
            tl.rtvi(cut - 0.25, "vad-user-started-speaking")
            tl.rtvi(cut, "bot-interrupted")
            tl.log(
                cut, "DEBUG", "pipecat.pipeline.worker", "_interrupt", "Bot interrupted by the user"
            )
            return cut - 0.25
        tl.rtvi(
            end + 0.04,
            "bot-llm-marker",
            {"text": "✓", "kind": "complete", "raw": "", "markers": ["✓", "○", "◐"]},
        )
        return end

    # Ambient: background noise and frame flow.

    def ambient(self, t0: float, t1: float) -> None:
        tl = self.tl
        busy = sorted(self.speaking_user)
        t = t0
        while t < t1:
            if not any(a - 0.1 <= t <= b + 0.6 for a, b in busy):
                tl.rtvi(t, "user-audio-level", {"value": round(0.012 + 0.01 * tl.rnd.random(), 3)})
            t += LEVEL_PERIOD
        t = t0
        while t < t1:
            user = any(a <= t <= b for a, b in self.speaking_user)
            gen = any(a <= t <= b for a, b in self.generating)
            synth = any(a <= t <= b for a, b in self.synthesizing)
            bot = any(a <= t <= b for a, b in self.speaking_bot)
            n = 12.5  # frames per 0.25 s at 50 fps
            procs: dict[str, dict[str, dict[str, int]]] = {
                "DailyInputTransport#0": {"out": {"audio": int(n), "system": 1 if user else 0}},
                STT: {"in": {"audio": int(n)}, "out": {"audio": int(n), "data": 2 if user else 0}},
                "LLMUserAggregator#0": {
                    "in": {"audio": int(n), "data": 2 if user else 0},
                    "out": {"audio": int(n), "data": 1 if gen else 0},
                },
                LLM: {"in": {"audio": int(n)}, "out": {"audio": int(n), "data": 9 if gen else 0}},
                TTS: {
                    "in": {"audio": int(n), "data": 9 if gen else 0},
                    "out": {"audio": int(n) + (12 if synth else 0), "data": 3 if bot else 0},
                },
                "DailyOutputTransport#0": {
                    "in": {"audio": 12 if bot else 0},
                    "out": {"system": 3 if bot else 0},
                },
                "LLMAssistantAggregator#0": {"in": {"data": 3 if bot else 0}},
            }
            tl.tail(t, "tail-flow", {"interval_secs": 0.25, "processors": procs})
            t += 0.25


def _delivery_at(deliveries: dict[int, list[str]], index: int) -> Optional[list[str]]:
    if not deliveries:
        return None
    current: list[str] = []
    for start in sorted(deliveries):
        if start <= index:
            current = deliveries[start]
    # A delivery lasts for the rest of its sentence in this script; keep it
    # for six words, then back to the baseline.
    last = max((s for s in deliveries if s <= index), default=None)
    if last is not None and index - last >= 6:
        return []
    return current


def _chunks(text: str) -> list[str]:
    words = text.split(" ")
    chunks = []
    for i in range(0, len(words), 2):
        piece = " ".join(words[i : i + 2])
        chunks.append(piece if i == 0 else " " + piece)
    return chunks


def _chunks_of_session(
    seed: int, loops: Optional[int]
) -> Iterator[list[tuple[float, dict[str, Any]]]]:
    """The session in chunks of one pass through the script, in order."""
    writer = _Writer(seed)
    t = writer.start(0.0)
    ambient_from = t
    t = writer.greeting(t)
    done = 0
    while loops is None or done < loops:
        for scene in SCRIPT:
            t = writer.scene(t, scene)
        writer.ambient(ambient_from, t)
        ambient_from = t
        events = sorted(writer.tl.events, key=lambda e: (e[0], e[1]))
        writer.tl.events = []
        for ranges in (
            writer.speaking_user,
            writer.generating,
            writer.synthesizing,
            writer.speaking_bot,
        ):
            ranges.clear()
        yield [(offset, message) for offset, _, message in events]
        done += 1


def build_session(seed: int = 7, loops: int = 1) -> list[tuple[float, dict[str, Any]]]:
    """The whole scripted session as ``(offset_secs, message)``, in order.

    Args:
        seed: Seed for the audio levels.
        loops: How many times to go through the script after the greeting.
    """
    session: list[tuple[float, dict[str, Any]]] = []
    for chunk in _chunks_of_session(seed, loops):
        session.extend(chunk)
    return session


_TIME_KEYS = {"started_at", "in_progress_at", "timestamp", "start_time", "user_turn_start_time"}


def _rebase(value: Any, base: float) -> Any:
    # The script writes times as offsets from the start of the session.
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _TIME_KEYS and isinstance(v, (int, float)) and v < 1e8:
                out[k] = base + v
            else:
                out[k] = _rebase(v, base)
        return out
    if isinstance(value, list):
        return [_rebase(v, base) for v in value]
    return value


def _stamp(
    message: dict[str, Any], timestamp: float, base: Optional[float] = None
) -> dict[str, Any]:
    """Stamp a scripted message as sent at ``timestamp``, its times relative to ``base``."""
    stamped = dict(message)
    stamped["timestamp"] = timestamp
    data = stamped.get("data")
    if isinstance(data, dict):
        if base is not None:
            data = _rebase(data, base)
        if stamped.get("type") == "tail-log":
            data = dict(data, time=_iso(timestamp))
        elif stamped.get("type") == "user-input":
            data = dict(data, timestamp=_iso(timestamp))
        stamped["data"] = data
    return stamped


def messages(
    base: Optional[float] = None, seed: int = 7, loops: int = 1
) -> Iterator[dict[str, Any]]:
    """The session's messages, stamped as if it started at ``base``, without waiting."""
    base = time.time() if base is None else base
    for offset, message in build_session(seed, loops):
        yield _stamp(message, base + offset, base)


async def play(loop: bool = True, speed: float = 1.0) -> AsyncIterator[dict[str, Any]]:
    """Play the session in real time, stamped with the current time.

    Args:
        loop: Keep going through the script until cancelled.
        speed: Playback speed; timestamps follow the playback.
    """
    base = time.time()
    for chunk in _chunks_of_session(7, None if loop else 1):
        for offset, message in chunk:
            at = base + offset / speed
            delay = at - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            yield _stamp(message, time.time(), base)
