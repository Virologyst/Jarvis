"""Tools Jarvis can call during a conversation.

Every tool that changes anything outside Jarvis only *proposes* it: the handler returns a card for
the screen and the real work runs after someone taps Approve (see approvals.py). Jarvis's own
state (the screen theme, the task lists) changes directly. Add new tools here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from jarvis_protocol import Card

from . import clock
from .tasks import TaskError, TaskStore
from .themes import THEMES, describe

log = logging.getLogger(__name__)


def _schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    """A strict input schema in which every property is required.

    The API caps optional parameters across all strict tools in one request (24), so instead
    of leaving a property out, Claude sends an empty value: "" for text (and enums gain ""),
    false for yes/no. Properties not listed in `required` are the ones that may be empty, and
    the handlers below treat empty as "not given".
    """
    props = {}
    for name, spec in (properties or {}).items():
        spec = dict(spec)
        if name not in (required or []):
            if spec.get("type") == "string":
                if "enum" in spec:
                    spec["enum"] = [*spec["enum"], ""]
                spec["description"] = (spec.get("description", "") + " Empty if not given.").strip()
            elif spec.get("type") == "boolean":
                spec["description"] = (spec.get("description", "") + " false if not needed.").strip()
        props[name] = spec
    return {"type": "object", "properties": props, "required": list(props),
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

# --- Task lists (tasks.py) ----------------------------------------------------------------------
# Jarvis's own notebook, like the theme choice: these change nothing outside the brain and
# deleting only moves a task to a bin it can be restored from, so they run without a card.

_PERSON = {"type": "string",
           "description": ("Whose list, by name, e.g. 'Sam'. Empty for the person talking "
                           "(Craig, until Jarvis can tell voices apart).")}
_TASK_REF = {"type": "string",
             "description": ("Which task: its number from an earlier result (best), e.g. '12', "
                             "or words from its title.")}
_DATE = ("YYYY-MM-DD, or a plain word like 'today', 'tomorrow', 'friday', 'next monday', "
         "'in 3 days'. Brisbane time.")

TOOLS += [
    {
        "name": "add_task",
        "description": (
            "Add a task or appointment to someone's to-do list, with an optional due date and "
            "time (Brisbane). A time with no date means the next time it comes round. Put any "
            "context you already know (agenda, who's involved, background) in notes."),
        "input_schema": _schema(
            {"title": {"type": "string", "description": "Short description, e.g. 'Meeting with "
                                                        "Sam about the website'."},
             "person": _PERSON,
             "due_date": {"type": "string", "description": "When it's due: " + _DATE},
             "due_time": {"type": "string", "description": "A specific time, HH:MM 24-hour."},
             "notes": {"type": "string", "description": "Context to keep with the task."}},
            ["title"]),
        "strict": True,
    },
    {
        "name": "tasks_for_day",
        "description": (
            "Someone's rundown for a day, e.g. 'what tasks do I have today?': tasks due that day "
            "in time order, overdue ones flagged (for today), on-hold ones listed separately, "
            "completed ones left out."),
        "input_schema": _schema(
            {"person": _PERSON,
             "date": {"type": "string", "description": "Which day: " + _DATE + " Empty for today."}}),
        "strict": True,
    },
    {
        "name": "list_tasks",
        "description": "List someone's tasks, filtered by status and optionally by due date.",
        "input_schema": _schema(
            {"person": _PERSON,
             "status": {"type": "string",
                        "enum": ["active", "open", "on_hold", "done", "overdue", "undated", "all",
                                 "deleted"],
                        "description": ("active (default) = open and on hold; all = everything "
                                        "except deleted.")},
             "date": {"type": "string", "description": "Only tasks due on this day: " + _DATE}}),
        "strict": True,
    },
    {
        "name": "find_task",
        "description": (
            "Look up one task and its details and notes, by what it's about and/or its date or "
            "time, e.g. 'what's my ten o'clock meeting about?' -> query 'meeting', time '10:00'. "
            "Use it to answer questions about a task. If several match, it lists them: ask which."),
        "input_schema": _schema(
            {"query": {"type": "string", "description": "Words about the task, or its number."},
             "person": _PERSON,
             "date": {"type": "string", "description": "The day it's due: " + _DATE},
             "time": {"type": "string", "description": "The time it's at, HH:MM 24-hour."}}),
        "strict": True,
    },
    {
        "name": "update_task",
        "description": (
            "Change a task: tick it off (status done), put it on hold (on_hold), resume or reopen "
            "it (open), rename it, or move its due date or time. Use 'none' to clear a date or "
            "time. Leave the fields that don't change empty."),
        "input_schema": _schema(
            {"task": _TASK_REF, "person": _PERSON,
             "status": {"type": "string", "enum": ["open", "on_hold", "done"]},
             "title": {"type": "string", "description": "New description."},
             "due_date": {"type": "string", "description": "New due date: " + _DATE},
             "due_time": {"type": "string", "description": "New time, HH:MM 24-hour."}},
            ["task"]),
        "strict": True,
    },
    {
        "name": "delete_task",
        "description": (
            "Remove a task from a list (for tasks that are no longer needed; use update_task to "
            "tick off finished ones). Removed tasks can be brought back with restore true."),
        "input_schema": _schema(
            {"task": _TASK_REF, "person": _PERSON,
             "restore": {"type": "boolean", "description": "True to bring back a removed task."}},
            ["task"]),
        "strict": True,
    },
    {
        "name": "task_notes",
        "description": (
            "Add to or rewrite a task's notes: your own record of its context (agenda, people "
            "involved, background, decisions, follow-ups). Add notes whenever the conversation "
            "tells you something useful about a task. Use replace true to rewrite them all, e.g. "
            "to correct or tidy them."),
        "input_schema": _schema(
            {"task": _TASK_REF, "person": _PERSON,
             "text": {"type": "string", "description": "The note to add, or the full new notes."},
             "replace": {"type": "boolean", "description": "True to replace all the notes."}},
            ["task", "text"]),
        "strict": True,
    },
    {
        "name": "add_person",
        "description": ("Start a task list for someone new in the office, by name. Check the "
                        "name first if speech recognition may have misheard it."),
        "input_schema": _schema({"name": {"type": "string"}}, ["name"]),
        "strict": True,
    },
    {
        "name": "list_people",
        "description": "Whose task lists there are, and how many tasks each has to do.",
        "input_schema": _schema(),
        "strict": True,
    },
]
_TASK_TOOLS = {"add_task", "tasks_for_day", "list_tasks", "find_task", "update_task",
               "delete_task", "task_notes", "add_person", "list_people"}


class ToolBox:
    """Runs tool calls for one terminal's conversation; shows any cards on that terminal."""

    def __init__(self, upgrades, show_card: Callable[[Card], Awaitable[None]],
                 current_theme: Callable[[], str] | None = None,
                 set_theme: Callable[[str], Awaitable[str]] | None = None,
                 tasks: TaskStore | None = None,
                 speaker: Callable[[], str | None] | None = None):
        self.upgrades = upgrades
        self.show_card = show_card
        self.current_theme = current_theme
        self.set_theme = set_theme
        self.tasks = tasks
        # Who's talking, for "my tasks". Nothing can tell voices apart yet, so None (= Craig);
        # a voice-identification step can pass a callable returning the speaker's name.
        self.speaker = speaker or (lambda: None)

    async def run(self, name: str, args: dict) -> str:
        log.info("tool %s %s", name, args)
        if name in _TASK_TOOLS:
            if self.tasks is None:
                raise RuntimeError("the task lists aren't available here")
            try:
                return self._run_task_tool(name, args)
            except TaskError as e:
                return str(e)
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

    def _run_task_tool(self, name: str, args: dict) -> str:
        t, me = self.tasks, self.speaker()

        def arg(key: str) -> str | None:
            value = args.get(key)
            return str(value) if value not in (None, "") else None

        if name == "add_task":
            return t.add_task(arg("title") or "", arg("person"), arg("due_date"), arg("due_time"),
                              arg("notes"), speaker=me)
        if name == "tasks_for_day":
            return t.rundown(arg("person"), arg("date"), speaker=me)
        if name == "list_tasks":
            return t.list_tasks(arg("person"), arg("status"), arg("date"), speaker=me)
        if name == "find_task":
            return t.find_task(arg("query"), arg("person"), arg("date"), arg("time"), speaker=me)
        if name == "update_task":
            return t.update_task(arg("task") or "", arg("person"), arg("title"), arg("due_date"),
                                 arg("due_time"), arg("status"), speaker=me)
        if name == "delete_task":
            return t.delete_task(arg("task") or "", arg("person"), bool(args.get("restore")),
                                 speaker=me)
        if name == "task_notes":
            return t.task_notes(arg("task") or "", arg("text") or "", arg("person"),
                                bool(args.get("replace")), speaker=me)
        if name == "add_person":
            return t.add_person(arg("name") or "")
        return t.list_people()
