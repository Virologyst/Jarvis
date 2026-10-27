"""Everyone's to-do lists: tasks with due dates and times, and notes Jarvis keeps on each one.

Each person in the office has their own named list, saved in data/tasks.json so it survives
restarts. Jarvis can't tell voices apart yet, so a task with no person named goes on the speaker's
list, and the speaker is Craig (the owner) unless told otherwise: every method takes a `speaker`
name, which ToolBox gets from a callable that a voice-identification step can fill in later.

Dates and times are Brisbane time, from the system clock and the timezone database (clock.py).
This is Jarvis's own notebook: nothing leaves the brain PC, and deleting a task only moves it to a
bin it can be restored from. Results are worded to be said aloud; bracketed parts are for Jarvis.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .clock import HOME_ZONE, number_words, parse_time, say_date, say_time

log = logging.getLogger(__name__)

OWNER = "Craig"
STATUSES = ("open", "on_hold", "done")
_STATUS_WORDS = {"open": "to do", "on_hold": "on hold", "done": "done", "deleted": "deleted"}
_SELF = {"", "me", "my", "mine", "i", "myself", "self", "default"}
_CLEAR = {"none", "clear", "no", "remove", "nothing", "no date", "no time", "null"}
_STOPWORDS = {"the", "a", "an", "to", "for", "my", "with", "about", "on", "at", "of", "and", "in",
              "task", "thing", "that", "this", "is", "i", "have", "me", "what", "whats", "one",
              "o", "clock", "oclock", "am", "pm", "today", "tomorrow", "list"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]


class TaskError(ValueError):
    """Something Jarvis should tell the person or ask about; the message says what."""


@dataclass
class Task:
    id: int
    person: str                    # key into TaskStore.people
    title: str
    status: str = "open"           # open, on_hold, done or deleted
    due_date: str | None = None    # YYYY-MM-DD, Brisbane
    due_time: str | None = None    # HH:MM, 24-hour, Brisbane
    notes: str = ""                # context Jarvis keeps: agenda, people, background, decisions
    created: str = ""
    updated: str = ""
    completed: str | None = None

    @property
    def active(self) -> bool:
        return self.status in ("open", "on_hold")

    def sort_key(self) -> tuple:
        return (self.due_date or "9999-99-99", self.due_time or "99:99", self.id)


# --- Reading dates, times and names -----------------------------------------------------------

def _count(n: int) -> str:
    return number_words(n) if 0 <= n < 60 else str(n)


def _plural(n: int, word: str) -> str:
    return f"{_count(n)} {word}{'' if n == 1 else 's'}"


def parse_day(text: str | None, today: date) -> date | None:
    """'today', 'tomorrow', 'friday', 'next friday', 'in 3 days', '2026-10-30', '30 October',
    'October 30th', '30/10' (day first, Australian) -> a date. Empty -> None."""
    words = " ".join((text or "").lower().replace(",", " ").split())
    words = re.sub(r"^(on|by|due|for|this coming|the)\s+", "", words)
    if not words:
        return None
    if words in ("today", "tonight", "this morning", "this afternoon", "this evening", "now"):
        return today
    if words == "tomorrow":
        return today + timedelta(days=1)
    if words in ("day after tomorrow", "the day after tomorrow"):
        return today + timedelta(days=2)
    if words == "yesterday":
        return today - timedelta(days=1)
    if words in ("next week", "in a week", "a week from today"):
        return today + timedelta(days=7)
    m = re.fullmatch(r"in (\d+) (day|days|week|weeks)", words)
    if m:
        n = int(m.group(1))
        return today + timedelta(days=n * (7 if m.group(2).startswith("week") else 1))
    m = re.fullmatch(r"(this |next )?(\w+)", words)
    if m and m.group(2) in _WEEKDAYS:
        ahead = (_WEEKDAYS.index(m.group(2)) - today.weekday()) % 7
        if m.group(1) == "next " and ahead == 0:
            ahead = 7
        return today + timedelta(days=ahead)
    try:
        return date.fromisoformat(words)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", words)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        return _make_date(day, month, year, today, text)
    m = (re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?(?: of)? ([a-z]+)(?: (\d{4}))?", words)
         or re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?", words))
    if m:
        a, b, year = m.groups()
        day_s, month_s = (a, b) if a.isdigit() else (b, a)
        month = next((i + 1 for i, name in enumerate(_MONTHS)
                      if len(month_s) >= 3 and name.startswith(month_s)), None)
        if month:
            return _make_date(int(day_s), month, year, today, text)
    raise TaskError(f"I couldn't read the date '{text}'. Give it as YYYY-MM-DD, or a word like "
                    "today, tomorrow or friday.")


def _make_date(day: int, month: int, year: str | None, today: date, text: str) -> date:
    try:
        if year:
            return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
        found = date(today.year, month, day)
    except ValueError:
        raise TaskError(f"'{text}' isn't a real date.") from None
    # No year: this year, unless that's well in the past, then next year.
    if found < today - timedelta(days=60):
        found = found.replace(year=today.year + 1)
    return found


def read_time(text: str | None) -> time | None:
    """'10:00', '10am', 'ten thirty' is up to Jarvis; '10 o'clock' -> 10:00. Empty -> None."""
    words = (text or "").lower().replace("o'clock", "").replace("o’clock", "").strip()
    words = re.sub(r"^at\s+", "", words)
    if not words:
        return None
    try:
        return parse_time(words)
    except ValueError:
        raise TaskError(f"I couldn't read the time '{text}'. Give it as HH:MM, 24-hour.") from None


