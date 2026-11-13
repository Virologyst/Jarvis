"""Scheduled spoken briefings: Jarvis says them unprompted, like reminders.

Two of them, each with its own days and Brisbane time, saved in data/briefings.json:

- "weekday": Sir's daily priorities (the same sources and ranking as daily_priorities), Monday to
  Friday at 9 am to start with.
- "personal": his personal list from his Me note (routines due, personal projects and their open
  items, personal to-dos; no V Source work), Saturdays at 9 am.

Every CHECK_EVERY_S the service asks the schedule what's due, gathers it (the server passes a
function for that) and hands the words to `deliver`, which queues it on every terminal like a
reminder: said once each is quiet, then the mic stays open for an answer. A briefing that fell
due while the brain was off is still said up to CATCH_UP late; after that it waits for its next
day. Changing the schedule (briefing_schedule, by voice) only changes Jarvis's own settings
file, like the theme or the task lists, so it needs no approval card.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .clock import HOME_ZONE, number_words, parse_time, say_date, say_time
from .priorities import Briefing

log = logging.getLogger(__name__)

CHECK_EVERY_S = 30
CATCH_UP = timedelta(hours=2)
DEFAULT_SNOOZE_MIN = 15
WEEKDAY, PERSONAL = "weekday", "personal"
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LABELS = {WEEKDAY: "the weekday priorities briefing", PERSONAL: "the personal briefing"}
_DEFAULTS = {WEEKDAY: ("09:00", [0, 1, 2, 3, 4]), PERSONAL: ("09:00", [5])}
# Words that pick a briefing, e.g. "the Saturday one", "my work briefing"
_PICK = {WEEKDAY: re.compile(r"week ?day|work|priorit|office|v ?source|daily", re.I),
         PERSONAL: re.compile(r"personal|saturday|weekend|home|\bme\b|private", re.I)}


class BriefingError(ValueError):
    """Something to say back instead of changing the schedule."""


@dataclass
class Schedule:
    time: str = "09:00"                 # HH:MM, Brisbane
    days: list[int] = field(default_factory=list)   # 0 = Monday
    enabled: bool = True
    paused_until: str = ""              # YYYY-MM-DD: quiet before that day
    last_day: str = ""                  # the last day it was said (or let go for that day)
    snoozed_until: str = ""             # ISO time to say it again


@dataclass
class SpokenBriefing:
    """What a terminal says, and a note telling Jarvis what he just said (like DueReminder)."""
    kind: str
    text: str
    note: str


def parse_days(text: str) -> list[int]:
    """'weekdays', 'Monday to Friday', 'saturday', 'weekends', 'every day', 'mon, wed and fri'."""
    words = " ".join(re.sub(r"[,&/]", " ", (text or "").lower()).split())
    if not words:
        raise BriefingError("Which days?")
    if re.search(r"\b(every ?day|daily|all week|seven days)\b", words):
        return list(range(7))
    if re.search(r"\bweek ?days\b|\bworking days\b|\bwork days\b", words):
        return [0, 1, 2, 3, 4]
    if re.search(r"\bweekends?\b", words):
        return [5, 6]
    found = [(m.start(), i) for i, name in enumerate(DAY_NAMES)
             for m in re.finditer(rf"\b{name[:3].lower()}[a-z]*\b", words)]
    found.sort()
    if not found:
        raise BriefingError(f"I couldn't tell which days from '{text}'.")
    days = {i for _, i in found}
    # "monday to friday", "tuesday through thursday"
    m = re.search(r"\b([a-z]+)\s+(?:to|through|thru|until|-)\s+([a-z]+)", words)
    if m:
        ends = [next((i for i, n in enumerate(DAY_NAMES) if w.startswith(n[:3].lower())), None)
                for w in m.groups()]
        if None not in ends:
            a, b = ends
            days |= {(a + k) % 7 for k in range((b - a) % 7 + 1)}
    return sorted(days)


def say_days(days: list[int]) -> str:
    s = sorted(set(days))
    if s == list(range(7)):
        return "every day"
    if s == [0, 1, 2, 3, 4]:
        return "weekdays, Monday to Friday"
    if s == [5, 6]:
        return "weekends"
    names = [DAY_NAMES[d] + "s" for d in s]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class BriefingSchedule:
    """When each briefing is said, kept in a JSON file written in one step after every change."""

    def __init__(self, path: Path, now: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self._clock = now or (lambda: datetime.now(ZoneInfo(HOME_ZONE)))
        self.briefings: dict[str, Schedule] = {
            k: Schedule(t, list(d)) for k, (t, d) in _DEFAULTS.items()}
        self.last_said = ""   # which was said most recently, for "snooze it"
        self._load()

    # Reading and writing

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(Schedule)}
            for kind, raw in dict(data.get("briefings", {})).items():
                if kind in self.briefings:
                    s = Schedule(**{k: v for k, v in dict(raw).items() if k in known})
                    parse_time(s.time)
                    s.days = sorted({int(d) % 7 for d in s.days})
                    self.briefings[kind] = s
            self.last_said = str(data.get("last_said") or "")
        except (OSError, ValueError, TypeError, AttributeError) as e:
            spare = self.path.with_name(f"{self.path.name}.unreadable-{self.now():%Y%m%d%H%M%S}")
            log.error("couldn't read %s (%s); kept it as %s and used the defaults",
                      self.path, e, spare)
            try:
                self.path.replace(spare)
            except OSError:
                pass
            self.briefings = {k: Schedule(t, list(d)) for k, (t, d) in _DEFAULTS.items()}

    def _save(self) -> None:
        data = {"version": 1, "last_said": self.last_said,
                "briefings": {k: asdict(s) for k, s in self.briefings.items()}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def now(self) -> datetime:
        return self._clock().astimezone(ZoneInfo(HOME_ZONE))

    # When

    def _at(self, s: Schedule, day: date) -> datetime:
        return datetime.combine(day, parse_time(s.time), tzinfo=ZoneInfo(HOME_ZONE))

    def _scheduled_today(self, s: Schedule, today: date) -> bool:
        return (s.enabled and today.weekday() in s.days
                and not (s.paused_until and today.isoformat() < s.paused_until))

    def due(self) -> list[str]:
        """The briefings to say now: on schedule (up to CATCH_UP late), or a snooze that's up."""
        now, out = self.now(), []
        today = now.date()
        for kind, s in self.briefings.items():
            if s.snoozed_until:
                at = datetime.fromisoformat(s.snoozed_until)
                if at <= now <= at + CATCH_UP:
                    out.append(kind)
                    continue
                if now > at + CATCH_UP:
                    s.snoozed_until = ""
                    self._save()
            if not self._scheduled_today(s, today) or s.last_day == today.isoformat():
                continue
            at = self._at(s, today)
            if at <= now <= at + CATCH_UP:
                out.append(kind)
        return out

    def mark_said(self, kind: str) -> None:
        s = self.briefings[kind]
        s.last_day = self.now().date().isoformat()
        s.snoozed_until = ""
        self.last_said = kind
        self._save()

    def next_time(self, kind: str) -> datetime | None:
        """When it's next said (a snooze counts), or None if it's off."""
        s, now = self.briefings[kind], self.now()
        if s.snoozed_until:
            return datetime.fromisoformat(s.snoozed_until)
        if not s.enabled or not s.days:
            return None
        for k in range(15):
            day = now.date() + timedelta(days=k)
            if not self._scheduled_today(s, day):
                continue
            if day == now.date() and (s.last_day == day.isoformat() or self._at(s, day) < now):
                continue
            return self._at(s, day)
        return None

    # Changing it (by voice, through the briefing_schedule tool)

    def pick(self, ref: str, action: str = "") -> str:
        ref = (ref or "").strip()
        hits = [k for k, rx in _PICK.items() if rx.search(ref)]
        if ref in self.briefings:
            return ref
        if len(hits) == 1:
            return hits[0]
        if not ref and action == "snooze" and self.last_said:
            return self.last_said
        raise BriefingError("Which briefing: the weekday priorities one, or the Saturday "
                            "personal one?")

    def describe(self, kind: str | None = None) -> str:
        kinds = [kind] if kind else list(self.briefings)
        lines = []
        for k in kinds:
            s = self.briefings[k]
            if not s.enabled:
                state = "off"
            else:
                state = f"on, {say_days(s.days)} at {say_time(parse_time(s.time))}"
                if s.paused_until and self.now().date().isoformat() < s.paused_until:
                    state += f", paused until {say_date(date.fromisoformat(s.paused_until))}"
            nxt = self.next_time(k)
            when = (f" Next: {say_date(nxt)} at {say_time(nxt)}." if nxt else "")
            lines.append(f"{LABELS[k][0].upper()}{LABELS[k][1:]}: {state}.{when}")
        return " ".join(lines)

    def set_time(self, kind: str, text: str) -> str:
        try:
            at = parse_time(text)
        except ValueError as e:
            raise BriefingError(str(e)) from None
        s = self.briefings[kind]
        s.time = f"{at:%H:%M}"
        now = self.now()
        # A new time that's already gone today waits for the next day, rather than firing now
        if self._scheduled_today(s, now.date()) and self._at(s, now.date()) <= now:
            s.last_day = now.date().isoformat()
        self._save()
        return self.describe(kind)

    def set_days(self, kind: str, text: str) -> str:
        self.briefings[kind].days = parse_days(text)
        self._save()
        return self.describe(kind)

    def turn(self, kind: str, on: bool) -> str:
        s = self.briefings[kind]
        s.enabled, s.paused_until = on, ""
        if not on:
            s.snoozed_until = ""
        self._save()
        return self.describe(kind)

    def pause(self, kind: str, until: date) -> str:
        if until <= self.now().date():
            raise BriefingError("Pause it until when? Give a day after today.")
        s = self.briefings[kind]
        s.enabled, s.paused_until, s.snoozed_until = True, until.isoformat(), ""
        self._save()
        return self.describe(kind)

    def snooze(self, kind: str, minutes: int) -> str:
        if not 1 <= minutes <= 12 * 60:
            raise BriefingError("Snooze it for how many minutes?")
        at = self.now().replace(second=0, microsecond=0) + timedelta(minutes=minutes)
        self.briefings[kind].snoozed_until = at.isoformat()
        self._save()
        return f"Snoozed {LABELS[kind]} until {say_time(at)}."


