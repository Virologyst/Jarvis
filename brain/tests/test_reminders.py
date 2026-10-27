"""Task reminders: working out when, saying each one once, catching up after a restart, snoozing,
and waiting for a quiet moment on the terminal."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.llm import Conversation  # noqa: E402
from jarvis_brain.persona import SYSTEM_PROMPT  # noqa: E402
from jarvis_brain.reminders import ReminderService  # noqa: E402
from jarvis_brain.tasks import TaskError, TaskStore, parse_duration  # noqa: E402
from jarvis_brain.tools import TOOLS, ToolBox  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    BRISBANE = ZoneInfo("Australia/Brisbane")
except Exception:  # pragma: no cover - no timezone database
    BRISBANE = None

pytestmark = pytest.mark.skipif(BRISBANE is None, reason="no timezone database")


class Clock:
    """A clock the test moves by hand. Starts Tuesday 27 October 2026, 9 am, Brisbane."""

    def __init__(self, hour: int = 9, minute: int = 0):
        self.now = datetime(2026, 10, 27, hour, minute, tzinfo=BRISBANE)

    def __call__(self) -> datetime:
        return self.now

    def to(self, day: int, hour: int, minute: int = 0) -> None:
        self.now = datetime(2026, 10, day, hour, minute, tzinfo=BRISBANE)

    def forward(self, **kw) -> None:
        self.now += timedelta(**kw)


def store(tmp_path: Path, clock: Clock | None = None) -> TaskStore:
    return TaskStore(tmp_path / "tasks.json", now=clock or Clock())


def at(s: TaskStore, task_ref: str, when: str) -> str:
    t = s._locate("craig", task_ref)
    return s.resolve_reminder(t, when).strftime("%Y-%m-%d %H:%M")


# --- Working out when --------------------------------------------------------------------------

@pytest.mark.parametrize("text, minutes", [
    ("15 minutes", 15), ("fifteen minutes", 15), ("twenty five minutes", 25), ("an hour", 60),
    ("half an hour", 30), ("an hour and a half", 90), ("1.5 hours", 90), ("two hours", 120),
    ("a day", 1440), ("a week", 10080), ("10 mins", 10), ("for 5 minutes", 5),
    ("another ten minutes", 10),
])
def test_durations(text, minutes) -> None:
    assert parse_duration(text) == timedelta(minutes=minutes)


@pytest.mark.parametrize("text", ["", "soon", "later on", "zero minutes", "minutes"])
def test_not_durations(text) -> None:
    assert parse_duration(text) is None


@pytest.mark.parametrize("when, expected", [
    ("one hour before", "2026-10-30 16:00"),
    ("30 minutes before", "2026-10-30 16:30"),
    ("an hour and a half before", "2026-10-30 15:30"),
    ("two hours before it's due", "2026-10-30 15:00"),
    ("a day before", "2026-10-29 17:00"),
    ("the day before", "2026-10-29 17:00"),
    ("the morning of", "2026-10-30 08:00"),
    ("the day of", "2026-10-30 08:00"),
    ("that morning", "2026-10-30 08:00"),
    ("the evening before", "2026-10-29 18:00"),
    ("the night before", "2026-10-29 18:00"),
    ("when it's due", "2026-10-30 17:00"),
    ("2026-10-28 09:30", "2026-10-28 09:30"),
    ("2026-10-28T09:30", "2026-10-28 09:30"),
    ("tomorrow 9am", "2026-10-28 09:00"),
    ("friday at 3pm", "2026-10-30 15:00"),
    ("Thursday 14:45", "2026-10-29 14:45"),
    ("tomorrow morning", "2026-10-28 08:00"),
    ("tomorrow", "2026-10-28 09:00"),
    ("15:00", "2026-10-27 15:00"),          # still ahead today
    ("08:00", "2026-10-28 08:00"),          # already gone today -> tomorrow
    ("in 20 minutes", "2026-10-27 09:20"),
    ("remind me at 4pm", "2026-10-27 16:00"),
])
def test_reminder_times(tmp_path, when, expected) -> None:
    s = store(tmp_path)
    s.add_task("Print the 3D files for Dave", due_date="friday", due_time="17:00")
    assert at(s, "1", when) == expected


def test_relative_to_a_date_with_no_time(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Renew the domain", due_date="friday")
    assert at(s, "1", "a day before") == "2026-10-29 09:00"
    assert at(s, "1", "the morning of") == "2026-10-30 08:00"
    err = pytest.raises(TaskError, at, s, "1", "an hour before").value
    assert "needs a time" in str(err)


def test_relative_without_a_deadline_asks_for_a_time(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Tidy the shed")
    assert "no due date" in str(pytest.raises(TaskError, at, s, "1", "the morning of").value)
    assert "no due date" in str(pytest.raises(TaskError, at, s, "1", "an hour before").value)
    assert "couldn't work out" in str(pytest.raises(TaskError, at, s, "1", "whenever").value)


# --- Setting, changing, clearing, showing ------------------------------------------------------

def test_set_reports_the_time_and_lists_show_it(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Print the 3D files for Dave", due_date="today", due_time="17:00")
    out = s.set_reminder("3D files", "an hour before")
    assert "Reminder set for 'Print the 3D files for Dave': today at four o'clock" in out
    assert "reminder 2026-10-27 16:00" in out
    assert "reminder today at four o'clock in the afternoon" in s.list_tasks()
    assert "reminder 2026-10-27 16:00" in s.list_tasks()
    assert "Reminder: today at four o'clock in the afternoon." in s.find_task("3D")
    # set replaces, add keeps both
    assert "instead of today at four" in s.set_reminder("1", "16:30")
    assert [r["at"] for r in s.tasks[0].pending_reminders] == ["2026-10-27T16:30"]
    s.set_reminder("1", "15:00", action="add")
    assert len(s.tasks[0].pending_reminders) == 2
    assert "Reminder cleared" in s.set_reminder("1", action="clear")
    assert "No reminder set." in s.find_task("3D")
    assert "no reminder to clear" in s.set_reminder("1", action="clear")


def test_past_and_unclear_reminders_are_refused(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Call the bank", due_date="today", due_time="09:30")
    assert "already passed" in str(pytest.raises(TaskError, s.set_reminder, "bank",
                                                 "an hour before").value)
    with pytest.raises(TaskError):
        s.set_reminder("bank", "")
    with pytest.raises(TaskError):
        s.set_reminder("bank", "noon", action="nag")
    assert s.tasks[0].reminders == []


def test_add_task_with_a_reminder_or_a_prompt_to_ask(tmp_path) -> None:
    s = store(tmp_path)
    out = s.add_task("Dentist", due_date="tomorrow", due_time="14:00", reminder="the morning of")
    assert "reminder tomorrow at eight o'clock in the morning" in out
    assert "No reminder yet" not in out
    assert "ask if and when they'd like one" in s.add_task("Buy milk")
    s.add_task("Gym", reminder="no")
    assert s.tasks[2].reminders == []
    with pytest.raises(TaskError):   # can't be worked out: nothing added
        s.add_task("Mystery", reminder="an hour before")
    assert len(s.tasks) == 3


def test_moving_the_deadline_mentions_the_reminder(tmp_path) -> None:
    s = store(tmp_path)
    s.add_task("Report", due_date="friday", due_time="17:00", reminder="an hour before")
    assert "its reminder is still" in s.update_task("report", due_date="next monday")


# --- Saying each reminder once -----------------------------------------------------------------

def test_due_once_then_never_again_even_after_a_restart(tmp_path) -> None:
    clock = Clock()
    s = store(tmp_path, clock)
    s.add_task("Print the 3D files for Dave", due_date="today", due_time="17:00",
               reminder="an hour before")
    clock.to(27, 15, 59)
    assert s.due_reminders() == []
    clock.to(27, 16, 0)
    [due] = s.due_reminders()
    assert due.text == ("[neutral] Sir, a reminder: print the 3D files for Dave is due at five "
                        "o'clock this afternoon.")
    assert "[task 1]" in due.note and "snooze" in due.note
    s.mark_reminded(due.task_id, due.at)
    assert s.due_reminders() == []
    clock.forward(minutes=5)
    assert store(tmp_path, clock).due_reminders() == []           # restarted: still said
    assert "reminder" not in s.list_tasks()                        # nothing more to come


def test_whose_task_it_is_when_not_craigs(tmp_path) -> None:
    clock = Clock()
    s = store(tmp_path, clock)
    s.add_person("Sam")
    s.add_task("Fix the printer", "Sam", "tomorrow", "10:00", reminder="the morning of")
    clock.to(28, 8, 0)
    [due] = s.due_reminders()
    assert due.text == ("[neutral] Sir, a reminder for Sam: fix the printer is due at ten "
                        "o'clock this morning.")


def test_done_deleted_and_on_hold_tasks_stay_quiet(tmp_path) -> None:
    clock = Clock()
    s = store(tmp_path, clock)
    for title in ("Done thing", "Deleted thing", "Held thing"):
        s.add_task(title, due_date="today", due_time="17:00", reminder="16:00")
    s.update_task("1", status="done")
    s.delete_task("2")
    s.update_task("3", status="on hold")
    clock.to(27, 16, 0)
    assert s.due_reminders() == []
    s.update_task("3", status="open")       # back on the list before it's too late: said
    assert [d.task_id for d in s.due_reminders()] == [3]


def test_missed_while_off_is_caught_up_unless_too_late(tmp_path) -> None:
    clock = Clock()
    s = store(tmp_path, clock)
    s.add_task("Meeting", due_date="today", due_time="11:00", reminder="10:00")
    s.add_task("Invoice", due_date="today", due_time="11:00", reminder="10:00")
    s.add_task("Water plants", reminder="10:00")           # no deadline
    s.add_task("Book flights", reminder="10:00")           # no deadline

    clock.to(27, 10, 40)                                   # Jarvis comes back: all still useful
    again = store(tmp_path, clock)
    assert [d.task_id for d in again.due_reminders()] == [1, 2, 3, 4]
    again.mark_reminded(1, "2026-10-27T10:00")
    again.mark_reminded(3, "2026-10-27T10:00")

    clock.to(27, 22, 30)                                   # off again until late that night
    late = store(tmp_path, clock)
    assert late.due_reminders() == []    # invoice: an hour past due; flights: twelve hours late
    assert all(r["skipped"] for t in late.tasks[1::2] for r in t.reminders)
    assert store(tmp_path, clock).due_reminders() == []    # skipping is saved too


def test_a_later_reminder_overtakes_an_earlier_one(tmp_path) -> None:
    clock = Clock()
    s = store(tmp_path, clock)
    s.add_task("Report", due_date="tomorrow", due_time="17:00", reminder="the day before")
    s.set_reminder("report", "the morning of", action="add")
    clock.to(28, 9, 0)                                     # both fell due while off
    [due] = s.due_reminders()
    assert due.at == "2026-10-28T08:00"
    assert sum(bool(r["skipped"]) for r in s.tasks[0].reminders) == 1


# --- Snoozing ----------------------------------------------------------------------------------

def test_snooze_the_reminder_just_given(tmp_path) -> None:
    clock = Clock()
    s = store(tmp_path, clock)
    s.add_task("Call Mum", due_date="today", due_time="18:00", reminder="17:00")
    with pytest.raises(TaskError):                         # nothing reminded about yet
        s.set_reminder("", "15 minutes", action="snooze")
    clock.to(27, 17, 0)
    [due] = s.due_reminders()
    s.mark_reminded(due.task_id, due.at)
    out = s.set_reminder("", "fifteen minutes", action="snooze")
    assert "Snoozed for 'Call Mum': today at quarter past five" in out
    clock.to(27, 17, 14)
    assert s.due_reminders() == []
    clock.to(27, 17, 15)
    [again] = s.due_reminders()
    assert again.task_id == 1 and again.at == "2026-10-27T17:15"
    with pytest.raises(TaskError):
        s.set_reminder("", "", action="snooze")


# --- The background service --------------------------------------------------------------------

def test_service_waits_for_a_listener_then_says_it_once(tmp_path) -> None:
    clock = Clock()
    s = store(tmp_path, clock)
    s.add_task("Stand-up", due_date="today", due_time="10:00", reminder="09:50")
    heard: list = []
    listening = False

    async def deliver(due):
        if listening:
            heard.append(due.text)
        return listening

    service = ReminderService(s, deliver, every_s=0)
    clock.to(27, 9, 50)
    assert asyncio.run(service.check()) == 0              # no terminal connected: kept
    listening = True
    clock.forward(seconds=30)
    assert asyncio.run(service.check()) == 1
    assert asyncio.run(service.check()) == 0
    assert len(heard) == 1 and "stand-up is due at ten o'clock this morning" in heard[0]


def test_service_run_keeps_going_after_a_failure(tmp_path) -> None:
    clock = Clock(9, 50)
    s = store(tmp_path, clock)
    calls = []

    async def deliver(due):
        return True

    service = ReminderService(s, deliver, every_s=0)

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk hiccup")
        if len(calls) == 3:
            raise asyncio.CancelledError
        return []

    s.due_reminders = flaky
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.run())
    assert len(calls) == 3


# --- As a tool, and in the persona -------------------------------------------------------------

def test_reminder_tool_is_offered_and_explained() -> None:
    tools = {t["name"]: t for t in TOOLS}
    assert "set_task_reminder" in tools and "set_task_reminder" in SYSTEM_PROMPT
    props = tools["set_task_reminder"]["input_schema"]["properties"]
    assert set(props["action"]["enum"]) == {"set", "add", "clear", "snooze"}
    assert "reminder" in tools["add_task"]["input_schema"]["properties"]
    assert "when they'd like to be reminded" in SYSTEM_PROMPT
    assert "don't ask again" in SYSTEM_PROMPT


def test_reminder_tool_runs_without_cards(tmp_path) -> None:
    async def no_card(card):
        raise AssertionError("reminders are Jarvis's own notebook: no cards")

    box = ToolBox(upgrades=None, show_card=no_card, tasks=store(tmp_path))
    run = lambda tool, **args: asyncio.run(box.run(tool, args))  # noqa: E731
    assert "reminder 2026-10-27 16:00" in run(
        "add_task", title="Ship it", person="", due_date="today", due_time="17:00", notes="",
        reminder="an hour before")
    out = run("set_task_reminder", task="ship", person="", action="set", when="16:30")
    assert "half past four" in out
    assert "already passed" in run("set_task_reminder", task="1", person="", action="set",
                                   when="today 08:00")
    assert "Reminder cleared" in run("set_task_reminder", task="1", person="", action="clear",
                                     when="")


# --- So a reply to a reminder makes sense ------------------------------------------------------

def test_conversation_records_the_reminder_in_order() -> None:
    conv = Conversation(client=None, model="m", effort="low", idle_reset_minutes=10)
    conv.add_aside("(Nobody said this...)", "[neutral] Sir, a reminder: lunch.")
    assert [m["role"] for m in conv.messages] == ["user", "assistant"]
    conv.messages.append({"role": "user", "content": "half-way through a turn"})
    conv.add_aside("(again)", "[neutral] Sir, another.")
    assert len(conv.messages) == 3                          # never breaks the order
    conv.messages.pop()
    conv.last_turn -= 3600                                  # long idle: starts fresh
    conv.add_aside("(later)", "[neutral] Sir, later.")
    assert [m["content"] for m in conv.messages] == ["(later)", "[neutral] Sir, later."]


# --- On a terminal: wait until quiet, then speak and listen ------------------------------------

def test_session_waits_until_quiet_then_speaks_with_a_follow_up(monkeypatch) -> None:
    server = pytest.importorskip("jarvis_brain.server")
    from jarvis_protocol import AudioStart, decode
    from jarvis_brain.tasks import DueReminder

    class FakeWS:
        closed = False

        def __init__(self):
            self.sent = []

        async def send_str(self, raw):
            self.sent.append(decode(raw))

        async def send_bytes(self, data):
            pass

    tts = NS(sample_rate=16000, synthesize=lambda text, voice, speed: b"\0" * 3200)
    brain = NS(settings=NS(model="m", effort="low", idle_reset_minutes=10), client=None,
               upgrades=None, tasks=None, tts=tts)
    monkeypatch.setattr(server, "REMINDER_GAP_S", 0.0)

    async def go():
        ws = FakeWS()
        session = server.TerminalSession(brain, ws)
        session.listening = True                            # someone is talking
        session.queue_reminder(DueReminder(1, "2026-10-27T16:00",
                                           "[neutral] Sir, a reminder: tea.", "(note)"))
        await asyncio.sleep(0.7)
        assert not any(isinstance(m, AudioStart) for m in ws.sent)
        session.listening = False
        await asyncio.wait_for(session._reminding, 5)
        starts = [m for m in ws.sent if isinstance(m, AudioStart)]
        assert len(starts) == 1 and starts[0].follow_up is True
        assert session.conversation.messages[-1]["content"] == "[neutral] Sir, a reminder: tea."
        assert session.audio_until > 0

    asyncio.run(go())
