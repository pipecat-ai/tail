#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail's colors and Textual themes.

Color follows the conversation. The user is blue and the bot is pink. The
stages of a response take the color of the part of the cycle they belong to:
teal while the bot listens (VAD, transcription, turn detection), violet while
it thinks (the LLM, and its tools a shade darker) and pink while it speaks
(speech synthesis a shade lighter than the bot). Green, amber and red only
grade or flag something, always next to a symbol or a word. Widgets draw Rich
text with ``current()``, which follows whether the app's theme is dark or
light.
"""

from dataclasses import dataclass

from rich.style import Style
from textual.theme import Theme


@dataclass(frozen=True)
class Palette:
    """Colors for Rich text drawn by the widgets."""

    dark: bool
    ink: str
    surface: str
    raised: str
    border: str
    grid: str
    muted: str
    dim: str
    text: str
    bright: str
    accent: str
    user: str
    bot: str
    llm: str
    stt: str
    tts: str
    tool: str
    ok: str
    warn: str
    err: str
    vad: str
    sel: str
    """Behind a selected row: the one background besides key caps."""
    user_wave: tuple[str, str, str]
    bot_wave: tuple[str, str, str]
    meter: tuple[str, ...]


DARK = Palette(
    dark=True,
    ink="#0b0f14",
    surface="#121820",
    raised="#19212c",
    border="#2f3b4e",
    grid="#1d2531",
    muted="#5d6b80",
    dim="#8592a6",
    text="#c9d3e0",
    bright="#f2f6fb",
    accent="#1bebb9",
    user="#4cc2ff",
    bot="#ff6ec7",
    llm="#b18cff",
    stt="#2f8f83",
    tts="#ffa3d9",
    tool="#8a78f0",
    ok="#4be08a",
    warn="#ffd166",
    err="#ff5f45",
    vad="#2f8f83",
    sel="#24324a",
    user_wave=("#0f4c6e", "#4cc2ff", "#d8f3ff"),
    bot_wave=("#6b1f4f", "#ff6ec7", "#ffd9ef"),
    meter=("#1f6feb", "#22c1c3", "#4be08a", "#ffd166", "#ff8c42", "#ff3d5a"),
)

LIGHT = Palette(
    dark=False,
    ink="#fbfcfd",
    surface="#f1f4f7",
    raised="#e6ebf1",
    border="#c5cfdb",
    grid="#e3e8ee",
    muted="#8792a2",
    dim="#5d687a",
    text="#27303d",
    bright="#0b0f14",
    accent="#08977a",
    user="#0a7fc2",
    bot="#c4237f",
    llm="#7449d6",
    stt="#1d7a76",
    tts="#8a1a5c",
    tool="#4f2fa8",
    ok="#16914a",
    warn="#a87b00",
    err="#d6401f",
    vad="#1d7a76",
    sel="#d5deea",
    user_wave=("#9fd3f0", "#0a7fc2", "#063d5e"),
    bot_wave=("#f2a9d1", "#c4237f", "#5e0f3c"),
    meter=("#1f6feb", "#139fa1", "#16914a", "#c99a00", "#e0661a", "#d62839"),
)

_current = DARK


def style(role: str, **attrs) -> Style:
    """A Rich style in the current palette's color for ``role`` (``text``, ``err``…)."""
    return Style(color=getattr(_current, role), **attrs)


def current() -> Palette:
    """The palette of the app's current theme."""
    return _current


def use_dark(dark: bool) -> None:
    """Switch the palette to follow a dark or light theme."""
    global _current
    _current = DARK if dark else LIGHT


TAIL_DARK = Theme(
    name="tail",
    primary=DARK.accent,
    secondary=DARK.user,
    accent=DARK.bot,
    warning=DARK.warn,
    error=DARK.err,
    success=DARK.ok,
    foreground=DARK.text,
    background=DARK.ink,
    surface=DARK.surface,
    panel=DARK.raised,
    dark=True,
    variables={
        "border": DARK.border,
        "border-blurred": DARK.border,
        "footer-background": DARK.surface,
        "footer-key-foreground": DARK.accent,
        "block-cursor-background": DARK.raised,
        "block-cursor-foreground": DARK.bright,
        "input-selection-background": "#1bebb940",
        "scrollbar": DARK.raised,
        "scrollbar-hover": DARK.border,
        "scrollbar-active": DARK.muted,
        "scrollbar-background": DARK.ink,
    },
)