def speech(b: Briefing, kind: str, top: int = 4) -> str:
    """The briefing as Jarvis says it unprompted: how many things, then the top few in order."""
    items = b.ranked()
    n = len(items)
    personal = kind == PERSONAL
    hello = ("[happy] Good morning, Sir. Your personal list for today."
             if personal else "[happy] Good morning, Sir. Your priorities for today.")
    parts = [hello]
    if not items:
        parts.append("I've nothing on your personal list today. Enjoy the weekend, Sir."
                     if personal else "I've nothing pressing for you today.")
    else:
        many = number_words(n) if n < 60 else str(n)
        parts.append(f"[statement] There {'is' if n == 1 else 'are'} {many} "
                     f"{'thing' if n == 1 else 'things'}.")
        leads = ["First", "Then", "After that", "And"]
        for i, it in enumerate(items[:top]):
            text = it.text[0].lower() + it.text[1:] if it.text else it.text
            parts.append(f"{leads[min(i, len(leads) - 1)]}, {text}")
        if n > top:
            parts.append(f"There {'is' if n - top == 1 else 'are'} {n - top} more; "
                         "just ask and I'll go through them.")
    if b.missing:
        names = [m.split(" (", 1)[0] for m in b.missing]
        parts.append(f"[concerned] I couldn't reach {' or '.join(names)}, so that's without it.")
    return " ".join(parts)


