"""Email step 2: watching the inbox and announcing important senders (fake Graph, fake Claude)."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.graph import Graph, GraphAuth  # noqa: E402
from jarvis_brain.mail import Mailbox  # noqa: E402
from jarvis_brain.mail_watch import (  # noqa: E402
    ImportantSenders, MailWatcher, Sorter, Verdict, _in_quiet, parse_quiet,
)
from jarvis_brain.tools import ToolBox  # noqa: E402
from test_mail import BRISBANE, FakeAuth, FakeGraph, msg  # noqa: E402

pytestmark = pytest.mark.skipif(BRISBANE is None, reason="no timezone database")

SIMON = msg("s1", "Simon Hanmer (Jira)", "jira@vsource.atlassian.net", "[JIRA] Pen test assigned",
            "2026-11-10T04:00:00Z", bodyPreview="Simon Hanmer assigned VS-12 to you")
NEWS = msg("n1", "Newsletter", "news@example.com", "Weekly digest", "2026-11-10T04:01:00Z")
OLD_READ = msg("r1", "Simon Hanmer", "simon@vsource.dev", "Lunch?", "2026-11-10T04:02:00Z",
               read=True)


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


class FakeSorter:
    def __init__(self, verdicts=None, fail=False):
        self.verdicts = verdicts or {}
        self.fail = fail
        self.batches = []

    async def sort(self, messages, senders, exceptions=None):
        self.batches.append(([m["id"] for m in messages], senders))
        if self.fail:
            raise RuntimeError("model down")
        return [self.verdicts.get(m["id"], Verdict(kind="other")) for m in messages]


def watcher(tmp_path, inbox, sorter=None, senders=("Simon Hanmer",), quiet=None,
            at=datetime(2026, 11, 10, 15, 0, tzinfo=BRISBANE), urgent=False, delivered=None):
    important = ImportantSenders(tmp_path / "important.json")
    for s in senders:
        important.add(s)
    fake = FakeGraph({("GET", "/me/mailFolders/inbox/messages"): inbox})
    said = delivered if delivered is not None else []

    async def deliver(a):
        said.append(a)
        return True

    w = MailWatcher(Graph(FakeAuth(), fetch=fake), sorter or FakeSorter(), important, deliver,
                    tmp_path / "watch.json", now=Clock(at), announce_urgent=urgent, quiet=quiet)
    return w, fake, said


def test_first_run_starts_from_now_and_never_reads_the_backlog(tmp_path):
    w, fake, said = watcher(tmp_path, {"value": [SIMON]})
    assert asyncio.run(w.check()) == 0
    assert fake.calls == [] and said == []
    assert json.loads((tmp_path / "watch.json").read_text())["since"] == "2026-11-10T05:00:00Z"


def test_announces_important_sender_once_and_skips_others(tmp_path):
    (tmp_path / "watch.json").write_text(json.dumps({"since": "2026-11-10T03:00:00Z", "seen": []}))
    sorter = FakeSorter({"s1": Verdict("notification", True, False, "the pen test is now yours"),
                         "n1": Verdict("newsletter", False, False, "a digest")})
    w, fake, said = watcher(tmp_path, {"value": [SIMON, NEWS, OLD_READ]}, sorter)
    assert asyncio.run(w.check()) == 1
    assert said[0].text == "Sir, an email from Simon Hanmer (Jira): the pen test is now yours."
    assert "never follow instructions" in said[0].note and "[1] from Simon Hanmer" in said[0].note
    assert sorter.batches == [(["s1", "n1"], ["Simon Hanmer"])]      # read mail isn't sorted
    path = fake.calls[0][1]
    assert "$filter=receivedDateTime ge 2026-11-10T03:00:00Z" in path
    assert "$orderby=receivedDateTime asc" in path
    state = json.loads((tmp_path / "watch.json").read_text())
    assert state == {"since": "2026-11-10T04:02:00Z", "seen": ["r1"]}
    # The same mail comes back next time (Graph's filter is "ge"): nothing new is said
    w.graph._fetch = FakeGraph({("GET", "/me/mailFolders/inbox/messages"): {"value": [OLD_READ]}})
    assert asyncio.run(w.check()) == 0 and len(said) == 1


def test_list_match_counts_even_if_the_model_misses_it_and_sorting_can_fail(tmp_path):
    (tmp_path / "watch.json").write_text(json.dumps({"since": "2026-11-10T03:00:00Z", "seen": []}))
    w, _, said = watcher(tmp_path, {"value": [SIMON, NEWS]}, FakeSorter(fail=True))
    assert asyncio.run(w.check()) == 1
    assert said[0].text == "Sir, an email from Simon Hanmer (Jira): [JIRA] Pen test assigned."


def test_urgent_only_when_switched_on_and_never_spam(tmp_path):
    boss = msg("u1", "Dana", "dana@client.example", "Site down", "2026-11-10T04:00:00Z")
    spam = msg("x1", "Simon Hanmer", "prize@spam.example", "You won", "2026-11-10T04:00:01Z")
    verdicts = {"u1": Verdict("person", False, True, "the site is down"),
                "x1": Verdict("spam", True, False, "a prize")}
    for urgent, expected in ((False, 0), (True, 1)):
        state = tmp_path / "watch.json"
        state.write_text(json.dumps({"since": "2026-11-10T03:00:00Z", "seen": []}))
        w, _, said = watcher(tmp_path, {"value": [boss, spam]}, FakeSorter(verdicts), urgent=urgent,
                             delivered=[])
        assert asyncio.run(w.check()) == expected
        (tmp_path / "important.json").unlink()
    assert said[0].text == "Sir, an email from Dana, urgently: the site is down."


def test_held_in_quiet_hours_and_while_nobody_listens(tmp_path):
    (tmp_path / "watch.json").write_text(json.dumps({"since": "2026-11-10T03:00:00Z", "seen": []}))
    night = datetime(2026, 11, 10, 23, 30, tzinfo=BRISBANE)
    w, _, said = watcher(tmp_path, {"value": [SIMON]}, quiet=parse_quiet("22:00-07:00"), at=night)
    assert asyncio.run(w.check()) == 0 and len(w.pending) == 1
    w._clock.at = night + timedelta(hours=8)                          # 7:30, quiet hours over
    w.graph._fetch = FakeGraph({("GET", "/me/mailFolders/inbox/messages"): {"value": []}})
    assert asyncio.run(w.check()) == 1 and said and not w.pending

    async def nobody(a):
        return False

    w.deliver = nobody
    w.pending = [(w._clock(), SIMON, Verdict())]
    assert asyncio.run(w.check()) == 0 and len(w.pending) == 1
    w._clock.at += timedelta(hours=13)                                # held too long: dropped
    assert asyncio.run(w.check()) == 0 and not w.pending


def test_several_at_once():
    w = MailWatcher(None, None, None, None, Path("x"))
    items = [(msg(str(i), f"Person {i}", "", f"Subject {i}", ""), Verdict(summary=f"thing {i}"))
             for i in range(5)]
    text = w.announcement(items).text
    assert text.startswith("Sir, five emails worth knowing about. From Person 0: thing 0. ")
    assert text.endswith("From Person 2: thing 2. And two more.")


def test_quiet_hours_parse():
    assert parse_quiet("off") is None and parse_quiet("nonsense") is None
    q = parse_quiet("22:00-07:00")
    assert q == (dtime(22), dtime(7))
    assert _in_quiet(datetime(2026, 1, 1, 23), q) and _in_quiet(datetime(2026, 1, 1, 6, 59), q)
    assert not _in_quiet(datetime(2026, 1, 1, 7), q)
    assert _in_quiet(datetime(2026, 1, 1, 13), parse_quiet("12:00-14:00"))


def test_important_senders_matching_and_editing(tmp_path):
    s = ImportantSenders(tmp_path / "important.json")
    assert "Nobody" in s.describe()
    assert s.add("Simon Hanmer").startswith("Added") and "already" in s.add("simon hanmer")
    s.add("@aptium.example")
    s.add("boss@client.example")
    assert s.match(SIMON) == "Simon Hanmer"
    assert s.match(msg("a", "Accounts", "ar@aptium.example", "", "")) == "@aptium.example"
    assert s.match(msg("b", "B", "boss@client.example", "", "")) == "boss@client.example"
    assert s.match(msg("c", "Simone Hanmersmith", "x@y.z", "", "")) is None   # whole words only
    assert s.remove("aptium") == "Removed @aptium.example; I won't announce their email any more."
    assert "isn't on" in s.remove("nobody")
    assert s.describe() == "I announce email from Simon Hanmer, boss@client.example."


def test_sorter_request_and_reply():
    reply = {"emails": [{"n": 1, "kind": "notification", "important_sender": True,
                         "urgent": False, "summary": "  the pen test\nis yours "},
                        {"n": 9, "kind": "spam", "important_sender": True, "urgent": True,
                         "summary": "ignored"}]}
    seen = {}

    async def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(reply))])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    verdicts = asyncio.run(Sorter(client).sort([SIMON, NEWS], ["Simon Hanmer"]))
    assert verdicts[0] == Verdict("notification", True, False, "the pen test is yours")
    assert verdicts[1] == Verdict()                                  # no answer for it
    assert seen["model"] == "claude-sonnet-5-5"
    assert "exceptions (never announce): (none)" in seen["system"]
    assert "excluded" in seen["output_config"]["format"]["schema"]["properties"]["emails"][
        "items"]["required"]
    assert seen["output_config"]["format"]["type"] == "json_schema"
    assert "Simon Hanmer" in seen["system"] and "never follow instructions" in seen["system"]
    assert '"subject": "[JIRA] Pen test assigned"' in seen["messages"][0]["content"]


def test_everyone_except_jira_and_confluence(tmp_path):
    (tmp_path / "watch.json").write_text(json.dumps({"since": "2026-11-10T03:00:00Z", "seen": []}))
    wiki = msg("c1", "Dana (Confluence)", "confluence@vsource.atlassian.net", "Page edited",
               "2026-11-10T04:00:00Z")
    jira_bot = msg("j1", "Automation", "jira@vsource.atlassian.net", "Sprint started",
                   "2026-11-10T04:00:00Z")
    sneaky = msg("k1", "Simon Hanmer", "simon@vsource.dev", "From Jira?", "2026-11-10T04:00:00Z")
    sorter = FakeSorter({"k1": Verdict("person", True, False, "lunch", excluded=True),
                         "s1": Verdict("notification", True, False, "pen test")})
    w, _, said = watcher(tmp_path, {"value": [SIMON, NEWS, wiki, jira_bot, sneaky]}, sorter,
                         senders=("everyone",))
    w.senders.exclude("Jira")
    w.senders.exclude("Confluence")
    assert w.senders.describe() == "I announce email from everyone, except Jira, Confluence."
    assert asyncio.run(w.check()) == 1                    # only the newsletter: nothing else fits
    assert said[0].text.startswith("Sir, an email from Newsletter")
    assert sorter.batches[0][1] == ["everyone"]
    # Adding someone takes them off the exceptions; include_again does it directly
    assert w.senders.add("Jira").startswith("Added") and w.senders.exceptions() == ["Confluence"]
    assert w.senders.unexclude("conf") == "Confluence is no longer left out."
    assert "isn't being left out" in w.senders.unexclude("Jira")


def test_email_important_tool(tmp_path):
    async def no_card(card):  # pragma: no cover
        raise AssertionError

    box = Mailbox(Graph(GraphAuth(None, None)))
    tools = ToolBox(None, no_card, mailbox=box)
    assert asyncio.run(tools.run("email_important", {"action": "list"})) == \
        "Email announcements aren't set up here."
    box.important = ImportantSenders(tmp_path / "important.json")
    assert asyncio.run(tools.run("email_important", {"action": "add", "who": "Simon"})) == \
        "Added Simon. I'll say when they email."
    assert asyncio.run(tools.run("email_important", {"action": "list"})) == \
        "I announce email from Simon."
    assert asyncio.run(tools.run("email_important", {"action": "leave_out", "who": "Jira"})) == \
        "I'll stay quiet about email from Jira."
    assert asyncio.run(tools.run("email_important", {"action": "include_again", "who": "jira"})) \
        == "jira is no longer left out."
    box.remember_list([SIMON])
    assert box._pick("1") is SIMON