TAIL_LIGHT = Theme(
    name="tail-light",
    primary=LIGHT.accent,
    secondary=LIGHT.user,
    accent=LIGHT.bot,
    warning=LIGHT.warn,
    error=LIGHT.err,
    success=LIGHT.ok,
    foreground=LIGHT.text,
    background=LIGHT.ink,
    surface=LIGHT.surface,
    panel=LIGHT.raised,
    dark=False,
    variables={
        "border": LIGHT.border,
        "border-blurred": LIGHT.border,
        "footer-background": LIGHT.surface,
        "footer-key-foreground": LIGHT.accent,
        "block-cursor-background": LIGHT.raised,
        "block-cursor-foreground": LIGHT.bright,
        "scrollbar": LIGHT.raised,
        "scrollbar-hover": LIGHT.border,
        "scrollbar-active": LIGHT.muted,
        "scrollbar-background": LIGHT.ink,
    },
)

THEMES = (TAIL_DARK, TAIL_LIGHT)
THEME_CYCLE = ("tail", "tail-light", "nord", "dracula", "tokyo-night", "gruvbox")

# Latency contribution colors by key, as roles of the palette.
CONTRIBUTION_ROLES = {
    "endpointing_wait": "vad",
    "first_request": "vad",
    "transcription": "stt",
    "turn_detection": "vad",
    "llm_inference": "llm",
    "turn_completion": "llm",
    "waiting_for_user": "muted",
    "llm_tool_call": "llm",
    "function_handler": "tool",
    "sentence_aggregation": "bot",
    "awaiting_speakable_text": "bot",
    "speech_synthesis": "tts",
    "output_transport": "dim",
    "service": "dim",
}

# Short names for contribution keys, in the words of someone tuning a bot.
CONTRIBUTION_NAMES = {
    "endpointing_wait": "end of turn",
    "first_request": "first request",
    "transcription": "stt",
    "turn_detection": "turn detection",
    "llm_inference": "llm",
    "turn_completion": "turn completion",
    "waiting_for_user": "waiting",
    "llm_tool_call": "llm → tool",
    "function_handler": "tool",
    "sentence_aggregation": "first sentence",
    "awaiting_speakable_text": "first sentence",
    "speech_synthesis": "tts",
    "output_transport": "audio out",
    "service": "service",
}


def contribution_color(key: str) -> str:
    """Color of a latency contribution."""
    p = current()
    return getattr(p, CONTRIBUTION_ROLES.get(key, "dim"))


def processor_color(name: str) -> str:
    """Color of a processor by the kind of service its name suggests."""
    p = current()
    if "Transport" in name or "Input" in name and "Transport" in name:
        return p.accent
    if "STT" in name or "Transcri" in name:
        return p.stt
    if "TTS" in name:
        return p.tts
    if "LLM" in name and "Aggregator" not in name and "Context" not in name:
        return p.llm
    if "VAD" in name or "Turn" in name:
        return p.vad
    return p.dim


LATENCY_TARGET_SECS = 1.0
"""Answers faster than this are on target; ``--target`` changes it."""

SLOW_FACTOR = 1.6
"""Answers slower than the target times this are too slow."""


def set_latency_target(seconds: float) -> None:
    """Set the response time the bot aims for."""
    global LATENCY_TARGET_SECS
    LATENCY_TARGET_SECS = max(0.05, float(seconds))


def stage_name(key: str) -> str:
    """Short name of a stage of an answer."""
    return CONTRIBUTION_NAMES.get(key, key.replace("_", " "))


def latency_target() -> float:
    """The response time the bot aims for."""
    return LATENCY_TARGET_SECS


def on_target(seconds: float) -> bool:
    """Whether a response time is under the target."""
    return seconds < LATENCY_TARGET_SECS


def latency_grade(seconds: float | None) -> str:
    """``on target``, ``slow`` or ``too slow``, so latency never relies on color alone."""
    if seconds is None:
        return ""
    if on_target(seconds):
        return "on target"
    if seconds < LATENCY_TARGET_SECS * SLOW_FACTOR:
        return "slow"
    return "too slow"


def latency_color(seconds: float | None) -> str:
    """Green on target, amber slow, red too slow (see ``latency_grade``)."""
    p = current()
    if seconds is None:
        return p.dim
    return {"on target": p.ok, "slow": p.warn}.get(latency_grade(seconds), p.err)