def note_for(b: Briefing, kind: str) -> str:
    """For the conversation: what was said, and the whole list for 'what else?'."""
    return (f"[At its scheduled time you said {LABELS[kind]} unprompted on the screens; the mic "
            "is open for Sir's answer. If he says snooze it, use briefing_schedule with action "
            "snooze; if he's done a routine, routine_done; a task, update_task. The full list:]\n"
            + b.text())


class BriefingService:
    def __init__(self, schedule: BriefingSchedule,
                 gather: Callable[[str], Awaitable[Briefing]],
                 deliver: Callable[[SpokenBriefing], Awaitable[bool]],
                 every_s: float = CHECK_EVERY_S, ready: Callable[[], bool] = lambda: True):
        self.schedule = schedule
        self.ready = ready         # a terminal is connected to hear it (else don't gather yet)
        self.gather = gather       # kind -> the briefing, from the same sources as the tools
        self.deliver = deliver     # False if nobody could take it yet
        self.every_s = every_s

    async def check(self) -> int:
        said = 0
        if not self.ready():
            return 0
        for kind in self.schedule.due():
            b = await self.gather(kind)
            if await self.deliver(SpokenBriefing(kind, speech(b, kind), note_for(b, kind))):
                self.schedule.mark_said(kind)
                log.info("%s said", LABELS[kind])
                said += 1
        return said

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad check mustn't stop later briefings
                log.exception("briefing check failed")
            await asyncio.sleep(self.every_s)
