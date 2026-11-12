"""The coder: Claude Code jobs in Sir's projects, against a fake Claude Code (no API calls)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sdk = pytest.importorskip("claude_agent_sdk")
from jarvis_protocol import CoderEvent  # noqa: E402
from jarvis_terminal.coder import Coder  # noqa: E402


class FakeClaude:
    """Plays a script: each step is a tool call (which may need permission) or the result."""

    def __init__(self, options, script, seen):
        self.options, self.script, self.seen = options, script, seen

    async def connect(self):
        self.seen["options"] = self.options

    async def query(self, prompt):
        self.seen.setdefault("prompts", []).append(prompt)

    async def receive_response(self):
        for step in self.script.pop(0):
            if step[0] == "tool":
                _, name, data = step
                yield sdk.AssistantMessage([sdk.ToolUseBlock("t", name, data)], "m")
                verdict = await self.options.can_use_tool(name, data, sdk.ToolPermissionContext())
                self.seen.setdefault("verdicts", []).append(
                    (name, data.get("command"), verdict.behavior, getattr(verdict, "message", "")))
            elif step[0] == "wait":
                await asyncio.sleep(step[1])
            else:
                yield sdk.ResultMessage("success", 1, 1, False, 1, "sess-1", result=step[1],
                                        total_cost_usd=0.01)

    async def interrupt(self):
        self.seen["interrupted"] = True

    async def disconnect(self):
        self.seen["disconnected"] = True


def make(tmp_path, script):
    pyc, web = tmp_path / "PycharmProjects", tmp_path / "WebstormProjects"
    for p in (pyc / "Jarvis", pyc / "Huntsman-Payload", web / "API", web / "portal"):
        p.mkdir(parents=True)
    sent, seen = [], {}

    async def send(msg):
        sent.append(msg)

    coder = Coder(send, roots=[pyc, web], no_git=[web / "API"], state=tmp_path / "coder.json",
                  client_factory=lambda options: FakeClaude(options, script, seen))
    return coder, sent, seen, pyc, web


async def until(cond, timeout=3.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


def test_job_runs_safe_steps_freely_and_reports(tmp_path):
    async def go():
        coder, sent, seen, pyc, _ = make(tmp_path, [[
            ("tool", "Bash", {"command": "python -m pytest -q"}),
            ("tool", "Read", {"file_path": str(tmp_path / "PycharmProjects/Jarvis/x.py")}),
            ("result", "Added the button. Tests pass.")]])
        ok, text = await coder.handle("coder_start", {"project": "jarvis", "task": "add a button"})
        assert ok and text.startswith("Started the coding job in Jarvis.")
        await until(lambda: coder.job.status == "done")
        assert [v[2] for v in seen["verdicts"]] == ["allow", "allow"]
        assert seen["options"].cwd == str(pyc / "Jarvis")
        assert seen["options"].permission_mode == "acceptEdits"
        assert "read aloud" in seen["options"].system_prompt["append"]
        done = sent[-1]
        assert isinstance(done, CoderEvent) and done.kind == "done"
        assert done.text == "Added the button. Tests pass." and done.data["project"] == "Jarvis"
        status = (await coder.handle("coder_status", {}))[1]
        assert "finished" in status and "Its report: Added the button." in status
    asyncio.run(go())


def test_risky_steps_wait_for_sir(tmp_path):
    async def go():
        coder, sent, seen, _, _ = make(tmp_path, [[
            ("tool", "Bash", {"command": "npm install express"}),
            ("tool", "Bash", {"command": "rm -rf build && git push"}),
            ("result", "Done.")]])
        await coder.handle("coder_start", {"project": "portal", "task": "add express"})
        await until(lambda: any(isinstance(m, CoderEvent) and m.kind == "permission" for m in sent))
        ask = sent[0]
        assert ask.text == "run: npm install express" and ask.data["detail"] == "npm install express"
        assert coder.job.status == "waiting"
        assert "waiting for your approval" in (await coder.handle("coder_status", {}))[1]
        coder.decide(ask.request_id, True)
        await until(lambda: len([m for m in sent if m.kind == "permission"]) == 2)
        second = [m for m in sent if m.kind == "permission"][1]
        coder.decide(second.request_id, False)
        await until(lambda: coder.job.status == "done")
        assert [v[2] for v in seen["verdicts"]] == ["allow", "deny"]
        assert "Sir said no" in seen["verdicts"][1][3]
    asyncio.run(go())


def test_no_git_in_the_api_project(tmp_path):
    async def go():
        coder, sent, seen, _, web = make(tmp_path, [[
            ("tool", "Bash", {"command": "git status"}),
            ("tool", "Bash", {"command": "cd x; git commit -m y"}),
            ("result", "Ready to commit.")]])
        await coder.handle("coder_start", {"project": "API", "task": "tidy"})
        await until(lambda: coder.job.status == "done")
        assert [v[2] for v in seen["verdicts"]] == ["deny", "deny"]
        assert "Sir handles git here" in seen["verdicts"][0][3]
        assert "Never run git in this folder" in seen["options"].system_prompt["append"]
        assert "Bash(git:*)" in seen["options"].disallowed_tools
        assert not [m for m in sent if m.kind == "permission"]          # refused, not asked
    asyncio.run(go())


def test_new_project_from_scratch(tmp_path):
    async def go():
        coder, sent, seen, pyc, web = make(tmp_path, [[("result", "Made a timer app.")],
                                                      [("result", "Made a dashboard.")]])
        ok, text = await coder.handle("coder_start", {"project": "Pomodoro timer", "new": "true",
                                                      "task": "a pomodoro timer"})
        assert ok and text.startswith(f"Made {pyc / 'Pomodoro-timer'} and started")
        assert (pyc / "Pomodoro-timer").is_dir()
        await until(lambda: coder.job.status == "done")
        assert "brand new, empty project folder" in seen["options"].system_prompt["append"]
        ok, text = await coder.handle("coder_start", {"project": "dash", "new": "true",
                                                      "where": "WebstormProjects", "task": "x"})
        assert ok and (web / "dash").is_dir()
        await until(lambda: coder.job.status == "done")
        ok, text = await coder.handle("coder_start", {"project": "portal", "new": "true",
                                                      "where": "WebstormProjects", "task": "x"})
        assert not ok and "already exists" in text
    asyncio.run(go())


def test_one_job_at_a_time_follow_ups_and_stop(tmp_path):
    async def go():
        coder, sent, seen, _, _ = make(tmp_path, [
            [("wait", 0.2), ("result", "First pass.")],
            [("result", "Made it blue too.")],
            [("result", "Now with tests.")],
            [("wait", 5), ("result", "never")]])
        await coder.handle("coder_start", {"project": "Jarvis", "task": "button"})
        ok, text = await coder.handle("coder_start", {"project": "portal", "task": "x"})
        assert not ok and "still working on Jarvis" in text
        assert (await coder.handle("coder_reply", {"message": "make it blue"}))[1].startswith(
            "Passed on")
        await until(lambda: coder.job.status == "done")
        assert seen["prompts"][-1] == "Also, from Sir: make it blue"
        assert coder.job.summary == "Made it blue too."
        # A follow-up after it's done carries on the same session, even after a restart
        fresh = Coder(coder.send, roots=coder.roots, no_git=coder.no_git, state=coder.state,
                      client_factory=coder._client_factory)
        assert (await fresh.handle("coder_reply", {"message": "add tests"}))[1] == \
            "Carrying on in Jarvis."
        await until(lambda: fresh.job.status == "done")
        assert seen["options"].resume == "sess-1"
        await fresh.handle("coder_reply", {"message": "slow one"})
        await until(lambda: "slow one" in seen["prompts"])
        assert (await fresh.handle("coder_stop", {}))[1].startswith("Stopped the job in Jarvis")
        await until(lambda: fresh.job.status == "stopped")
        assert seen["interrupted"]
    asyncio.run(go())


def test_finding_projects(tmp_path):
    async def go():
        coder, *_ = make(tmp_path, [])
        assert "PycharmProjects: Huntsman-Payload, Jarvis" in (await coder.handle(
            "coder_projects", {}))[1]
        ok, text = await coder.handle("coder_start", {"project": "huntsman payload", "task": "x"})
        assert ok
        ok, text = await coder.handle("coder_status", {})
        assert "Huntsman-Payload" in text
        await coder.handle("coder_stop", {})
        ok, text = await Coder(coder.send, roots=coder.roots, state=tmp_path / "other.json",
                               client_factory=coder._client_factory).handle(
            "coder_start", {"project": "nothing-here", "task": "x"})
        assert not ok and "There's no project called nothing-here" in text
        ok, text = await coder.handle("coder_start", {"project": "C:\\Windows", "task": "x"})
        assert not ok and "only work in the project folders" in text
    asyncio.run(go())


def test_brain_away_then_back(tmp_path):
    async def go():
        coder, sent, seen, _, _ = make(tmp_path, [[("result", "All done.")]])

        async def offline(msg):
            raise ConnectionError("not connected")

        coder.send = offline
        await coder.handle("coder_start", {"project": "Jarvis", "task": "x"})
        await until(lambda: coder.job.status == "done")
        await until(lambda: coder._unsent)
        got = []

        async def online(msg):
            got.append(msg)

        coder.send = online
        await coder.reconnected()
        assert got and got[0].kind == "done" and got[0].text == "All done."
    asyncio.run(go())
