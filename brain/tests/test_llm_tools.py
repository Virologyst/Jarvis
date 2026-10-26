"""The conversation's tool loop, against a fake Claude client (no network)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.llm import Conversation  # noqa: E402


class FakeStream:
    def __init__(self, text: str, final):
        self.text, self.final = text, final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    async def text_stream(self):
        for word in self.text.split(" "):
            yield word + " "

    async def get_final_message(self):
        return self.final


class FakeClient:
    """Plays back a list of (text, final message) responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        text, final = self.responses.pop(0)
        return FakeStream(text, final)


def tool_use(id_, name, args):
    return NS(type="tool_use", id=id_, name=name, input=args)


def test_tool_call_runs_and_result_goes_back() -> None:
    calls = []

    async def run_tool(name, args):
        calls.append((name, args))
        return "An approval card is on the screen."

    first = NS(stop_reason="tool_use", content=[tool_use("t1", "request_self_upgrade", {"task": "add a timer"})])
    second = NS(stop_reason="end_turn", content=[NS(type="text", text="Card's up, Sir.")])
    client = FakeClient([("", first), ("Card's up, Sir.", second)])
    conv = Conversation(client, "m", "low", 10, tools=[{"name": "request_self_upgrade"}], run_tool=run_tool)

    async def go():
        return "".join([t async for t in conv.reply("Give yourself a timer")])

    spoken = asyncio.run(go())
    assert calls == [("request_self_upgrade", {"task": "add a timer"})]
    assert "Card's up" in spoken
    # user, assistant(tool_use), user(tool_result), assistant(text)
    roles = [m["role"] for m in conv.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    result = conv.messages[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "t1"
    assert "tools" in client.requests[0]


def test_tool_error_is_reported_not_raised() -> None:
    async def run_tool(name, args):
        raise RuntimeError("boom")

    first = NS(stop_reason="tool_use", content=[tool_use("t1", "upgrade_status", {})])
    second = NS(stop_reason="end_turn", content=[NS(type="text", text="That failed.")])
    conv = Conversation(FakeClient([("", first), ("That failed.", second)]), "m", "low", 10,
                        tools=[{"name": "upgrade_status"}], run_tool=run_tool)

    async def go():
        return "".join([t async for t in conv.reply("How's the upgrade?")])

    asyncio.run(go())
    result = conv.messages[2]["content"][0]
    assert result["is_error"] is True and "boom" in result["content"]


def test_cancelled_turn_leaves_history_untouched() -> None:
    done = NS(stop_reason="end_turn", content=[NS(type="text", text="Hello, Sir.")])
    client = FakeClient([("Hello, Sir.", done), ("never", done)])
    conv = Conversation(client, "m", "low", 10)

    async def go():
        _ = [t async for t in conv.reply("Hey Jarvis")]
        before = list(conv.messages)
        gen = conv.reply("Tell me a long story")
        await gen.__anext__()          # Jarvis starts talking...
        await gen.aclose()             # ...and is interrupted
        return before

    before = asyncio.run(go())
    assert conv.messages == before
