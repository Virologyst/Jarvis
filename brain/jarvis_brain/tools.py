"""Tools Jarvis can call during a conversation.

Every tool that changes anything outside Jarvis only *proposes* it: the handler returns a card for
the screen and the real work runs after someone taps Approve (see approvals.py). Jarvis's own
state (the screen theme, the task lists) changes directly, and so do Sir's Obsidian project notes,
which he asked Jarvis to keep (additions and ticks only, each one logged). Reading files (on the
brain or a terminal) runs directly; writing files on the brain is proposed on a card like
everything else. Add new tools here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from jarvis_protocol import Card

from . import clock
from .files import FileAccessError, FileReader
from .filewrite import FileWriter
from .tasks import TaskError, TaskStore
from .themes import THEMES, describe
from .vault import Vault, VaultError

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
_REMIND_WHEN = ("When to remind them, Brisbane time: a date and time like '2026-10-30 16:30' or "
                "'tomorrow 09:00', a time like '15:00' (the next one), 'in 20 minutes', or "
                "relative to when it's due: 'an hour before', '30 minutes before', 'a day "
                "before', 'the morning of', 'the evening before', 'when it's due'.")

TOOLS += [
    {
        "name": "add_task",
        "description": (
            "Add a task or appointment to someone's to-do list, with an optional due date and "
            "time (Brisbane). A time with no date means the next time it comes round. Put any "
            "context you already know (agenda, who's involved, background) in notes. If they "
            "already said when to be reminded, put it in reminder; if not, ask afterwards."),
        "input_schema": _schema(
            {"title": {"type": "string", "description": "Short description, e.g. 'Meeting with "
                                                        "Sam about the website'."},
             "person": _PERSON,
             "due_date": {"type": "string", "description": "When it's due: " + _DATE},
             "due_time": {"type": "string", "description": "A specific time, HH:MM 24-hour."},
             "notes": {"type": "string", "description": "Context to keep with the task."},
             "reminder": {"type": "string", "description": _REMIND_WHEN}},
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
        "name": "set_task_reminder",
        "description": (
            "Set, change, add, clear or snooze a reminder on a task. When it falls due you say "
            "it aloud on the office screens, unprompted. set replaces any reminder still to "
            "come; add keeps them and adds another; clear removes them; snooze says it again "
            "after a while, e.g. 'snooze it fifteen minutes' -> when '15 minutes' (leave task "
            "empty to snooze the reminder you just gave). The result gives the exact Brisbane "
            "time it settled on: say it back so they can correct it."),
        "input_schema": _schema(
            {"task": {"type": "string",
                      "description": _TASK_REF["description"] + " Empty only for snooze, "
                                                                "meaning the reminder just given."},
             "person": _PERSON,
             "action": {"type": "string", "enum": ["set", "add", "clear", "snooze"]},
             "when": {"type": "string",
                      "description": (_REMIND_WHEN + " For snooze: how long, like '15 minutes' "
                                      "or 'an hour', or a time. Empty for clear.")}},
            ["action"]),
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
               "delete_task", "task_notes", "set_task_reminder", "add_person", "list_people"}

# --- Files (files.py, filewrite.py, and terminals' jarvis_terminal/files.py) --------------------
# Reading is read-only and runs without a card, on the brain's C: drive or on a terminal that
# shares its drive (the terminal applies the same privacy checks itself). Writing is on the
# brain only, and every write is just proposed: it happens after someone approves the card.
# Nothing deletes files. Jarvis's own code, settings and machinery and secret stores are
# refused everywhere.

_PATH = {"type": "string",
         "description": "Full Windows path on the C drive, e.g. 'C:\\Users\\Craig\\Documents'."}
_DEVICE = {"type": "string",
           "description": ("Which machine: 'brain' (the machine you run on), 'this terminal' "
                           "(the one you're being spoken to through), or a terminal's name from "
                           "list_file_devices. Empty: the brain if the path exists there, "
                           "otherwise this terminal.")}
_BRAIN_PATH = {"type": "string",
               "description": "Full Windows path on the brain's C drive."}

TOOLS += [
    {
        "name": "list_file_devices",
        "description": ("Which machines' files you can use: the brain, and the terminals "
                        "sharing their drives right now (by name, and which one is this one)."),
        "input_schema": _schema(),
        "strict": True,
    },
    {
        "name": "list_directory",
        "description": ("List the folders and files in a folder on the C drive of the brain or "
                        "a terminal (read-only)."),
        "input_schema": _schema({"path": _PATH, "device": _DEVICE}, ["path"]),
        "strict": True,
    },
    {
        "name": "read_file",
        "description": (
            "Read a text file on the C drive of the brain or a terminal (read-only): Markdown "
            "notes, text, Obsidian canvas mind maps (given as cards and connections), JSON and "
            "other text formats. Long files come back in parts; the result says which start to "
            "use to read on. Binary files (PDFs, images, Word documents) aren't read; the result "
            "says what they are."),
        "input_schema": _schema(
            {"path": _PATH,
             "start": {"type": "integer",
                       "description": "Character to start from: 0 for the beginning."},
             "device": _DEVICE},
            ["path", "start"]),
        "strict": True,
    },
    {
        "name": "search_files",
        "description": (
            "Find files on the C drive of the brain or a terminal whose names contain all the "
            "words in query, and with contents true, files whose text contains them too "
            "(slower). Searches under root, or the home folder if root is empty; give the "
            "Obsidian vault's folder when you know it."),
        "input_schema": _schema(
            {"query": {"type": "string", "description": "Words to look for."},
             "root": {"type": "string", "description": "Folder to search under."},
             "contents": {"type": "boolean", "description": "True to search inside text files."},
             "device": _DEVICE},
            ["query"]),
        "strict": True,
    },
    {
        "name": "write_file",
        "description": (
            "Create a text file on the brain's C drive (notes .md, .txt, canvas, JSON, CSV and "
            "similar), replace all of an existing one's text, or add to its end. Only on the "
            "brain, never on a terminal. Shows an approval card with what will be written; "
            "nothing is written until it's approved. Missing folders on the way are created."),
        "input_schema": _schema(
            {"path": _BRAIN_PATH,
             "text": {"type": "string", "description": "The text to write."},
             "mode": {"type": "string", "enum": ["create", "replace", "append"],
                      "description": ("create (default) = a new file, never over an existing "
                                      "one; replace = all of an existing file's text; append = "
                                      "add to the end of an existing file.")}},
            ["path", "text"]),
        "strict": True,
    },
    {
        "name": "edit_file",
        "description": (
            "Change one exact piece of text in an existing text file on the brain's C drive, "
            "e.g. fix a line in a note. Read the file first and copy the text to change exactly; "
            "it must appear once. Shows an approval card; nothing changes until it's approved."),
        "input_schema": _schema(
            {"path": _BRAIN_PATH,
             "find": {"type": "string", "description": "The exact text to change."},
             "replace": {"type": "string", "description": "What it becomes (can be empty)."}},
            ["path", "find", "replace"]),
        "strict": True,
    },
    {
        "name": "create_folder",
        "description": ("Create a folder on the brain's C drive. Shows an approval card; "
                        "nothing is created until it's approved."),
        "input_schema": _schema({"path": _BRAIN_PATH}, ["path"]),
        "strict": True,
    },
    {
        "name": "move_file",
        "description": (
            "Move or rename a file or folder on the brain's C drive. Never replaces something "
            "already there. Shows an approval card; nothing moves until it's approved."),
        "input_schema": _schema(
            {"path": _BRAIN_PATH,
             "to": {"type": "string",
                    "description": "The new full path, or a folder to move it into."}},
            ["path", "to"]),
        "strict": True,
    },
]
_FILE_TOOLS = {"list_directory", "read_file", "search_files"}
_WRITE_TOOLS = {"write_file", "edit_file", "create_folder", "move_file"}
# A terminal's file reader: (op, args) -> text, raising FileAccessError for refusals
RemoteFiles = Callable[[str, dict], Awaitable[str]]
_THIS_TERMINAL = {"this terminal", "this", "here", "terminal", "this one", "this device",
                  "this computer", "this pc", "local"}
_BRAIN = {"brain", "the brain", "server", "jarvis"}

# --- Sir's Obsidian vault (vault.py) --------------------------------------------------------------
# Project notes Sir asked Jarvis to keep up to date. Nothing is ever deleted (tasks are ticked
# off, not removed) and every change is written in the project's Log, so these run without a card.

_PROJECT = {"type": "string",
            "description": "Which project, by name, e.g. 'J.A.R.V.I.S' or 'Jarvis'."}

TOOLS += [
    {
        "name": "vault_projects",
        "description": ("Every project in Sir's Obsidian notes: status, progress, next step and "
                        "how many tasks are open or marked for you (#j)."),
        "input_schema": _schema(),
        "strict": True,
    },
    {
        "name": "vault_read",
        "description": (
            "Read a note from Sir's Obsidian vault. For a project: its status, progress, next "
            "step, open tasks with their numbers, and recent log. For any other note (e.g. the "
            "Success Map): its text. Give a heading to read just that part of a long note."),
        "input_schema": _schema(
            {"note": {"type": "string", "description": "The note or project's name."},
             "section": {"type": "string", "description": "A heading to read on its own."}},
            ["note"]),
        "strict": True,
    },
    {
        "name": "vault_add_task",
        "description": (
            "Add a checkbox task to a project. Goes under the Tasks heading unless a heading is "
            "given (e.g. 'Next up', or a group like 'Brain PC'), or under an existing task as a "
            "sub-step. for_j true marks it #j: one you may do or chase."),
        "input_schema": _schema(
            {"project": _PROJECT,
             "text": {"type": "string", "description": "The task, short."},
             "section": {"type": "string", "description": "Heading to put it under."},
             "under": {"type": "string",
                       "description": "An existing task (number or words) to add it beneath."},
             "for_j": {"type": "boolean", "description": "True to mark it #j."}},
            ["project", "text"]),
        "strict": True,
    },
    {
        "name": "vault_update_task",
        "description": ("Tick off (done), reopen (open) or reword a project's task. Refer to it "
                        "by its number from vault_read (best) or words from it."),
        "input_schema": _schema(
            {"project": _PROJECT,
             "task": {"type": "string", "description": "The task's number or words from it."},
             "status": {"type": "string", "enum": ["done", "open"]},
             "text": {"type": "string", "description": "New wording."}},
            ["project", "task"]),
        "strict": True,
    },
    {
        "name": "vault_update_project",
        "description": (
            "Change a project's status, progress or next step, and/or add a line to its log "
            "(decisions, milestones, what happened). Leave what doesn't change empty."),
        "input_schema": _schema(
            {"project": _PROJECT,
             "status": {"type": "string", "enum": ["idea", "planned", "active", "on hold", "done"]},
             "progress": {"type": "string", "description": "Percent done, 0 to 100."},
             "next_step": {"type": "string", "description": "The very next step, in a few words."},
             "log": {"type": "string", "description": "A line for the project's log."}},
            ["project"]),
        "strict": True,
    },
    {
        "name": "vault_new_project",
        "description": ("Start a new project note from Sir's Project template and link it from "
                        "the Success Map. Check the name with Sir first."),
        "input_schema": _schema(
            {"name": {"type": "string", "description": "The project's name."},
             "about": {"type": "string",
                       "description": "One or two sentences: what it is and what done looks like."},
             "status": {"type": "string", "enum": ["idea", "planned", "active", "on hold", "done"]}},
            ["name"]),
        "strict": True,
    },
    {
        "name": "vault_add_point",
        "description": (
            "Add a bullet point to any note under a heading (made if it doesn't exist), or "
            "beneath an existing bullet. Each bullet is a branch on that note's mind map, so use "
            "this to grow a mind map or add notes, ideas and decisions."),
        "input_schema": _schema(
            {"note": {"type": "string", "description": "The note's name."},
             "heading": {"type": "string", "description": "The heading to add it under."},
             "text": {"type": "string", "description": "The point, one line."},
             "under": {"type": "string", "description": "An existing bullet to nest it beneath."}},
            ["note", "heading", "text"]),
        "strict": True,
    },
]
_VAULT_TOOLS = {"vault_projects", "vault_read", "vault_add_task", "vault_update_task",
                "vault_update_project", "vault_new_project", "vault_add_point"}

# The API allows at most 20 strict tools per request (and 24 optional settings across them).
# Strict guarantees the input matches the schema, which matters for tools that change things
# directly. These don't need it, so they're left loose, keeping room for new tools under the
# limit (selfupgrade.py blocks an upgrade that crosses it): tools that only look something up,
# whose handlers cope with a missing or odd value, and tools that only propose a card that Sir
# reads before anything happens. A new tool that changes something directly should be strict.
_LOOSE = {
    # look-ups
    "upgrade_status", "list_themes", "get_time", "convert_time", "tasks_for_day", "list_tasks",
    "find_task", "list_people", "list_file_devices", "list_directory", "read_file",
    "search_files", "vault_projects", "vault_read",
    # only propose an approval card, or check their own input
    "undo_last_upgrade", "write_file", "edit_file", "create_folder", "move_file", "set_theme",
}
for _tool in TOOLS:
    if _tool["name"] in _LOOSE:
        _tool["strict"] = False


class ToolBox:
    """Runs tool calls for one terminal's conversation; shows any cards on that terminal."""

    def __init__(self, upgrades, show_card: Callable[[Card], Awaitable[None]],
                 current_theme: Callable[[], str] | None = None,
                 set_theme: Callable[[str], Awaitable[str]] | None = None,
                 tasks: TaskStore | None = None,
                 speaker: Callable[[], str | None] | None = None,
                 files: FileReader | None = None,
                 approvals=None,
                 terminal_id: Callable[[], str] | None = None,
                 file_terminals: Callable[[], dict[str, RemoteFiles]] | None = None,
                 vault: Vault | None = None):
        self.upgrades = upgrades
        self.show_card = show_card
        self.current_theme = current_theme
        self.set_theme = set_theme
        self.tasks = tasks
        self.vault = vault
        # Who's talking, for "my tasks". Nothing can tell voices apart yet, so None (= Craig);
        # a voice-identification step can pass a callable returning the speaker's name.
        self.speaker = speaker or (lambda: None)
        self.files = files if files is not None else FileReader()
        self.writer = FileWriter(self.files)
        self.approvals = approvals      # the ApprovalGate every file write goes through
        # This conversation's terminal, and the connected terminals sharing their drives
        self.terminal_id = terminal_id or (lambda: "")
        self.file_terminals = file_terminals or dict

    async def run(self, name: str, args: dict) -> str:
        log.info("tool %s %s", name, {k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80
                                          else v) for k, v in args.items()})
        if name == "list_file_devices":
            return self._describe_devices()
        if name in _FILE_TOOLS:
            try:
                return await self._run_read_tool(name, args)
            except FileAccessError as e:
                return str(e)
        if name in _WRITE_TOOLS:
            try:
                return await self._propose_write(name, args)
            except FileAccessError as e:
                return str(e)
        if name in _TASK_TOOLS:
            if self.tasks is None:
                raise RuntimeError("the task lists aren't available here")
            try:
                return self._run_task_tool(name, args)
            except TaskError as e:
                return str(e)
        if name in _VAULT_TOOLS:
            if self.vault is None:
                return "The notes vault isn't connected here."
            try:
                return self._run_vault_tool(name, args)
            except VaultError as e:
                return str(e)
            except OSError as e:
                log.warning("vault: %s", e)
                return f"I couldn't get to that note just now ({e.strerror or e})."
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

    # --- files --------------------------------------------------------------------------------

    async def _run_read_tool(self, name: str, args: dict) -> str:
        if name == "search_files":
            clean = {"query": str(args.get("query", "")), "root": str(args.get("root") or ""),
                     "contents": bool(args.get("contents"))}
            path = clean["root"]
        else:
            clean = {"path": str(args.get("path", ""))}
            if name == "read_file":
                try:   # a loose tool: the value may not be a number
                    clean["start"] = max(0, int(args.get("start") or 0))
                except (TypeError, ValueError):
                    clean["start"] = 0
            path = clean["path"]
        device = self._pick_device(str(args.get("device") or ""), path)
        if device is None:
            return "[On the brain] " + await asyncio.to_thread(self._run_file_tool, name, clean)
        terminal, remote = device
        return f"[On the terminal {terminal}] " + await remote(name, clean)

    def _run_file_tool(self, name: str, args: dict) -> str:
        f = self.files
        if name == "list_directory":
            return f.list_directory(args["path"])
        if name == "read_file":
            return f.read_file(args["path"], args["start"])
        return f.search_files(args["query"], args["root"] or None, args["contents"])

    def _pick_device(self, device: str, path: str) -> tuple[str, RemoteFiles] | None:
        """Which machine a read goes to: None for the brain, or (terminal name, its reader)."""
        wanted = " ".join(device.lower().replace("_", " ").split())
        terminals, here = self.file_terminals(), self.terminal_id()
        if wanted in _BRAIN:
            return None
        if not wanted or wanted == "auto":
            # The brain if the path is there, otherwise the terminal being spoken through
            if not path or self._exists_on_brain(path) or here not in terminals:
                return None
            return here, terminals[here]
        if wanted in _THIS_TERMINAL:
            if here in terminals:
                return here, terminals[here]
            raise FileAccessError("This terminal isn't sharing its files with me (only Windows "
                                  "terminals with file sharing on do). " + self._describe_devices())
        exact = [n for n in terminals if n.lower() == wanted]
        close = exact or [n for n in terminals if wanted in n.lower()]
        if len(close) == 1:
            return close[0], terminals[close[0]]
        raise FileAccessError(f"No terminal called {device} is sharing files with me right now. "
                              + self._describe_devices())

    def _exists_on_brain(self, raw: str) -> bool:
        try:
            return os.path.lexists(self.files.normalise(raw))
        except FileAccessError:
            return False

    def _describe_devices(self) -> str:
        here = self.terminal_id()
        names = sorted(self.file_terminals())
        text = ("You can read files on the brain, and create, edit, move and rename files there "
                "with approval. ")
        if not names:
            return text + "No terminals are sharing their files right now."
        listed = ", ".join(f"{n} (this terminal)" if n == here else n for n in names)
        text += f"Terminals sharing their files, read-only: {listed}."
        if here and here not in names:
            text += " This terminal isn't sharing its files."
        return text

    async def _propose_write(self, name: str, args: dict) -> str:
        """Check a write and put it on an approval card; it runs only once approved."""
        if self.approvals is None:
            return "Changing files isn't available here."
        w = self.writer
        if name == "write_file":
            plan = await asyncio.to_thread(w.write_text, str(args.get("path", "")),
                                           str(args.get("text", "")), str(args.get("mode") or ""))
        elif name == "edit_file":
            plan = await asyncio.to_thread(w.edit_text, str(args.get("path", "")),
                                           str(args.get("find", "")), str(args.get("replace", "")))
        elif name == "create_folder":
            plan = await asyncio.to_thread(w.create_folder, str(args.get("path", "")))
        else:
            plan = await asyncio.to_thread(w.move, str(args.get("path", "")),
                                           str(args.get("to", "")))

        async def action() -> str:
            return await asyncio.to_thread(plan.apply)

        card = self.approvals.propose(plan.title, plan.body, action)
        await self.show_card(card)
        return (f"An approval card is up on the brain's files: {plan.title}. Nothing changes "
                "until it's approved.")

    def _run_task_tool(self, name: str, args: dict) -> str:
        t, me = self.tasks, self.speaker()

        def arg(key: str) -> str | None:
            value = args.get(key)
            return str(value) if value not in (None, "") else None

        if name == "add_task":
            return t.add_task(arg("title") or "", arg("person"), arg("due_date"), arg("due_time"),
                              arg("notes"), arg("reminder"), speaker=me)
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
        if name == "set_task_reminder":
            return t.set_reminder(arg("task"), arg("when"), arg("action") or "set",
                                  arg("person"), speaker=me)
        if name == "add_person":
            return t.add_person(arg("name") or "")
        return t.list_people()

    def _run_vault_tool(self, name: str, args: dict) -> str:
        v = self.vault

        def arg(key: str) -> str | None:
            value = args.get(key)
            return str(value) if value not in (None, "") else None

        if name == "vault_projects":
            return v.projects()
        if name == "vault_read":
            return v.read(arg("note") or "", arg("section"))
        if name == "vault_add_task":
            return v.add_task(arg("project") or "", arg("text") or "", arg("section"),
                              arg("under"), bool(args.get("for_j")))
        if name == "vault_update_task":
            return v.update_task(arg("project") or "", arg("task") or "", arg("status"),
                                 arg("text"))
        if name == "vault_update_project":
            return v.update_project(arg("project") or "", arg("status"), arg("progress"),
                                    arg("next_step"), arg("log"))
        if name == "vault_new_project":
            return v.new_project(arg("name") or "", arg("about"), arg("status"))
        return v.add_point(arg("note") or "", arg("heading") or "", arg("text") or "",
                           arg("under"))
