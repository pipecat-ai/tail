#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""What the Pipecat Tail runs in can do.

Tail supports every Pipecat 1.x release. Rather than comparing version
numbers, it looks for the classes and settings it uses and falls back to
RTVI messages, which every 1.x release sends, for anything missing:

- 1.0 to 1.2 run pipelines with ``PipelineTask`` and ``PipelineRunner``;
  1.3 added ``WorkerRunner``, ``PipelineWorker``, the worker bus and jobs.
- 1.8 added raw VAD messages to RTVI and ``on_processor_setup``.
- 1.9 added the error, function call and service metrics observers. Before
  that, Tail reads RTVI metrics and function call messages instead.
- 1.12 stopped Pipecat's observers from de-duplicating the frames they see
  and tells observers which push of a frame is the first one.
- 1.13 added ``user-input`` and the raw text messages to RTVI and deprecated
  ``user-transcription``, ``bot-llm-text`` and ``bot-tts-text``, which 2.0
  removes.

Importing this module imports Pipecat.
"""

import dataclasses
import importlib
from typing import Any, Optional

from pipecat.processors.frameworks.rtvi.observer import (
    RTVIFunctionCallReportLevel,
    RTVIObserverParams,
)


def _optional(module: str, name: str) -> Optional[Any]:
    try:
        return getattr(importlib.import_module(module), name)
    except Exception:
        return None


ErrorObserver = _optional("pipecat.observers.error_observer", "ErrorObserver")
FunctionCallObserver = _optional("pipecat.observers.function_call_observer", "FunctionCallObserver")
ServiceMetricsObserver = _optional(
    "pipecat.observers.service_metrics_observer", "ServiceMetricsObserver"
)

HAS_WORKERS = _optional("pipecat.workers.runner", "WorkerRunner") is not None
"""Whether Pipecat has ``WorkerRunner``, workers and the worker bus (1.3+)."""

RTVI_PARAMS: frozenset[str] = frozenset(f.name for f in dataclasses.fields(RTVIObserverParams))
"""Names of the ``RTVIObserverParams`` settings this Pipecat has."""

HAS_USER_INPUT = "user_input_enabled" in RTVI_PARAMS
"""Whether RTVI has ``user-input`` and the raw text messages (1.13+)."""

DEPRECATED_RTVI_MESSAGES: frozenset[str] = (
    frozenset({"user-transcription", "bot-llm-text", "bot-tts-text", "bot-transcription"})
    if HAS_USER_INPUT
    else frozenset({"bot-transcription"})
)
"""RTVI messages Tail does not forward because a newer message carries the same.

``bot-transcription`` is never forwarded: Tail reads the bot's words from
``bot-output``.
"""


def rtvi_params(**settings: Any) -> RTVIObserverParams:
    """Build ``RTVIObserverParams`` from the settings this Pipecat knows.

    Settings Pipecat does not have are dropped, so callers can ask for the
    newest behavior and still run on older releases.

    Args:
        **settings: ``RTVIObserverParams`` fields.

    Returns:
        The parameters.
    """
    return RTVIObserverParams(**{k: v for k, v in settings.items() if k in RTVI_PARAMS})


def default_rtvi_params() -> RTVIObserverParams:
    """RTVI observer settings Tail uses.

    Audio levels are Tail's own, sampled often enough to draw the voice
    timeline. Raw VAD, user input, raw text and LLM markers are on where Pipecat has
    them. Metrics and function call reports are on only when the dedicated
    observers that carry richer versions of them are missing. System logs
    are off because Tail has its own log sink.
    """
    full_function_calls = FunctionCallObserver is None
    return rtvi_params(
        audio_level_period_secs=0.08,
        vad_user_speaking_enabled=True,
        user_input_enabled=True,
        llm_raw_text_enabled=True,
        tts_raw_text_enabled=True,
        bot_llm_marker_enabled=True,
        metrics_enabled=ServiceMetricsObserver is None,
        system_logs_enabled=False,
        function_call_report_level={
            "*": RTVIFunctionCallReportLevel.FULL
            if full_function_calls
            else RTVIFunctionCallReportLevel.DISABLED
        },
    )


def capabilities() -> list[str]:
    """Features the observer can report with this Pipecat.

    The app uses them to tell an empty view apart from one that needs a
    newer Pipecat.
    """
    caps = [
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
    ]
    if "vad_user_speaking_enabled" in RTVI_PARAMS:
        caps.append("vad")
    if HAS_USER_INPUT:
        caps.append("user-input")
    if "bot_llm_marker_enabled" in RTVI_PARAMS:
        caps.append("llm-marker")
    if HAS_WORKERS:
        caps.append("workers")
    return caps


def first_push(data: Any) -> bool:
    """Whether a ``FramePushed`` is the first push of its frame.

    Pipecat before 1.12 does not say, so every push counts as a first one and
    callers de-duplicate by frame id.
    """
    return bool(getattr(data, "first_push", True))


def knows_first_push(data: Any) -> bool:
    """Whether Pipecat tells first pushes apart (1.12+)."""
    return hasattr(data, "first_push")


def observes_every_push(observer: Any) -> bool:
    """Whether an observer wants every push of a frame or only the first (1.12+)."""
    return bool(getattr(observer, "observe_every_push", True))
