"""Task reminders, said aloud unprompted when they fall due.

Every CHECK_EVERY_S the service asks the task store (tasks.py) which reminders are due and hands
each to `deliver`, which the server points at every connected terminal. A reminder counts as said
once a terminal has taken it, and that's saved with the task, so it's never said twice, not even
after a restart. With no terminal connected it waits for one; the store decides when a late
reminder is too late to bother with. Speaking only Jarvis's own reply, it needs no approval card.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .tasks import DueReminder, TaskStore

log = logging.getLogger(__name__)

CHECK_EVERY_S = 30


class ReminderService:
    def __init__(self, tasks: TaskStore, deliver: Callable[[DueReminder], Awaitable[bool]],
                 every_s: float = CHECK_EVERY_S):
        self.tasks = tasks
        self.deliver = deliver     # returns False if nobody could take it yet
        self.every_s = every_s

    async def check(self) -> int:
        """Hand over whatever's due now; returns how many were taken."""
        taken = 0
        for due in self.tasks.due_reminders():
            if await self.deliver(due):
                self.tasks.mark_reminded(due.task_id, due.at)
                log.info("reminder for task %s said", due.task_id)
                taken += 1
        return taken

    async def run(self) -> None:
        """Check now (catching up on any that fell due while Jarvis was off), then keep checking."""
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad check mustn't stop later reminders
                log.exception("reminder check failed")
            await asyncio.sleep(self.every_s)
