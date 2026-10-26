"""The approval gate: the only way a risky action (like sending an email) runs.

The model can *propose* an action; it can never execute one. Execution happens
here, in code, only after a terminal sends Decision(approve) for that card.
Tools that have side effects must go through `propose()` — never call the
side-effecting function directly from a tool handler.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from jarvis_protocol import Card

log = logging.getLogger(__name__)

Action = Callable[[], Awaitable[str]]


@dataclass
class _Pending:
    card: Card
    action: Action
    expires_at: float


class ApprovalGate:
    def __init__(self, ttl_seconds: float = 24 * 3600):
        self.ttl = ttl_seconds
        self._pending: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()

    def propose(self, title: str, body: str, action: Action, data: dict | None = None) -> Card:
        """Register an action awaiting approval and return the card to show."""
        card = Card(card_id=uuid.uuid4().hex, kind="approval", title=title,
                    body=body, data=data or {})
        self._pending[card.card_id] = _Pending(card, action, time.time() + self.ttl)
        log.info("approval requested: %s (%s)", title, card.card_id)
        return card

    def pending(self) -> list[Card]:
        self._expire()
        return [p.card for p in self._pending.values()]

    async def decide(self, card_id: str, decision: str) -> str:
        """Apply the user's decision. Returns a short result to tell the user."""
        async with self._lock:  # one decision per card, even if two screens tap at once
            self._expire()
            pending = self._pending.pop(card_id, None)
        if pending is None:
            return "That request has expired or was already handled."
        if decision != "approve":
            log.info("rejected: %s", pending.card.title)
            return f"Okay, I won't {pending.card.title.lower()}."
        log.info("approved: %s", pending.card.title)
        return await pending.action()

    def _expire(self) -> None:
        now = time.time()
        for card_id in [k for k, p in self._pending.items() if p.expires_at < now]:
            del self._pending[card_id]
