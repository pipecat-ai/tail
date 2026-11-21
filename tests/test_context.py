"""What the observer sends of the LLM context: only what changed, small."""

import base64

from pipecat_tail import context, demo


class FakeContext:
    def __init__(self, messages, tools=None):
        self.messages = messages
        self.tools = tools

    def get_messages(self):
        return self.messages


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"{name} does things"
        self.properties = {"city": {"type": "string"}}
        self.required = ["city"]


class FakeTools:
    def __init__(self, *names):
        self.standard_tools = [FakeTool(n) for n in names]


def test_tracker_sends_the_whole_context_rendering_only_new_messages():
    messages = [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "weather in Lisbon?"},
    ]
    ctx = FakeContext(messages, FakeTools("get_weather"))
    tracker = context.ContextTracker()

    first = tracker.update("llm", None, ctx)
    assert [i["role"] for i in first["items"]] == ["system", "user"]
    assert first["messages"] == 2 and first["roles"] == {"system": 1, "user": 1}
    assert first["tool_schemas"][0]["name"] == "get_weather"
    assert first["tool_schemas"][0]["required"] == ["city"]

    messages.append(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "Lisbon"}'},
                }
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": "call_1", "content": '{"sky": "sunny"}'})
    second = tracker.update("llm", None, ctx)
    # The whole context again, so a client that missed the first still has it.
    assert len(second["items"]) == 4
    assert second["items"][2]["tool_calls"][0]["name"] == "get_weather"
    assert second["items"][3]["tool_call_id"] == "call_1"
    assert second["tool_schemas"] == first["tool_schemas"] and "system" in second
    # What was already rendered is reused.
    assert second["items"][0] is first["items"][0]

    # An edited message is rendered again, and what follows it.
    messages[1] = {"role": "user", "content": "weather in Porto?"}
    third = tracker.update("llm", None, ctx)
    assert third["items"][0] is first["items"][0]
    assert third["items"][1] is not second["items"][1]
    assert "Porto" in third["items"][1]["text"]


def test_images_become_small_thumbnails():
    item = context.item(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is it?"},
                {"type": "image_url", "image_url": {"url": demo.photo_url()}},
            ],
        }
    )
    assert item["text"] == "what is it?"
    media = item["media"][0]
    assert media["kind"] == "image"
    assert media["source_width"] == 160 and media["width"] <= context.THUMBNAIL_SIZE[0]
    assert len(base64.b64decode(media["rgb"])) == media["width"] * media["height"] * 3


def test_long_text_is_clipped():
    item = context.item({"role": "user", "content": "x" * (context.TEXT_LIMIT + 50)})
    assert item["text"].endswith("[+50 characters]")


def test_system_instruction_comes_from_the_llm_settings():
    class Settings:
        system_instruction = "You are a travel assistant."

    class LLM:
        _settings = Settings()

    tracker = context.ContextTracker()
    data = tracker.update("llm", LLM(), FakeContext([]))
    assert data["system"] == "You are a travel assistant."
    # It comes with every context, like the rest.
    assert tracker.update("llm", LLM(), FakeContext([]))["system"] == "You are a travel assistant."
