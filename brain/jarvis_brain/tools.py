"""Tools Jarvis can call during a conversation.

Every tool that changes anything only *proposes* it: the handler returns a card for the screen and
the real work runs after someone taps Approve (see approvals.py). Add new tools here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from jarvis_protocol import Card

from . import clock
from .themes import THEMES, describe

log = logging.getLogger(__name__)


def _schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties or {}, "required": required or [],
            "additionalProperties": False}


TOOLS = [
    {
        "name": "request_self_upgrade",
        "description": (
            "Ask Claude Code to change Jarvis's own code: new abilities, behaviour changes, bug "
            "fixes, voice or wake-word settings in the code. Only use it when the person clearly "
            "wants Jarvis itself changed, and you understand what they want: if the request is "
            "vague, ask one short question first instead. Write the task as a clear, complete "
            "instruction for a programmer who wasn't in the conversation. This only shows an "
            "approval card; nothing runs until the person taps Approve, and they approve the "
            "finished change again before it's applied."),
        "input_schema": _schema(
            {"task": {"type": "string",
                      "description": "What to change and why, as a complete instruction."}},
            ["task"]),
        "strict": True,
    },
    {
        "name": "upgrade_status",
        "description": "How the current or last self-upgrade is going.",
        "input_schema": _schema(),
        "strict": True,
    },
    {
        "name": "undo_last_upgrade",
        "description": ("Offer to roll back the most recently applied self-upgrade. Shows an "
                        "approval card; nothing changes until it's approved."),
        "input_schema": _schema(),
        "strict": True,
    },
    {
        "name": "list_themes",
        "description": "The screen themes available on this terminal, and which one is showing.",
        "input_schema": _schema(),
        "strict": True,
    },
    {
        "name": "set_theme",
        "description": ("Switch this terminal's screen theme (each theme has its own look and "
                        "voice), when asked, e.g. 'switch to the eye theme'."),
        "input_schema": _schema(
            {"theme": {"type": "string", "enum": list(THEMES),
                       "description": "; ".join(f"{t.name} = {t.title}" for t in THEMES.values())}},
            ["theme"]),
        "strict": True,
    },
    {
        "name": "get_time",
        "description": (
            "The current time and date, here in Brisbane or anywhere in the world, from the system "
            "clock and timezone database (daylight saving included). Always use this instead of "
            "guessing the time or date. Leave place empty for here. If the place is ambiguous, the "
            "result says so: ask which one they meant."),
        "input_schema": _schema(
            {"place": {"type": "string",
                       "description": ("A city, country, region or IANA timezone, e.g. 'London', "
                                       "'Portland, Maine', 'Japan', 'US Eastern'. Empty for "
                                       "Brisbane.")}}),
        "strict": True,
    },
    {
        "name": "convert_time",
        "description": (
            "What time it is in one place when it's a given time in another, e.g. 'what time will "
            "it be in Tokyo when it's 3pm here'. Places default to Brisbane. If a place is "
            "ambiguous, the result says so: ask which one they meant."),
        "input_schema": _schema(
            {"time": {"type": "string", "description": "The time in from_place, as HH:MM, 24-hour."},
             "from_place": {"type": "string",
                            "description": "Where it's that time. Empty for Brisbane."},
             "to_place": {"type": "string",
                          "description": "Where to give the time for. Empty for Brisbane."},
             "date": {"type": "string",
                      "description": ("The date in from_place, YYYY-MM-DD, if they named a day. "
                                      "Empty for the next time it's that time.")}},
            ["time"]),
        "strict": True,
    },
]


class ToolBox:
    """Runs tool calls for one terminal's conversation; shows any cards on that terminal."""

    def __init__(self, upgrades, show_card: Callable[[Card], Awaitable[None]],
                 current_theme: Callable[[], str] | None = None,
                 set_theme: Callable[[str], Awaitable[str]] | None = None):
        self.upgrades = upgrades
        self.show_card = show_card
        self.current_theme = current_theme
        self.set_theme = set_theme

    async def run(self, name: str, args: dict) -> str:
        log.info("tool %s %s", name, args)
        card = None
        if name == "request_self_upgrade":
            text, card = self.upgrades.request(str(args.get("task", "")))
        elif name == "upgrade_status":
            text = self.upgrades.status()
        elif name == "undo_last_upgrade":
            text, card = self.upgrades.request_undo()
        elif name == "list_themes":
            now = self.current_theme() if self.current_theme else "unknown"
            text = f"Showing now: {now}. Available: {describe()}"
        elif name == "set_theme":
            if not self.set_theme:
                raise RuntimeError("themes aren't available here")
            text = await self.set_theme(str(args.get("theme", "")))
        elif name == "get_time":
            text = clock.current_time(args.get("place") or None)
        elif name == "convert_time":
            text = clock.convert_time(
                str(args.get("time", "")), args.get("from_place") or None,
                args.get("to_place") or None, args.get("date") or None)
        else:
            raise ValueError(f"unknown tool {name}")
        if card:
            await self.show_card(card)
        return text
