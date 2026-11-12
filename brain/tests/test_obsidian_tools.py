"""Jarvis's Obsidian tools go to the terminal that reaches Obsidian."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.approvals import ApprovalGate  # noqa: E402
from jarvis_brain.files import FileAccessError  # noqa: E402
from jarvis_brain.persona import SYSTEM_PROMPT  # noqa: E402
from jarvis_brain.selfupgrade import tool_schema_problems  # noqa: E402
from jarvis_brain.tools import REQUEST_TOOLS, ToolBox  # noqa: E402


async def no_card(card):  # pragma: no cover
    raise AssertionError


def terminal(name, calls, fail=None):
    async def remote(op, args):
        calls.append((name, op, args))
        if fail:
            raise FileAccessError(fail)
        return f"{name} did {op}"
    return remote


def test_goes_to_this_terminal_or_the_only_one():
    calls = []
    both = {"craig-pc": terminal("craig-pc", calls), "laptop": terminal("laptop", calls)}
    here = ToolBox(None, no_card, terminal_id=lambda: "craig-pc", obsidian_terminals=lambda: both)
    assert asyncio.run(here.run("obsidian_write", {"action": "append", "heading": "Ideas",
                                                   "text": "- x", "note": ""})) == \
        "craig-pc did obsidian_edit"
    assert calls[-1][2] == {"action": "append", "heading": "Ideas", "text": "- x"}
    pi = ToolBox(None, no_card, terminal_id=lambda: "pi", obsidian_terminals=lambda: both)
    assert "which one?" in asyncio.run(pi.run("obsidian_open_note", {}))
    one = ToolBox(None, no_card, terminal_id=lambda: "pi",
                  obsidian_terminals=lambda: {"craig-pc": both["craig-pc"]})
    assert asyncio.run(one.run("obsidian_show", {"note": "Home"})) == "craig-pc did obsidian_open"


def test_canvas_arguments_tidied_and_errors_said():
    calls = []
    tools = ToolBox(None, no_card, terminal_id=lambda: "craig-pc",
                    obsidian_terminals=lambda: {"craig-pc": terminal("craig-pc", calls)})
    asyncio.run(tools.run("obsidian_canvas", {"canvas": "Board", "cards": ["A", {"text": "B"}],
                                              "links": ["junk", {"from": "A", "to": "B"}]}))
    assert calls[-1][2] == {"canvas": "Board", "cards": [{"text": "A"}, {"text": "B"}],
                            "links": [{"from": "A", "to": "B"}]}
    failing = ToolBox(None, no_card, terminal_id=lambda: "craig-pc", obsidian_terminals=lambda: {
        "craig-pc": terminal("craig-pc", [], fail="Obsidian isn't answering on this computer.")})
    assert asyncio.run(failing.run("obsidian_open_note", {})) == \
        "Obsidian isn't answering on this computer."
    assert "isn't reachable right now" in asyncio.run(ToolBox(None, no_card).run(
        "obsidian_open_note", {}))


def test_canvas_removals_wait_for_approval():
    calls, shown = [], []

    async def show(card):
        shown.append(card)

    gate = ApprovalGate()
    tools = ToolBox(None, show, approvals=gate, terminal_id=lambda: "craig-pc",
                    obsidian_terminals=lambda: {"craig-pc": terminal("craig-pc", calls)})
    said = asyncio.run(tools.run("obsidian_canvas", {
        "canvas": "Board", "cards": [], "links": [], "edit_links": "junk",
        "remove_cards": ["Old idea", {"card": "c1"}, ""], "remove_links": ["junk"]}))
    assert "approval card is up" in said and "craig-pc did obsidian_canvas" in said
    assert calls == [("craig-pc", "obsidian_canvas", {
        "canvas": "Board", "cards": [], "links": [], "remove_cards": ["Old idea", "c1"],
        "preview": True})]
    assert len(shown) == 1 and shown[0].body == "craig-pc did obsidian_canvas"
    assert asyncio.run(gate.decide(shown[0].card_id, "approve")) == "craig-pc did obsidian_canvas"
    assert calls[-1][2] == {"canvas": "Board", "cards": [], "links": [],
                            "remove_cards": ["Old idea", "c1"]}
    # Edits that only change cards go straight through, like adding
    asyncio.run(tools.run("obsidian_canvas", {"canvas": "Board", "edit_cards": [
        {"card": "c1", "color": "red"}]}))
    assert calls[-1][2] == {"canvas": "Board", "cards": [], "links": [],
                            "edit_cards": [{"card": "c1", "color": "red"}]}
    assert len(shown) == 1
    # A card the terminal can't find is said straight away, with no approval card
    failing = ToolBox(None, show, approvals=gate, terminal_id=lambda: "craig-pc",
                      obsidian_terminals=lambda: {"craig-pc": terminal(
                          "craig-pc", [], fail='No card matches "x".')})
    assert asyncio.run(failing.run("obsidian_canvas", {"canvas": "Board",
                                                       "remove_cards": ["x"]})) == \
        'No card matches "x".'
    assert len(shown) == 1
    assert "approval screen" in asyncio.run(ToolBox(
        None, show, terminal_id=lambda: "craig-pc",
        obsidian_terminals=lambda: {"craig-pc": terminal("craig-pc", [])}).run(
        "obsidian_canvas", {"canvas": "Board", "remove_links": [{"from": "a", "to": "b"}]}))


def test_tools_load_on_demand_and_the_request_is_valid():
    obsidian = [t for t in REQUEST_TOOLS if t.get("name", "").startswith("obsidian_")]
    assert {t["name"] for t in obsidian} == {"obsidian_open_note", "obsidian_read",
                                             "obsidian_write", "obsidian_show", "obsidian_project",
                                             "obsidian_search", "obsidian_canvas"}
    assert all(t["defer_loading"] and not t["strict"] for t in obsidian)
    assert all(t["name"] in SYSTEM_PROMPT for t in obsidian if t["name"] != "obsidian_read")
    assert tool_schema_problems(REQUEST_TOOLS) == []
