"""Coding jobs from the brain's side: tools reach the coder's terminal, and its requests become
approval cards whose answers go back to it."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_protocol import CoderDecision, CoderEvent  # noqa: E402
from jarvis_brain.approvals import ApprovalGate  # noqa: E402
from jarvis_brain.persona import SYSTEM_PROMPT  # noqa: E402
from jarvis_brain.selfupgrade import tool_schema_problems  # noqa: E402
from jarvis_brain.server import Brain  # noqa: E402
from jarvis_brain.tools import REQUEST_TOOLS, ToolBox  # noqa: E402


async def no_card(card):  # pragma: no cover
    raise AssertionError


def test_tools_go_to_the_coder_terminal():
    calls = []

    async def remote(op, args):
        calls.append((op, args))
        return "Started."

    tools = ToolBox(None, no_card, terminal_id=lambda: "pi",
                    coder_terminals=lambda: {"craig-pc": remote})
    assert asyncio.run(tools.run("code_start", {"project": "Timer", "task": "a timer",
                                                "new": True, "where": "PycharmProjects"})) == "Started."
    assert calls[-1] == ("coder_start", {"project": "Timer", "task": "a timer", "new": "true",
                                         "where": "PycharmProjects"})
    asyncio.run(tools.run("code_status", {}))
    assert calls[-1] == ("coder_status", {})
    assert "isn't reachable" in asyncio.run(ToolBox(None, no_card).run("code_status", {}))


def test_tools_are_loose_deferred_described_and_valid():
    code = {t["name"] for t in REQUEST_TOOLS if t.get("name", "").startswith("code_")}
    assert code == {"code_projects", "code_start", "code_reply", "code_status", "code_stop"}
    assert all(t["defer_loading"] and not t["strict"]
               for t in REQUEST_TOOLS if t.get("name") in code)
    assert all(name in SYSTEM_PROMPT for name in code)
    assert tool_schema_problems(REQUEST_TOOLS) == []


class FakeSession:
    def __init__(self, terminal_id):
        self.terminal_id, self.sent = terminal_id, []
        self.ws = SimpleNamespace(closed=False)

    async def send(self, msg):
        self.sent.append(msg)


def fake_brain():
    said, delivered = [], []
    pc = FakeSession("craig-pc")
    brain = SimpleNamespace(approvals=ApprovalGate(), sessions={pc}, _coder_cards={})

    async def announce(text, card=None):
        said.append((text, card))

    async def deliver(a):
        delivered.append(a)
        return True

    async def broadcast(msg):
        said.append(("again", msg))

    brain.announce, brain.deliver_reminder, brain.broadcast = announce, deliver, broadcast
    return brain, pc, said, delivered


def test_permission_becomes_a_card_and_the_answer_goes_back():
    async def go():
        brain, pc, said, _ = fake_brain()
        ask = CoderEvent("j1", "permission", "run: npm install express", request_id="r1",
                         data={"project": "portal", "detail": "npm install express"})
        await Brain.on_coder_event(brain, "craig-pc", ask)
        text, card = said[0]
        assert text == "Sir, the coder in portal needs your approval: run: npm install express."
        assert card.title == "Coder: run: npm install express"
        assert "working in portal" in card.body and "npm install express" in card.body
        await Brain.on_coder_event(brain, "craig-pc", ask)       # asked again after a reconnect
        assert said[1] == ("again", card) and len(brain.approvals.pending()) == 1
        assert await brain.approvals.decide(card.card_id, "approve") == \
            "Approved. The coder carries on."
        assert pc.sent == [CoderDecision("r1", True)]
        await Brain.on_coder_event(brain, "craig-pc", CoderEvent(
            "j1", "permission", "run: git push", request_id="r2", data={"project": "portal"}))
        card2 = said[-1][1]
        assert await brain.approvals.decide(card2.card_id, "reject") == \
            "Refused. The coder will carry on without it."
        assert pc.sent[-1] == CoderDecision("r2", False)
        assert brain._coder_cards == {}
    asyncio.run(go())


def test_finished_job_is_said_when_quiet():
    async def go():
        brain, _, _, delivered = fake_brain()
        await Brain.on_coder_event(brain, "craig-pc", CoderEvent(
            "j1", "done", "Added the button.\nTests pass.", data={"project": "Jarvis",
                                                                 "task": "add a button"}))
        a = delivered[0]
        assert a.text == "Sir, the coding job in Jarvis is finished. Added the button. Tests pass."
        assert "The task: add a button" in a.note and "code_reply" in a.note
        await Brain.on_coder_event(brain, "craig-pc", CoderEvent("j2", "failed", "Boom.",
                                                                 data={"project": "API"}))
        assert delivered[1].text == "Sir, the coding job in API ran into trouble. Boom."
    asyncio.run(go())


def test_gate_runs_on_reject_and_short_lived_cards():
    async def go():
        gate, hits = ApprovalGate(), []

        async def yes():
            return "yes"

        async def no():
            hits.append("no")
            return "said no"

        card = gate.propose("Do it", "", yes, on_reject=no, ttl_seconds=0.01)
        await asyncio.sleep(0.05)
        assert gate.pending() == []                      # expired
        card = gate.propose("Do it", "", yes, on_reject=no)
        assert await gate.decide(card.card_id, "reject") == "said no" and hits == ["no"]
        plain = gate.propose("Send it", "", yes)
        assert await gate.decide(plain.card_id, "reject") == "Okay, I won't send it."
    asyncio.run(go())
