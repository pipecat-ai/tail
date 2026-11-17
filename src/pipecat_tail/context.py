#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""What the LLM saw: the context, in a form small enough to send to Tail.

When a bot says something odd, the first question is what was in its
context. The observer sends the context every time it goes to the LLM, but
only the messages that changed since the last time: a context grows by a few
messages a turn, so sending all of it every turn would grow with the square
of the conversation. Long texts are clipped and images become small
thumbnails, so a multimodal bot's context stays a few kilobytes a turn.

Nothing here imports Pipecat; the messages are plain OpenAI-style dicts or
objects with ``llm`` and ``message`` attributes (Pipecat's
``LLMSpecificMessage``).
"""

import base64
import binascii
import io
import json
from typing import Any, Optional

TEXT_LIMIT = 4000
"""Characters kept of a message's text."""

SYSTEM_LIMIT = 16000
"""Characters kept of the system instruction."""

ARGUMENTS_LIMIT = 1000
"""Characters kept of a tool call's arguments."""

THUMBNAIL_SIZE = (64, 48)
"""Largest thumbnail, in pixels. Tail draws two pixels per terminal cell."""


def clip(text: Any, limit: int) -> str:
    """Clip text to ``limit`` characters, saying how much was left out."""
    text = text if isinstance(text, str) else _jsonish(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [+{len(text) - limit:,} characters]"


def _jsonish(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def fingerprint(message: Any) -> tuple:
    """A cheap identity for a context message.

    Contexts are append-mostly lists of the same objects, so the object's id
    plus a little of its content tells a kept message from a new or edited
    one without serializing it.
    """
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            sample: Any = (len(content), content[:64])
        elif isinstance(content, list):
            sample = (len(content), str(content[0])[:64] if content else "")
        else:
            sample = repr(content)[:64]
        calls = message.get("tool_calls")
        return (id(message), message.get("role"), sample, len(calls) if calls else 0)
    return (id(message), type(message).__name__)


def changed_from(previous: list[tuple], current: list[tuple]) -> int:
    """Index of the first message that differs: everything before it was already sent."""
    start = 0
    for a, b in zip(previous, current):
        if a != b:
            break
        start += 1
    return start


def thumbnail(url: str) -> Optional[dict[str, Any]]:
    """Shrink a ``data:image/...;base64,`` URL to a small RGB thumbnail.

    Returns:
        ``{"width", "height", "rgb", "source_width", "source_height"}`` with
        ``rgb`` the base64 of the raw pixels, or ``None`` if the image can't
        be read (or Pillow isn't installed).
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        header, _, payload = url.partition(",")
        if ";base64" not in header:
            return None
        image = Image.open(io.BytesIO(base64.b64decode(payload)))
        source = image.size
        image = image.convert("RGB")
        image.thumbnail(THUMBNAIL_SIZE)
        return {
            "width": image.width,
            "height": image.height,
            "rgb": base64.b64encode(image.tobytes()).decode("ascii"),
            "source_width": source[0],
            "source_height": source[1],
        }
    except (binascii.Error, OSError, ValueError):
        return None


def _media(part: dict[str, Any]) -> dict[str, Any]:
    kind = str(part.get("type", "other"))
    if kind == "image_url":
        image = part.get("image_url")
        url = image.get("url", "") if isinstance(image, dict) else str(image or "")
        if url.startswith("data:image"):
            thumb = thumbnail(url)
            return {"kind": "image", **(thumb or {})}
        return {"kind": "image", "url": clip(url, 200)}
    if kind == "input_audio":
        return {"kind": "audio"}
    if kind.startswith("file"):
        name = part.get("filename") or (part.get("file") or {}).get("filename")
        return {"kind": "file", "name": str(name) if name else None}
    return {"kind": kind}


def item(message: Any) -> dict[str, Any]:
    """One context message as Tail sends it.

    Returns:
        ``role`` and ``text``, plus ``media`` (images as thumbnails, audio,
        files), ``tool_calls`` (``id``, ``name``, ``arguments``),
        ``tool_call_id`` and ``name`` when the message has them. A message in
        one LLM's own format has role ``specific``, the LLM's name in ``llm``
        and its JSON as text.
    """
    if not isinstance(message, dict):
        inner = getattr(message, "message", message)
        return {
            "role": "specific",
            "llm": str(getattr(message, "llm", "") or ""),
            "text": clip(_jsonish(inner), TEXT_LIMIT),
        }
    result: dict[str, Any] = {"role": str(message.get("role") or "other")}
    content = message.get("content")
    texts: list[str] = []
    media: list[dict[str, Any]] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                texts.append(str(part.get("text", "")))
            else:
                media.append(_media(part))
    elif content is not None:
        texts.append(_jsonish(content))
    result["text"] = clip("\n".join(t for t in texts if t), TEXT_LIMIT)
    if media:
        result["media"] = media
    calls = message.get("tool_calls")
    if isinstance(calls, list):
        result["tool_calls"] = [
            {
                "id": call.get("id"),
                "name": (call.get("function") or {}).get("name"),
                "arguments": clip(
                    (call.get("function") or {}).get("arguments", ""), ARGUMENTS_LIMIT
                ),
            }
            for call in calls
            if isinstance(call, dict)
        ]
    for key in ("tool_call_id", "name"):
        if message.get(key):
            result[key] = str(message[key])
    return result


def summary(messages: list[Any]) -> dict[str, Any]:
    """Counts of a context: messages, messages by role, characters and images."""
    roles: dict[str, int] = {}
    characters = 0
    images = 0
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else "specific"
        role = role if isinstance(role, str) else "other"
        roles[role] = roles.get(role, 0) + 1
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            characters += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        characters += len(str(part.get("text", "")))
                    elif "image" in str(part.get("type", "")):
                        images += 1
    return {"messages": len(messages), "roles": roles, "characters": characters, "images": images}


def tool_schemas(tools: Any) -> list[dict[str, Any]]:
    """The tools offered to the LLM: name, description and parameters."""
    schemas = []
    for tool in getattr(tools, "standard_tools", None) or []:
        name = getattr(tool, "name", None)
        if not isinstance(name, str):
            continue
        properties = getattr(tool, "properties", None)
        required = getattr(tool, "required", None)
        schemas.append(
            {
                "name": name,
                "description": clip(getattr(tool, "description", "") or "", 600),
                "parameters": properties if isinstance(properties, dict) else {},
                "required": list(required) if isinstance(required, (list, tuple)) else [],
            }
        )
    return schemas


def system_instruction(llm: Any) -> Optional[str]:
    """The LLM service's system instruction, if it keeps one in its settings."""
    settings = getattr(llm, "_settings", None)
    value = getattr(settings, "system_instruction", None)
    if value is None and isinstance(settings, dict):
        value = settings.get("system_instruction")
    return clip(value, SYSTEM_LIMIT) if isinstance(value, str) and value else None


class ContextTracker:
    """Remembers what was sent of each LLM's context, to send only what changed."""

    def __init__(self):
        """Create an empty tracker."""
        self._sent: dict[str, list[tuple]] = {}
        self._system: dict[str, Optional[str]] = {}
        self._tools: dict[str, str] = {}

    def update(self, llm_name: str, llm: Any, context: Any) -> dict[str, Any]:
        """Build the message data for a context about to go to an LLM.

        Returns:
            The summary counts and tool names (as before), plus ``start`` and
            ``items``, the messages from ``start`` on; ``system`` and
            ``tool_schemas`` when they changed since the last time.
        """
        try:
            messages = list(context.get_messages())
        except Exception:
            messages = list(getattr(context, "messages", None) or [])
        prints = [fingerprint(m) for m in messages]
        start = changed_from(self._sent.get(llm_name, []), prints)
        self._sent[llm_name] = prints
        data: dict[str, Any] = {"llm": llm_name, **summary(messages)}
        schemas = tool_schemas(getattr(context, "tools", None))
        data["tools"] = [s["name"] for s in schemas]
        data["start"] = start
        data["items"] = [item(m) for m in messages[start:]]
        system = system_instruction(llm)
        if system != self._system.get(llm_name):
            self._system[llm_name] = system
            data["system"] = system or ""
        tools_key = _jsonish(schemas)
        if tools_key != self._tools.get(llm_name):
            self._tools[llm_name] = tools_key
            data["tool_schemas"] = schemas
        return data
