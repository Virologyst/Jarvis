"""Task lists: per-person lists, dates in Brisbane time, rundowns, notes, saved across restarts."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.persona import SYSTEM_PROMPT  # noqa: E402
from jarvis_brain.tasks import TaskError, TaskStore, parse_day  # noqa: E402
from jarvis_brain.tools import TOOLS, ToolBox  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    BRISBANE = ZoneInfo("Australia/Brisbane")
except Exception:  # pragma: no cover - no timezone database
    BRISBANE = None

pytestmark = pytest.mark.skipif(BRISBANE is None, reason="no timezone database")

TUESDAY = date(2026, 10, 27)


def store(tmp_path: Path, hour: int = 9, minute: int = 0) -> TaskStore:
    when = datetime(2026, 10, 27, hour, minute, tzinfo=BRISBANE)
    return TaskStore(tmp_path / "tasks.json", now=lambda: when)


# --- Dates -------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("today", TUESDAY), ("tomorrow", date(2026, 10, 28)), ("yesterday", date(2026, 10, 26)),
    ("friday", date(2026, 10, 30)), ("on Friday", date(2026, 10, 30)),
    ("tuesday", TUESDAY), ("next tuesday", date(2026, 11, 3)),
    ("in 3 days", date(2026, 10, 30)), ("next week", date(2026, 11, 3)),
    ("2026-11-05", date(2026, 11, 5)), ("5 November", date(2026, 11, 5)),
    ("November 5th", date(2026, 11, 5)), ("5/11", date(2026, 11, 5)),
    ("3 January", date(2027, 1, 3)), ("20 October", date(2026, 10, 20)),
])
def test_parse_day(text, expected) -> None:
    assert parse_day(text, TUESDAY) == expected


def test_bad_date_says_so() -> None:
    with pytest.raises(TaskError):
        parse_day("whenever", TUESDAY)
    with pytest.raises(TaskError):
        parse_day("31/2", TUESDAY)


# --- People ------------------------------------------------------------------------------------

def test_default_list_is_craigs_and_others_need_creating(tmp_path) -> None:
    s = store(tmp_path)
    assert "Craig's list" in s.add_task("Buy coffee")
    assert s.tasks[0].person == "craig"
    assert "no task list for Sam" in str(pytest.raises(TaskError, s.add_task, "x", "Sam").value)
    assert "Started a task list for Sam" in s.add_person("sam")
    assert "already has" in s.add_person("Sam")
    assert "Sam's list" in s.add_task("Fix the printer", "Sam's")
    assert "Sam's list" in s.add_task("Order toner", "sam's list")
    assert "Craig (one task to do)" in s.list_people()


def test_speaker_hook_picks_the_default_list(tmp_path) -> None:
    s = store(tmp_path)
    s.add_person("Sam")
    s.add_task("Ring the bank", speaker="Sam")
    assert s.tasks[0].person == "sam"
    s.add_task("Ring the bank", person="me", speaker="Sam")
    assert s.tasks[1].person == "sam"


# --- Adding, finding, changing -----------------------------------------------------------------

def test_time_without_date_is_the_next_one(tmp_path) -> None:
    s = store(tmp_path, hour=11)
    s.add_task("Stand-up", due_time="10:00")         # already past at 11 -> tomorrow
    s.add_task("Lunch", due_time="13:30")
    assert s.tasks[0].due_date == "2026-10-28"
    assert s.tasks[1].due_date == "2026-10-27"
    assert "today at half past one in the afternoon" in s.find_task("lunch")


def test_status_changes_and_edits(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Send the invoice to Acme", due_date="today")
    assert "ticked off" in s.update_task("invoice", status="done")
    assert s.tasks[0].status == "done" and s.tasks[0].completed
    assert "back on the to-do list" in s.update_task("1", status="open")
    assert "put on hold" in s.update_task("task 1", status="on hold")
    out = s.update_task("1", title="Send the Acme invoice", due_date="friday", due_time="14:00")
    assert "renamed" in out and "two o'clock in the afternoon" in out
    assert (s.tasks[0].due_date, s.tasks[0].due_time) == ("2026-10-30", "14:00")
    s.update_task("1", due_time="none")
    assert s.tasks[0].due_time is None and s.tasks[0].due_date == "2026-10-30"
    s.update_task("1", due_date="none")
    assert s.tasks[0].due_date is None
    with pytest.raises(TaskError):
        s.update_task("1")


def test_delete_is_recoverable(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Old idea")
    assert "can be restored" in s.delete_task("old idea")
    assert "no tasks" in s.list_tasks()
    assert "Old idea" in s.list_tasks(status="deleted")
    with pytest.raises(TaskError):
        s.find_task("old idea")
    assert "Restored" in s.delete_task("old idea", restore=True)
    assert s.tasks[0].status == "open"


def test_ambiguous_and_missing_matches_ask(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Meeting with Sam", due_date="today", due_time="10:00")
    s.add_task("Meeting with the bank", due_date="today", due_time="15:00")
    err = pytest.raises(TaskError, s.update_task, "meeting", status="done").value
    assert "More than one" in str(err) and "[task 1" in str(err) and "[task 2" in str(err)
    assert "can't find" in str(pytest.raises(TaskError, s.find_task, "dentist").value)
    assert "Meeting with the bank" in s.find_task("bank meeting")


def test_list_by_status_and_date(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("A", due_date="today")
    s.add_task("B", due_date="tomorrow")
    s.add_task("C", due_date="yesterday")
    s.add_task("D")
    s.update_task("2", status="on_hold")
    s.update_task("4", status="done")
    assert s.list_tasks().startswith("Craig has three tasks:")
    assert "C, yesterday (overdue)" in s.list_tasks()
    assert "B, tomorrow (on hold)" in s.list_tasks()
    assert "D" not in s.list_tasks()
    assert "D, no due date" in s.list_tasks(status="done")
    assert s.list_tasks(status="overdue").count("[task") == 1
    out = s.list_tasks(status="all", day="tomorrow")
    assert out.count("[task") == 1 and "Wednesday the twenty-eighth of October" in out


# --- The daily rundown -------------------------------------------------------------------------

def test_rundown_orders_flags_and_separates(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Afternoon call", due_date="today", due_time="15:00")
    s.add_task("Water the plants", due_date="today")
    s.add_task("Team meeting", due_date="today", due_time="10:00")
    s.add_task("Late report", due_date="yesterday")
    s.add_task("Paused thing", due_date="today")
    s.add_task("Finished thing", due_date="today")
    s.add_task("Next week's thing", due_date="next week")
    s.update_task("Paused", status="on_hold")
    s.update_task("Finished", status="done")
    out = s.rundown()
    lines = out.splitlines()
    assert lines[0] == ("Craig has three tasks for today, Tuesday the twenty-seventh of October, "
                        "and one overdue.")
    assert lines[1].startswith("At ten o'clock in the morning: Team meeting.")
    assert lines[2].startswith("At three o'clock in the afternoon: Afternoon call.")
    assert lines[3].startswith("Any time today: Water the plants.")
    assert lines[4] == "Overdue:" and lines[5].startswith("Late report, due yesterday.")
    assert lines[6] == "On hold:" and "Paused thing" in lines[7]
    assert "Already done today: one task." in out
    assert "Next week" not in out and "*" not in out and "#" not in out


def test_rundown_when_free_and_for_another_day(tmp_path) -> None:
    s = store(tmp_path)
    assert s.rundown() == ("Craig has nothing left to do today, Tuesday the twenty-seventh of "
                           "October and nothing overdue.")
    s.add_task("Dentist", due_date="tomorrow", due_time="08:30")
    s.add_task("Old thing", due_date="yesterday")
    out = s.rundown(day="tomorrow")
    assert "Wednesday the twenty-eighth of October" in out
    assert "half past eight in the morning: Dentist" in out and "Overdue" not in out


# --- Notes and contextual lookup ---------------------------------------------------------------

def test_notes_and_finding_by_time(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Meeting with Sam", due_date="today", due_time="10:00")
    s.add_task("Meeting with the accountant", due_date="tomorrow", due_time="10:00")
    s.task_notes("Meeting with Sam", "Agenda: the new website and hosting costs.")
    s.task_notes("1", "Sam is bringing the quotes.")
    found = s.find_task("meeting", at="10:00")             # today's ten o'clock wins
    assert "Meeting with Sam" in found
    assert "Agenda: the new website and hosting costs.\nSam is bringing the quotes." in found
    assert "accountant" in s.find_task(at="10am", day="tomorrow")
    assert "Meeting with Sam" in s.find_task("website")   # notes count too
    s.task_notes("1", "Agenda: hosting only.", replace=True)
    assert s.tasks[0].notes == "Agenda: hosting only."
    assert "No notes yet." in s.find_task("accountant")


# --- Saving ------------------------------------------------------------------------------------

def test_survives_a_restart(tmp_path) -> None:
    s = store(tmp_path)
    s.add_person("Sam")
    s.add_task("Fix printer", "Sam", "friday", "09:15", "Paper jam in tray two.")
    again = store(tmp_path)
    assert "sam" in again.people
    t = again.tasks[0]
    assert (t.title, t.person, t.due_date, t.due_time, t.notes) == (
        "Fix printer", "sam", "2026-10-30", "09:15", "Paper jam in tray two.")
    again.add_task("Another")
    assert again.tasks[1].id == 2


def test_unreadable_file_is_kept_aside(tmp_path) -> None:
    (tmp_path / "tasks.json").write_text("{not json", encoding="utf-8")
    s = store(tmp_path)
    assert s.tasks == [] and "craig" in s.people
    assert list(tmp_path.glob("tasks.json.unreadable-*"))
    s.add_task("Fresh start")
    assert json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))["tasks"]


# --- As tools ----------------------------------------------------------------------------------

def test_task_tools_are_offered_and_mentioned_in_the_persona() -> None:
    names = {t["name"] for t in TOOLS}
    for name in ("add_task", "tasks_for_day", "list_tasks", "find_task", "update_task",
                 "delete_task", "task_notes", "add_person", "list_people"):
        assert name in names
        assert name in SYSTEM_PROMPT


def test_task_tools_run_without_cards(tmp_path) -> None:
    async def no_card(card):
        raise AssertionError("task tools must not show cards")

    box = ToolBox(upgrades=None, show_card=no_card, tasks=store(tmp_path))
    run = lambda tool, **args: asyncio.run(box.run(tool, args))  # noqa: E731
    assert "Craig's list" in run("add_task", title="Team meeting", due_date="today",
                                 due_time="10:00", person="", notes="")
    assert "Team meeting" in run("tasks_for_day")
    assert "saved" in run("task_notes", task="1", text="About the roadmap.")
    assert "About the roadmap." in run("find_task", query="", time="10:00")
    assert "ticked off" in run("update_task", task="meeting", status="done")
    assert "no task list for Sam" in run("list_tasks", person="Sam")   # told, not raised
    assert "Started" in run("add_person", name="Sam")
    assert "Sam" in run("list_people")
    assert "removed" in run("delete_task", task="1").lower()


def test_speaker_callable_reaches_the_store(tmp_path) -> None:
    async def no_card(card):
        pass

    s = store(tmp_path)
    s.add_person("Sam")
    box = ToolBox(upgrades=None, show_card=no_card, tasks=s, speaker=lambda: "Sam")
    asyncio.run(box.run("add_task", {"title": "Sam's own job"}))
    assert s.tasks[0].person == "sam"
