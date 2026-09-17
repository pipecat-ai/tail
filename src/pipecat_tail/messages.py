#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Messages exchanged between a Tail observer or server and the Tail app.

Tail speaks two kinds of messages over the same connection:

- RTVI messages, produced by Pipecat's ``RTVIObserver`` and forwarded verbatim.
  They carry ``label: "rtvi-ai"``.
- Tail messages, produced by Tail itself from the other Pipecat observers and
  from the worker bus. They carry ``label: "tail"``.

Every message is a JSON object. When the message comes from a specific
pipeline worker it also carries a ``worker`` field with the worker name so the
app can scope views to one worker.
"""

import enum
import platform
import time
from dataclasses import fields, is_dataclass
from importlib.metadata import version
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field

TAIL_LABEL = "tail"
"""Label carried by every message Tail generates itself."""

RTVI_LABEL = "rtvi-ai"
"""Label carried by RTVI messages forwarded from Pipecat."""

PROTOCOL_VERSION = "2"
"""Version of the Tail wire protocol."""


def _package_version(name: str) -> str:
    try:
        return version(name)
    except Exception:
        return "unknown"


def system_info() -> dict[str, str]:
    """Return version information about the process Tail is running in.

    Returns:
        Mapping with ``pipecat``, ``tail``, ``python`` and ``platform`` keys.
    """
    return {
        "pipecat": _package_version("pipecat-ai"),
        "tail": _package_version("pipecat-ai-tail"),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def tail_serializer(obj: Any) -> Any:
    """Convert an arbitrary Pipecat object into JSON-compatible data.

    Dataclasses and pydantic models become mappings, enums become their value,
    sequences become lists and anything else becomes a short type marker. Large
    binary payloads (audio, images) are replaced by their size.

    Args:
        obj: The object to convert.

    Returns:
        JSON-compatible data.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return f"<{len(obj)} bytes>"
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, BaseModel):
        return tail_serializer(obj.model_dump(exclude_none=True))
    if is_dataclass(obj) and not isinstance(obj, type):
        values = {}
        for f in fields(obj):
            # Read the raw instance state so deprecated fields don't warn.
            value = object.__getattribute__(obj, f.name)
            if value is not None:
                values[f.name] = tail_serializer(value)
        return values
    if isinstance(obj, Mapping):
        return {str(k): tail_serializer(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [tail_serializer(v) for v in obj if v is not None]
    name = getattr(obj, "name", None)
    if isinstance(name, str):
        return name
    return f"<{type(obj).__name__}>"


class TailMessage(BaseModel):
    """Base class for every message Tail generates itself.

    Subclasses add a ``type`` literal.
    """

    label: Literal["tail"] = TAIL_LABEL
    worker: Optional[str] = None
    timestamp: float = Field(default_factory=time.time)


class TailReadyMessage(TailMessage):
    """First message sent to a client, describing the process Tail runs in."""

    type: Literal["tail-ready"] = "tail-ready"
    data: dict[str, Any] = Field(default_factory=system_info)


class TailPipelineStartedMessage(TailMessage):
    """A pipeline worker started, with its ordered processor names."""

    type: Literal["tail-pipeline-started"] = "tail-pipeline-started"
    data: dict[str, Any]


class TailPipelineFinishedMessage(TailMessage):
    """A pipeline worker finished."""

    type: Literal["tail-pipeline-finished"] = "tail-pipeline-finished"


class TailTurnMessage(TailMessage):
    """A conversation turn started or ended.

    ``data`` carries ``kind`` (``started`` or ``ended``), ``turn`` and, when
    ended, ``duration_secs`` and ``was_interrupted``.
    """

    type: Literal["tail-turn"] = "tail-turn"
    data: dict[str, Any]


class TailLatencyMessage(TailMessage):
    """User-to-bot latency for one turn, with its breakdown.

    ``data`` carries ``latency_secs``, ``first_bot_speech`` and the fields of
    Pipecat's ``LatencyBreakdown``.
    """

    type: Literal["tail-latency"] = "tail-latency"
    data: dict[str, Any]


class TailStartupMessage(TailMessage):
    """Pipecat's ``StartupTimingReport`` for a pipeline start."""

    type: Literal["tail-startup"] = "tail-startup"
    data: dict[str, Any]


class TailTransportTimingMessage(TailMessage):
    """Pipecat's ``TransportTimingReport`` for a pipeline start."""

    type: Literal["tail-transport-timing"] = "tail-transport-timing"
    data: dict[str, Any]


class TailErrorMessage(TailMessage):
    """Pipecat's ``ErrorEvent`` for an ``ErrorFrame``."""

    type: Literal["tail-error"] = "tail-error"
    data: dict[str, Any]


class TailSpeechMessage(TailMessage):
    """Pipecat's ``SpeechEvent``: raw VAD, turn ruling, bot speech, interruption."""

    type: Literal["tail-speech"] = "tail-speech"
    data: dict[str, Any]


class TailFunctionCallMessage(TailMessage):
    """Pipecat's ``FunctionCallEvent`` for a tool call lifecycle step."""

    type: Literal["tail-function-call"] = "tail-function-call"
    data: dict[str, Any]


class TailServiceLatencyMessage(TailMessage):
    """A service latency record: ``ttfb``, ``ttfa``, ``ttfat`` or ``processing``."""

    type: Literal["tail-service-latency"] = "tail-service-latency"
    data: dict[str, Any]


class TailServiceUsageMessage(TailMessage):
    """A service usage record: LLM tokens, STT audio seconds or TTS characters."""

    type: Literal["tail-service-usage"] = "tail-service-usage"
    data: dict[str, Any]


class TailLogMessage(TailMessage):
    """A structured log record.

    ``data`` carries ``time`` (ISO 8601), ``level``, ``name``, ``function``,
    ``line`` and ``message``.
    """

    type: Literal["tail-log"] = "tail-log"
    data: dict[str, Any]


class TailWorkersMessage(TailMessage):
    """The worker registry of a runner: every known worker and its state."""

    type: Literal["tail-workers"] = "tail-workers"
    data: dict[str, Any]


class TailWorkerReadyMessage(TailMessage):
    """A worker became ready."""

    type: Literal["tail-worker-ready"] = "tail-worker-ready"
    data: dict[str, Any]


class TailWorkerErrorMessage(TailMessage):
    """A worker reported an error."""

    type: Literal["tail-worker-error"] = "tail-worker-error"
    data: dict[str, Any]


class TailJobMessage(TailMessage):
    """A step in a job lifecycle.

    ``data`` carries ``kind`` (``requested``, ``updated``, ``responded``,
    ``cancelled``, ``stream_start``, ``stream_data`` or ``stream_end``),
    ``job_id``, ``source``, ``target`` and, depending on the kind,
    ``job_name``, ``status``, ``payload``, ``response``, ``update`` or ``data``.
    """

    type: Literal["tail-job"] = "tail-job"
    data: dict[str, Any]


class TailBusMessage(TailMessage):
    """A message seen on the worker bus.

    ``data`` carries ``name``, ``message_type``, ``category`` (``frame``,
    ``job``, ``lifecycle`` or ``other``), ``source``, ``target`` and a
    serialized ``payload``.
    """

    type: Literal["tail-bus"] = "tail-bus"
    data: dict[str, Any]


def message_label(message: Mapping[str, Any]) -> Optional[str]:
    """Return the label of a wire message, or ``None`` when it has none.

    Args:
        message: A decoded wire message.
    """
    label = message.get("label")
    return label if isinstance(label, str) else None
