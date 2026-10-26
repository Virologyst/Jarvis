"""The approval gate: the only way a risky action (like sending an email) runs.

The model can *propose* an action; it can never execute one. Execution happens
here, in code, only after a terminal sends Decision(approve) for that card.
Tools that have side effects must go through `propose()` — never call the
side-effecting function directly from a tool handler.

Cards survive a brain restart when they're proposed with a `kind` and the gate has a
`store` file: the card is saved there, and on start-up `restore()` asks the code that owns
that kind (see `register()`) to rebuild its action from the card. Only that owner decides
whether the card still makes sense; a card nobody can rebuild is dropped, never run blind.
The saved file holds cards only, never code, so editing it can't make anything run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from jarvis_protocol import Card

log = logging.getLogger(__name__)

Action = Callable[[], Awaitable[str]]
# Given a saved card, return its action again, or None if it no longer applies.
Rebuild = Callable[[Card], "Action | None"]


@dataclass
class _Pending:
    card: Card
    action: Action
    expires_at: float
    kind: str | None = None


class ApprovalGate:
    def __init__(self, ttl_seconds: float = 24 * 3600, store: Path | None = None):
        self.ttl = ttl_seconds
        self.store = store
        self._pending: dict[str, _Pending] = {}
        self._rebuilders: dict[str, Rebuild] = {}
        self._lock = asyncio.Lock()

    def register(self, kind: str, rebuild: Rebuild) -> None:
        """Say how to rebuild the action for saved cards of this kind (call before restore())."""
        self._rebuilders[kind] = rebuild

    def propose(self, title: str, body: str, action: Action, data: dict | None = None,
                kind: str | None = None) -> Card:
        """Register an action awaiting approval and return the card to show.

        kind: lets the card survive a restart (needs a store and a registered rebuilder).
        """
        card = Card(card_id=uuid.uuid4().hex, kind="approval", title=title,
                    body=body, data=data or {})
        self._pending[card.card_id] = _Pending(card, action, time.time() + self.ttl, kind)
        log.info("approval requested: %s (%s)", title, card.card_id)
        self._save()
        return card

    def pending(self) -> list[Card]:
        self._expire()
        return [p.card for p in self._pending.values()]

    async def decide(self, card_id: str, decision: str) -> str:
        """Apply the user's decision. Returns a short result to tell the user."""
        async with self._lock:  # one decision per card, even if two screens tap at once
            self._expire()
            pending = self._pending.pop(card_id, None)
            self._save()        # gone from disk before it runs: a crash can't replay it
        if pending is None:
            return "That request has expired or was already handled."
        if decision != "approve":
            log.info("rejected: %s", pending.card.title)
            return f"Okay, I won't {pending.card.title.lower()}."
        log.info("approved: %s", pending.card.title)
        return await pending.action()

    def withdraw(self, card_id: str) -> None:
        """Take a card down without deciding it (its owner says it no longer applies)."""
        if self._pending.pop(card_id, None):
            self._save()

    # ---- surviving a restart ---------------------------------------------

    def restore(self) -> int:
        """Bring back saved cards whose owner can still rebuild them. Returns how many."""
        if not self.store or not self.store.exists():
            return 0
        try:
            saved = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("couldn't read saved approvals (%s); starting with none", e)
            return 0
        now, restored = time.time(), 0
        for entry in saved if isinstance(saved, list) else []:
            try:
                card = Card(**entry["card"])
                kind, expires_at = entry["kind"], float(entry["expires_at"])
            except (KeyError, TypeError, ValueError):
                continue
            rebuild = self._rebuilders.get(kind)
            if expires_at < now or rebuild is None or card.card_id in self._pending:
                continue
            action = rebuild(card)
            if action is None:
                log.info("saved approval no longer applies: %s", card.title)
                continue
            self._pending[card.card_id] = _Pending(card, action, expires_at, kind)
            restored += 1
        self._save()    # drop whatever wasn't restored
        if restored:
            log.info("restored %d approval card(s) from before the restart", restored)
        return restored

    def _save(self) -> None:
        if not self.store:
            return
        entries = [{"card": asdict(p.card), "kind": p.kind, "expires_at": p.expires_at}
                   for p in self._pending.values() if p.kind]
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store.with_suffix(".tmp")
            tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
            os.replace(tmp, self.store)
        except OSError as e:
            log.warning("couldn't save approvals: %s", e)

    def _expire(self) -> None:
        now = time.time()
        expired = [k for k, p in self._pending.items() if p.expires_at < now]
        for card_id in expired:
            del self._pending[card_id]
        if expired:
            self._save()
