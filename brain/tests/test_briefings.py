"""Scheduled briefings: when they fall due, catching up, changing them by voice, the personal
list from the Me note, and what's said. No network, no Obsidian, no Microsoft account."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.briefings import (PERSONAL, WEEKDAY, BriefingError,  # noqa: E402
                                    BriefingSchedule, BriefingService, parse_days, speech)
from jarvis_brain.persona import SYSTEM_PROMPT  # noqa: E402
from jarvis_brain.priorities import Briefing  # noqa: E402
from jarvis_brain.selfupgrade import tool_schema_problems  # noqa: E402
from jarvis_brain.tasks import TaskStore  # noqa: E402
from jarvis_brain.tools import REQUEST_TOOLS, ToolBox  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    BRISBANE = ZoneInfo("Australia/Brisbane")
except Exception:  # pragma: no cover
    BRISBANE = None

pytestmark = pytest.mark.skipif(BRISBANE is None, reason="no timezone database")

ME = """---
type: note
---
# Me
## Routines
### Daily
- Clean the kitchen
### Weekly
- Mow the lawns (last done 2026-11-14)
- Clean the car (last done 2026-11-01)
### Monthly
- Clean the caravan (last done 2026-10-13)
## Caravan
- [ ] Fix the awning
- [x] New tyres
## To do
- [ ] Book the dentist 📅 2026-11-14
- [ ] Call Mum
## Purchases
- [ ] New hose
## Log
- [ ] not a task
"""

WORK = """---
type: project
status: active
next: Order a Pi case
---
# J.A.R.V.I.S
- [ ] Wire the calendar #j
"""


class Clock:
    """Friday 13 November 2026, 8:59 am, Brisbane; moved by hand."""

    def __init__(self):
        self.now = datetime(2026, 11, 13, 8, 59, tzinfo=BRISBANE)

    def __call__(self):
        return self.now

    def forward(self, **kw):
        self.now += timedelta(**kw)


def schedule(tmp_path, clock=None):
    return BriefingSchedule(tmp_path / "briefings.json", now=clock or Clock())


# --- When ----------------------------------------------------------------------------------

def test_weekday_briefing_at_nine_once_and_saturday_personal(tmp_path) -> None:
    c = Clock()
    s = schedule(tmp_path, c)
    assert s.due() == []
    c.forward(minutes=1)
    assert s.due() == [WEEKDAY]
    s.mark_said(WEEKDAY)
    assert s.due() == []
    # said once, even after a restart
    assert schedule(tmp_path, c).due() == []
    c.forward(days=1)                      # Saturday 9 am
    assert s.due() == [PERSONAL]
    c.forward(days=1)                      # Sunday: neither
    assert s.due() == []
    c.forward(days=1)                      # Monday
    assert s.due() == [WEEKDAY]


def test_catches_up_but_not_hours_late(tmp_path) -> None:
    c = Clock()
    c.now = c.now.replace(hour=10, minute=30)   # the brain came up late
    assert schedule(tmp_path, c).due() == [WEEKDAY]
    c.now = c.now.replace(hour=14)
    assert schedule(tmp_path, c).due() == []


def test_changing_the_time_days_and_turning_off(tmp_path) -> None:
    c = Clock()
    s = schedule(tmp_path, c)
    said = s.set_time(WEEKDAY, "8:30 am")
    # already gone today: waits for Monday rather than going off now
    assert "at half past eight in the morning" in said and "Monday" in said
    c.forward(minutes=1)
    assert s.due() == []
    s.set_days(PERSONAL, "Saturday and Sunday")
    assert s.briefings[PERSONAL].days == [5, 6]
    s.turn(PERSONAL, False)
    c.forward(days=1)
    assert s.due() == [] and "personal briefing: off" in s.describe()
    again = schedule(tmp_path, c)          # kept in the file
    assert again.briefings[WEEKDAY].time == "08:30" and not again.briefings[PERSONAL].enabled


def test_pause_until_a_day_and_snooze(tmp_path) -> None:
    c = Clock()
    s = schedule(tmp_path, c)
    s.pause(WEEKDAY, date(2026, 11, 17))
    c.forward(minutes=1)
    assert s.due() == []
    with pytest.raises(BriefingError):
        s.pause(WEEKDAY, date(2026, 11, 13))
    s.turn(WEEKDAY, True)
    assert s.due() == [WEEKDAY]
    s.mark_said(WEEKDAY)
    assert "until quarter past nine" in s.snooze(s.pick("", "snooze"), 15)
    c.forward(minutes=14)
    assert s.due() == []
    c.forward(minutes=1)
    assert s.due() == [WEEKDAY]
    s.mark_said(WEEKDAY)
    assert s.due() == []


def test_a_damaged_file_is_set_aside(tmp_path) -> None:
    (tmp_path / "briefings.json").write_text("{not json", encoding="utf-8")
    s = schedule(tmp_path)
    assert s.briefings[WEEKDAY].time == "09:00"
    assert list(tmp_path.glob("briefings.json.unreadable-*"))


@pytest.mark.parametrize("text, days", [
    ("weekdays", [0, 1, 2, 3, 4]), ("Monday to Friday", [0, 1, 2, 3, 4]),
    ("tuesday through thursday", [1, 2, 3]), ("saturday", [5]), ("weekends", [5, 6]),
    ("every day", list(range(7))), ("mon, wed and fri", [0, 2, 4]),
])
def test_days(text, days) -> None:
    assert parse_days(text) == days


def test_picking_a_briefing_by_its_words(tmp_path) -> None:
    s = schedule(tmp_path)
    assert s.pick("the Saturday one") == PERSONAL
    assert s.pick("my work priorities") == WEEKDAY
    with pytest.raises(BriefingError, match="Which briefing"):
        s.pick("")


# --- What's in it and what's said ------------------------------------------------------------

def test_personal_list_is_the_me_note_only() -> None:
    sat = date(2026, 11, 14)
    b = Briefing(sat, sat, "Craig", title="Personal list")
    b.add_personal(ME)
    texts = [it.text for it in b.ranked()]
    assert texts[0] == "Due today: Book the dentist."
    assert "Daily: Clean the kitchen." in texts
    assert any(t.startswith("Weekly: Clean the car") for t in texts)
    assert any(t.startswith("Monthly: Clean the caravan") for t in texts)
    assert "For Caravan: Fix the awning." in texts and "To do: Call Mum." in texts
    joined = " ".join(texts)
    assert "Mow the lawns" not in joined and "hose" not in joined and "not a task" not in joined
    assert "Tyres" not in joined and "tyres" not in joined
    assert b.text().startswith("[Personal list for Craig")


def test_speech_says_how_many_then_the_top_few() -> None:
    b = Briefing(date(2026, 11, 14), date(2026, 11, 14), "Craig")
    b.add_personal(ME)
    said = speech(b, PERSONAL)
    assert said.startswith("[happy] Good morning, Sir. Your personal list for today.")
    assert "There are six things." in said
    assert "First, due today: Book the dentist." in said
    assert "There are 2 more" in said
    empty = Briefing(date(2026, 11, 13), date(2026, 11, 13), "Craig")
    empty.missing.append("the Outlook calendar (not connected here)")
    said = speech(empty, WEEKDAY)
    assert "nothing pressing" in said and "couldn't reach the Outlook calendar," in said


# --- The service and the tool ------------------------------------------------------------------

def obsidian(notes):
    async def remote(op, args):
        assert op == "obsidian_project_notes"
        return json.dumps({"notes": notes, "left_out": 0})
    return remote


def toolbox(tmp_path, clock):
    tasks = TaskStore(tmp_path / "tasks.json", now=clock)
    tasks.add_task("Send the invoice", due_date="today")
    s = schedule(tmp_path, clock)
    terminals = {"craig-pc": obsidian({"Projects/Me.md": ME, "Projects/J.A.R.V.I.S.md": WORK})}
    return ToolBox(None, None, tasks=tasks, obsidian_terminals=lambda: terminals,
                   briefings=s), s


def test_service_says_the_weekday_priorities_and_leaves_a_note(tmp_path) -> None:
    c = Clock()
    tools, s = toolbox(tmp_path, c)
    heard = []

    async def deliver(spoken):
        heard.append(spoken)
        return True

    service = BriefingService(s, tools.gather_briefing, deliver)
    assert asyncio.run(service.check()) == 0
    c.forward(minutes=1)
    assert asyncio.run(service.check()) == 1
    said = heard[0]
    assert said.kind == WEEKDAY and "Your priorities for today" in said.text
    assert "Send the invoice" in said.text and "Order a Pi case" in said.note
    assert "briefing_schedule" in said.note
    assert asyncio.run(service.check()) == 0          # once only


def test_service_waits_for_a_terminal(tmp_path) -> None:
    c = Clock()
    c.forward(minutes=1)
    tools, s = toolbox(tmp_path, c)
    gathered = []

    async def gather(kind):
        gathered.append(kind)
        return Briefing(c.now.date(), c.now.date(), "Craig")

    async def deliver(spoken):  # pragma: no cover - never reached
        raise AssertionError

    service = BriefingService(s, gather, deliver, ready=lambda: False)
    assert asyncio.run(service.check()) == 0 and gathered == []
    assert s.due() == [WEEKDAY]                       # still waiting


def test_saturday_personal_has_no_work(tmp_path) -> None:
    c = Clock()
    c.now = datetime(2026, 11, 14, 9, 0, tzinfo=BRISBANE)
    tools, _ = toolbox(tmp_path, c)
    b = asyncio.run(tools.gather_briefing(PERSONAL))
    text = b.text()
    assert "Book the dentist" in text and "Fix the awning" in text
    assert "Pi case" not in text and "invoice" not in text and "Wire the calendar" not in text


def test_the_voice_tool(tmp_path) -> None:
    c = Clock()
    tools, s = toolbox(tmp_path, c)

    def run(**args):
        return asyncio.run(tools.run("briefing_schedule", args))

    assert "weekdays, Monday to Friday at nine o'clock in the morning" in run(action="status")
    assert "Which briefing" in run(action="time", value="8am")
    assert "seven o'clock" in run(action="time", briefing="saturday", value="7am")
    assert s.briefings[PERSONAL].time == "07:00"
    assert "off" in run(action="off", briefing="personal")
    assert "paused until" in run(action="pause", briefing="work", value="next tuesday")
    assert "Pause it until which day?" in run(action="pause", briefing="work")
    s.last_said = WEEKDAY
    assert "Snoozed the weekday priorities briefing until" in run(action="snooze", value="10")
    assert "Personal list for Craig" in run(action="now", briefing="personal")
    assert "I can't do" in run(action="explode", briefing="work")


def test_tool_is_loose_deferred_and_in_the_persona() -> None:
    tool = next(t for t in REQUEST_TOOLS if t.get("name") == "briefing_schedule")
    assert tool["defer_loading"] and not tool["strict"]
    assert "briefing_schedule" in SYSTEM_PROMPT and "Scheduled briefings" in SYSTEM_PROMPT
    assert tool_schema_problems(REQUEST_TOOLS) == []