def _person_key(name: str) -> str:
    key = " ".join(name.lower().split())
    key = re.sub(r"\s+(task )?list$", "", key)
    key = re.sub(r"(['’]s|s['’])$", "", key)
    return key.strip()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("’", ""))


def _like(word: str, words: list[str]) -> bool:
    return word in words or (len(word) >= 4 and any(
        len(w) >= 4 and (w.startswith(word) or word.startswith(w)) for w in words))


# --- The store --------------------------------------------------------------------------------

class TaskStore:
    """All the task lists, saved to one JSON file after every change."""

    def __init__(self, path: Path, now: Callable[[], datetime] | None = None):
        self.path = path
        self._clock = now or (lambda: datetime.now(ZoneInfo(HOME_ZONE)))
        self.people: dict[str, str] = {}     # key -> name as it's said
        self.tasks: list[Task] = []
        self.next_id = 1
        self._load()
        if _person_key(OWNER) not in self.people:
            self.people[_person_key(OWNER)] = OWNER

    # Saving and loading

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(Task)}
            self.people = dict(data.get("people", {}))
            self.tasks = [Task(**{k: v for k, v in t.items() if k in known})
                          for t in data.get("tasks", [])]
            self.next_id = max([int(data.get("next_id", 1))] + [t.id + 1 for t in self.tasks])
        except (OSError, ValueError, TypeError, AttributeError) as e:
            # Keep the damaged file for a person to look at rather than overwrite it.
            spare = self.path.with_name(f"{self.path.name}.unreadable-{self._now():%Y%m%d%H%M%S}")
            log.error("couldn't read %s (%s); kept it as %s and started empty", self.path, e, spare)
            try:
                self.path.replace(spare)
            except OSError:
                pass
            self.people, self.tasks, self.next_id = {}, [], 1

    def _save(self) -> None:
        data = {"version": 1, "next_id": self.next_id, "people": self.people,
                "tasks": [asdict(t) for t in self.tasks]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # Small helpers

    def _now(self) -> datetime:
        return self._clock().astimezone(ZoneInfo(HOME_ZONE))

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def today(self) -> date:
        return self._now().date()

    def person(self, name: str | None, speaker: str | None = None) -> str:
        """The key of a named list; no name (or 'me') means the speaker's, then Craig's."""
        raw = (name or "").strip()
        if raw.lower() in _SELF or _person_key(raw) in _SELF:
            raw = speaker or OWNER
        key = _person_key(raw)
        if key in self.people:
            return key
        if key.endswith("s") and key[:-1] in self.people:   # "Sams" misheard for "Sam's"
            return key[:-1]
        raise TaskError(f"There's no task list for {raw} yet. Check the name with them; if it's "
                        f"right, use add_person to start one. Lists so far: {self._names()}.")

    def _names(self) -> str:
        return ", ".join(self.people.values())

    def _whose(self, key: str) -> str:
        return f"{self.people.get(key, key)}'s"

    def _say_when(self, t: Task) -> str:
        if not t.due_date:
            return "no due date"
        d, today = date.fromisoformat(t.due_date), self.today()
        day = {0: "today", 1: "tomorrow", -1: "yesterday"}.get((d - today).days, say_date(d))
        return f"{day} at {say_time(time.fromisoformat(t.due_time))}" if t.due_time else day

    def _tag(self, t: Task) -> str:
        due = " ".join(x for x in (t.due_date, t.due_time) if x) or "no date"
        return f"[task {t.id}; {due}; {t.status}]"

    def _line(self, t: Task, show_status: bool = True) -> str:
        flags = []
        if show_status and t.status != "open":
            flags.append(_STATUS_WORDS[t.status])
        if t.active and t.due_date and t.due_date < self.today().isoformat():
            flags.append("overdue")
        flag = f" ({', '.join(flags)})" if flags else ""
        return f"{t.title}, {self._say_when(t)}{flag}. {self._tag(t)}"

    def details(self, t: Task) -> str:
        lines = [f"{t.title}, on {self._whose(t.person)} list. Due {self._say_when(t)}. "
                 f"Status: {_STATUS_WORDS[t.status]}."]
        if t.active and t.due_date and t.due_date < self.today().isoformat():
            lines.append("It's overdue.")
        lines.append(f"Notes: {t.notes}" if t.notes else "No notes yet.")
        lines.append(f"{self._tag(t)[:-1]}; created {t.created[:10]}; updated {t.updated[:10]}]")
        return "\n".join(lines)

    def _resolve_due(self, day_text: str | None, time_text: str | None,
                     current: tuple[str | None, str | None] = (None, None)
                     ) -> tuple[str | None, str | None]:
        """New (due_date, due_time) from what was said; 'none' clears. A time with no date goes
        on the next day it's still ahead (today, or tomorrow once it's passed)."""
        due_date, due_time = current
        if day_text is not None and day_text.strip().lower() in _CLEAR:
            due_date, due_time = None, None
        elif day_text:
            due_date = parse_day(day_text, self.today()).isoformat()
        if time_text is not None and time_text.strip().lower() in _CLEAR:
            due_time = None
        elif time_text:
            at = read_time(time_text)
            due_time = at.strftime("%H:%M")
            if not due_date:
                now = self._now()
                day = now.date() + timedelta(days=1 if at <= now.time() else 0)
                due_date = day.isoformat()
        return due_date, due_time

    # Finding a task

    def _locate(self, key: str, ref: str | None, day: str | None = None,
                at: str | None = None, deleted: bool = False) -> Task:
        """One task from a number or some words, optionally narrowed by date and time.
        Raises TaskError (saying what to ask) when there's no match or more than one."""
        ref = (ref or "").strip()
        m = re.fullmatch(r"(?:task\s*)?(?:number\s*|no\.?\s*)?#?\s*(\d+)", ref, re.I)
        if m:
            found = next((t for t in self.tasks if t.id == int(m.group(1))
                          and (t.status == "deleted") == deleted), None)
            if not found:
                raise TaskError(f"There's no {'deleted ' if deleted else ''}task number "
                                f"{m.group(1)}.")
            return found
        pool = [t for t in self.tasks if t.person == key and (t.status == "deleted") == deleted]
        if day:
            wanted = parse_day(day, self.today()).isoformat()
            pool = [t for t in pool if t.due_date == wanted]
        if at:
            wanted_t = read_time(at).strftime("%H:%M")
            pool = [t for t in pool if t.due_time == wanted_t]
            if not day:   # "the ten o'clock meeting": today's if there is one, else upcoming
                today = self.today().isoformat()
                pool = ([t for t in pool if t.due_date == today]
                        or [t for t in pool if (t.due_date or "") >= today] or pool)
        query = [w for w in _words(ref) if w not in _STOPWORDS]
        if query:
            scored = []
            for t in pool:
                title, notes = _words(t.title), _words(t.notes)
                score = sum(2 if _like(w, title) else 1 if _like(w, notes) else 0 for w in query)
                if score:
                    scored.append((score, t))
            best = max((s for s, _ in scored), default=0)
            pool = [t for s, t in scored if s == best]
        if len(pool) > 1 and not deleted:
            pool = [t for t in pool if t.active] or pool
        if len(pool) == 1:
            return pool[0]
        what = " ".join(x for x in (ref and f"'{ref}'", day and f"on {day}", at and f"at {at}")
                        if x) or "that"
        if not pool:
            elsewhere = [t for t in self.tasks if t.person != key and t.status != "deleted"
                         and query and any(_like(w, _words(t.title)) for w in query)]
            hint = "".join(f"\n{self._whose(t.person)} list has: {self._line(t)}"
                           for t in elsewhere[:3])
            raise TaskError(f"I can't find a {'deleted ' if deleted else ''}task matching {what} "
                            f"on {self._whose(key)} list.{hint}")
        listed = "\n".join(self._line(t) for t in sorted(pool, key=Task.sort_key)[:8])
        raise TaskError(f"More than one task matches {what}. Ask which one they mean:\n{listed}")

    # What the tools call

    def add_person(self, name: str) -> str:
        name = " ".join((name or "").split())
        key = _person_key(name)
        if not key or len(key) > 40 or not re.fullmatch(r"[\w .'’-]+", key):
            raise TaskError("Give the person's name, just letters, e.g. 'Sam'.")
        if key in self.people:
            return f"{self.people[key]} already has a task list."
        display = name if any(c.isupper() for c in name) else name.title()
        display = re.sub(r"(['’]s|s['’])$", "", display)
        self.people[key] = display
        self._save()
        return f"Started a task list for {display}."

    def list_people(self) -> str:
        parts = []
        for key, name in self.people.items():
            n = sum(1 for t in self.tasks if t.person == key and t.active)
            parts.append(f"{name} ({_plural(n, 'task')} to do)")
        return "Task lists: " + "; ".join(parts) + "."

    def add_task(self, title: str, person: str | None = None, due_date: str | None = None,
                 due_time: str | None = None, notes: str | None = None,
                 speaker: str | None = None) -> str:
        title = " ".join((title or "").split())
        if not title:
            raise TaskError("What's the task? I need a short description.")
        key = self.person(person, speaker)
        day, at = self._resolve_due(due_date, due_time)
        stamp = self._stamp()
        task = Task(self.next_id, key, title, due_date=day, due_time=at,
                    notes=(notes or "").strip(), created=stamp, updated=stamp)
        self.next_id += 1
        self.tasks.append(task)
        self._save()
        return f"Added to {self._whose(key)} list: {self._line(task)}"

    def list_tasks(self, person: str | None = None, status: str | None = None,
                   day: str | None = None, speaker: str | None = None) -> str:
        key = self.person(person, speaker)
        status = (status or "active").strip().lower().replace(" ", "_").replace("-", "_")
        today = self.today().isoformat()
        keep = {
            "active": lambda t: t.active, "open": lambda t: t.status == "open",
            "on_hold": lambda t: t.status == "on_hold", "done": lambda t: t.status == "done",
            "all": lambda t: t.status != "deleted", "deleted": lambda t: t.status == "deleted",
            "overdue": lambda t: t.active and bool(t.due_date) and t.due_date < today,
            "undated": lambda t: t.active and not t.due_date,
        }.get(status)
        if keep is None:
            raise TaskError(f"Unknown status '{status}'.")
        wanted = parse_day(day, self.today()) if day else None
        found = sorted((t for t in self.tasks if t.person == key and keep(t)
                        and (wanted is None or t.due_date == wanted.isoformat())),
                       key=Task.sort_key)
        label = {"active": "", "open": "open ", "on_hold": "on-hold ", "done": "completed ",
                 "all": "", "deleted": "deleted ", "overdue": "overdue ",
                 "undated": "undated "}[status]
        when = f" due {say_date(wanted)}" if wanted else ""
        whose = self.people[key]
        if not found:
            return f"{whose} has no {label}tasks{when}."
        noun = "task" if len(found) == 1 else "tasks"
        head = f"{whose} has {_count(len(found))} {label}{noun}{when}:"
        return head + "\n" + "\n".join(self._line(t) for t in found)

    def rundown(self, person: str | None = None, day: str | None = None,
                speaker: str | None = None) -> str:
        """A day's tasks in time order, overdue ones flagged, on-hold ones separately."""
        key = self.person(person, speaker)
        today = self.today()
        wanted = parse_day(day, today) if day else today
        name = "today" if wanted == today else say_date(wanted)
        mine = [t for t in self.tasks if t.person == key]
        due = sorted((t for t in mine if t.due_date == wanted.isoformat()), key=Task.sort_key)
        overdue = sorted((t for t in mine if t.active and t.due_date
                          and t.due_date < today.isoformat() and wanted == today),
                         key=Task.sort_key)
        todo = [t for t in due if t.status == "open"]
        held = [t for t in due + overdue if t.status == "on_hold"]
        late = [t for t in overdue if t.status == "open"]
        done = [t for t in due if t.status == "done"]
        whose = self.people[key]
        on = f"today, {say_date(today)}" if wanted == today else say_date(wanted)
        if not (todo or held or late):
            extra = f" ({_plural(len(done), 'task')} already done)" if done else ""
            return (f"{whose} has nothing left to do {on}{extra}"
                    f"{' and nothing overdue' if wanted == today else ''}.")
        lines = [f"{whose} has {_plural(len(todo), 'task')} for {on}"
                 + (f", and {_count(len(late))} overdue" if late else "") + "."]
        for t in todo:
            when = (f"At {say_time(time.fromisoformat(t.due_time))}" if t.due_time
                    else f"Any time {name}")
            lines.append(f"{when}: {t.title}. {self._tag(t)}")
        if late:
            lines.append("Overdue:")
            lines += [f"{t.title}, due {self._say_when(t)}. {self._tag(t)}" for t in late]
        if held:
            lines.append("On hold:")
            lines += [f"{t.title}, due {self._say_when(t)}. {self._tag(t)}" for t in held]
        if done:
            lines.append(f"Already done {name}: {_plural(len(done), 'task')}.")
        return "\n".join(lines)

    def find_task(self, query: str | None = None, person: str | None = None,
                  day: str | None = None, at: str | None = None,
                  speaker: str | None = None) -> str:
        if not (query or day or at):
            raise TaskError("Say what the task is about, or its date or time.")
        key = self.person(person, speaker)
        return self.details(self._locate(key, query, day, at))

    def update_task(self, task: str, person: str | None = None, title: str | None = None,
                    due_date: str | None = None, due_time: str | None = None,
                    status: str | None = None, speaker: str | None = None) -> str:
        key = self.person(person, speaker)
        t = self._locate(key, task)
        status = (status or "").strip().lower().replace(" ", "_").replace("-", "_") or None
        if status and status not in STATUSES:
            raise TaskError(f"Status must be one of {', '.join(STATUSES)}.")
        if not (title or due_date or due_time or status):
            raise TaskError("What should change about it?")
        said = []
        if title and title.strip() and title.strip() != t.title:
            said.append(f"renamed from '{t.title}'")
            t.title = " ".join(title.split())
        if due_date or due_time:
            t.due_date, t.due_time = self._resolve_due(due_date, due_time,
                                                       (t.due_date, t.due_time))
            said.append(f"now due {self._say_when(t)}")
        if status and status != t.status:
            said.append({"done": "ticked off", "on_hold": "put on hold",
                         "open": "back on the to-do list"}[status])
            t.status = status
            t.completed = self._stamp() if status == "done" else None
        t.updated = self._stamp()
        self._save()
        change = ", ".join(said) if said else "no change needed"
        return f"{t.title}: {change}. {self._tag(t)}"

    def delete_task(self, task: str, person: str | None = None, restore: bool = False,
                    speaker: str | None = None) -> str:
        key = self.person(person, speaker)
        t = self._locate(key, task, deleted=restore)
        if restore:
            t.status = "done" if t.completed else "open"
            t.updated = self._stamp()
            self._save()
            return f"Restored to {self._whose(t.person)} list: {self._line(t)}"
        t.status = "deleted"
        t.updated = self._stamp()
        self._save()
        return (f"Removed from {self._whose(t.person)} list: {t.title}. It can be restored if "
                f"that was a mistake. {self._tag(t)}")

    def task_notes(self, task: str, text: str, person: str | None = None,
                   replace: bool = False, speaker: str | None = None) -> str:
        key = self.person(person, speaker)
        t = self._locate(key, task)
        text = (text or "").strip()
        if replace:
            t.notes = text
        elif text:
            t.notes = f"{t.notes}\n{text}" if t.notes else text
        else:
            raise TaskError("There's nothing to add to the notes.")
        t.updated = self._stamp()
        self._save()
        return f"Notes on '{t.title}' saved. {self._tag(t)}\nNotes now: {t.notes or '(none)'}"
