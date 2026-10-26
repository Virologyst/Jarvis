"""Tools Jarvis can call during a conversation.

Every tool that changes anything only *proposes* it: the handler returns a card for the screen and
the real work runs after someone taps Approve (see approvals.py). Add new tools here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from jarvis_protocol import Card

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
]


class ToolBox:
    """Runs tool calls for one terminal's conversation; shows any cards on that terminal."""

    def __init__(self, upgrades, show_card: Callable[[Card], Awaitable[None]]):
        self.upgrades = upgrades
        self.show_card = show_card

    async def run(self, name: str, args: dict) -> str:
        log.info("tool %s %s", name, args)
        if name == "request_self_upgrade":
            text, card = self.upgrades.request(str(args.get("task", "")))
        elif name == "upgrade_status":
            text, card = self.upgrades.status(), None
        elif name == "undo_last_upgrade":
            text, card = self.upgrades.request_undo()
        else:
            raise ValueError(f"unknown tool {name}")
        if card:
            await self.show_card(card)
        return text
