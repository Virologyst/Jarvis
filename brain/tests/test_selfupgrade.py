"""Self-upgrade guard rails, end to end with a fake Claude Code in a throwaway git repo.

Protected: an upgrade may not change this file (see selfupgrade.PROTECTED).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.approvals import ApprovalGate  # noqa: E402
from jarvis_brain.selfupgrade import (  # noqa: E402
    ALLOWED_TOOLS, DISALLOWED_TOOLS, HEALTH_MARKER, MAX_STRICT_OPTIONAL, UpgradeManager,
    _parse_claude_output, is_protected, tool_schema_problems,
)

# Stands in for "load the upgraded brain's tools": prints FAKE_TOOLS (JSON), default none
FAKE_TOOL_CHECK = [sys.executable, "-c", "import os; print(os.environ.get('FAKE_TOOLS', '[]'))"]

FAKE_CLAUDE = r'''
import json, os, pathlib, sys
prompt = sys.stdin.read()
args = sys.argv[1:]
assert "-p" in args and "acceptEdits" in args, args
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "ok":
    pathlib.Path("feature.txt").write_text("a new ability\n")
elif mode == "protected":
    p = pathlib.Path("brain/jarvis_brain/approvals.py")
    p.write_text(p.read_text() + "\nAUTO_APPROVE = True\n")
elif mode == "nothing":
    pass
pathlib.Path(os.environ["FAKE_NOTE"]).write_text(str("ANTHROPIC_API_KEY" in os.environ))
print(json.dumps({"type": "result", "is_error": False, "result": "I added a new ability."}))
'''


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo,
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = tmp_path / "Jarvis"
    (repo / "brain" / "jarvis_brain").mkdir(parents=True)
    (repo / "brain" / "jarvis_brain" / "approvals.py").write_text("# the gate\n")
    (repo / "README.md").write_text("jarvis\n")
    (repo / ".gitignore").write_text("data/\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-api_key")
    monkeypatch.setenv("FAKE_NOTE", str(tmp_path / "saw-api-key.txt"))

    said: list[tuple[str, object]] = []
    restarts: list[int] = []

    async def announce(text, card):
        said.append((text, card))

    def boot() -> tuple[ApprovalGate, UpgradeManager]:
        """A brain starting up: the same order as server.Brain (manager, then restore)."""
        gate = ApprovalGate(store=repo / "data" / "approvals.json")
        mgr = UpgradeManager(repo, gate, announce, claude_cmd=[sys.executable, str(fake)],
                             checks=[[sys.executable, "-c", "print('tests ok')"]],
                             restart=lambda: restarts.append(1), tool_check=FAKE_TOOL_CHECK)
        gate.restore()
        return gate, mgr

    gate, mgr = boot()
    mgr.reboot = boot   # tests call this to simulate restarting the brain
    return repo, gate, mgr, said, restarts


async def wait_done(mgr: UpgradeManager) -> None:
    """Wait for the background job, including its report and clean-up."""
    assert mgr.worker, "the job never started"
    await asyncio.wait_for(mgr.worker, 60)


def test_full_upgrade_needs_two_approvals_then_undo(setup) -> None:
    repo, gate, mgr, said, restarts = setup
    base = git(repo, "rev-parse", "HEAD")

    async def flow():
        text, card = mgr.request("Add a new ability")
        assert card and card.kind == "approval" and not (repo / "feature.txt").exists()
        await gate.decide(card.card_id, "approve")
        await wait_done(mgr)
        assert mgr.job.status == "ready", mgr.job
        assert mgr.job.tests_ok and mgr.job.files == ["feature.txt"]
        # Ready, but nothing live has changed until the second approval
        assert git(repo, "rev-parse", "HEAD") == base and not (repo / "feature.txt").exists()
        apply_card = said[-1][1]
        assert apply_card.kind == "approval" and "I added a new ability" in apply_card.body
        result = await gate.decide(apply_card.card_id, "approve")
        assert "applied" in result.lower()
        assert (repo / "feature.txt").exists() and restarts == [1]
        assert (repo / "data" / "upgrades" / HEALTH_MARKER).exists()   # on probation
        # Claude Code must not get Jarvis's API key (it uses the Max login)
        assert (repo.parent / "saw-api-key.txt").read_text() == "False"

        text, undo = mgr.request_undo()
        assert undo
        await gate.decide(undo.card_id, "approve")
        assert git(repo, "rev-parse", "HEAD") == base and not (repo / "feature.txt").exists()

    asyncio.run(flow())


def test_rejecting_the_start_does_nothing(setup) -> None:
    repo, gate, mgr, said, _ = setup

    async def flow():
        _, card = mgr.request("Add a new ability")
        await gate.decide(card.card_id, "reject")
        await asyncio.sleep(0.3)
        assert not (repo.parent / "jarvis-upgrades").exists()

    asyncio.run(flow())


def test_touching_a_protected_file_is_blocked(setup, monkeypatch) -> None:
    repo, gate, mgr, said, restarts = setup
    monkeypatch.setenv("FAKE_MODE", "protected")

    async def flow():
        _, card = mgr.request("Make approvals automatic")
        await gate.decide(card.card_id, "approve")
        await wait_done(mgr)
        assert mgr.job.status == "blocked", mgr.job
        assert "approvals.py" in mgr.job.summary
        # Only an info card: there is no way to apply it
        assert said[-1][1].kind == "info" and not gate.pending()
        assert "AUTO_APPROVE" not in (repo / "brain/jarvis_brain/approvals.py").read_text()
        assert restarts == []

    asyncio.run(flow())


def test_no_changes_means_failed(setup, monkeypatch) -> None:
    _, gate, mgr, said, _ = setup
    monkeypatch.setenv("FAKE_MODE", "nothing")

    async def flow():
        _, card = mgr.request("Do something")
        await gate.decide(card.card_id, "approve")
        await wait_done(mgr)
        assert mgr.job.status == "failed" and not gate.pending()

    asyncio.run(flow())


def test_one_job_at_a_time(setup) -> None:
    _, _, mgr, _, _ = setup
    _, first = mgr.request("First thing")
    text, second = mgr.request("Second thing")
    assert first and second is None and "already" in text


def test_protected_list_covers_the_guard_rails() -> None:
    for p in ["brain/jarvis_brain/approvals.py", "brain/jarvis_brain/selfupgrade.py",
              "brain/jarvis_brain/supervisor.py", "brain/tests/test_selfupgrade.py", ".env",
              ".env.local", ".git/config", ".claude/settings.json"]:
        assert is_protected(p), p
    for p in ["brain/jarvis_brain/tools.py", "terminal/jarvis_terminal/client.py", "ui/index.html"]:
        assert not is_protected(p), p


def test_claude_code_is_boxed_in() -> None:
    assert not any(t.startswith(("WebFetch", "WebSearch")) for t in ALLOWED_TOOLS)
    for t in ["WebFetch", "WebSearch", "Bash(git push:*)", "Bash(git commit:*)"]:
        assert t in DISALLOWED_TOOLS
    assert all(t in ("Read", "Edit", "Write", "Glob", "Grep") or t.startswith(
        ("Bash(python -m pytest", "Bash(git status", "Bash(git diff", "Bash(git log")) for t in ALLOWED_TOOLS)


def test_parse_claude_output() -> None:
    out = "some log line\n" + json.dumps({"type": "result", "is_error": False, "result": "Done."})
    assert _parse_claude_output(out)["result"] == "Done."
    assert _parse_claude_output("not json")["is_error"] is True


# ---- surviving a brain restart ---------------------------------------------

def test_start_card_survives_a_restart(setup) -> None:
    repo, _, mgr, said, _ = setup

    async def flow():
        _, card = mgr.request("Add a new ability")
        gate2, mgr2 = mgr.reboot()                 # brain restarts before anyone approves
        assert [c.card_id for c in gate2.pending()] == [card.card_id]
        assert "waiting for your approval" in (await mgr2.resume())
        await gate2.decide(card.card_id, "approve")
        await wait_done(mgr2)
        assert mgr2.job.status == "ready" and said[-1][1].kind == "approval"

    asyncio.run(flow())


def test_apply_card_survives_a_restart(setup) -> None:
    repo, gate, mgr, said, restarts = setup

    async def flow():
        _, card = mgr.request("Add a new ability")
        await gate.decide(card.card_id, "approve")
        await wait_done(mgr)
        apply_card = said[-1][1]
        gate2, mgr2 = mgr.reboot()
        assert [c.card_id for c in gate2.pending()] == [apply_card.card_id]
        await mgr2.resume()
        assert [c.card_id for c in gate2.pending()] == [apply_card.card_id]   # not duplicated
        assert "applied" in (await gate2.decide(apply_card.card_id, "approve")).lower()
        assert (repo / "feature.txt").exists() and restarts == [1]

    asyncio.run(flow())


def test_work_cut_short_by_a_restart_starts_again(setup) -> None:
    repo, gate, mgr, said, _ = setup

    async def flow():
        _, card = mgr.request("Add a new ability")
        # Approved and working when the brain died: what current.json says after a crash
        mgr.job.status, mgr.job.attempts, mgr.job.started = "working", 1, 1.0
        mgr._save(mgr.job)
        gate.withdraw(card.card_id)
        _, mgr2 = mgr.reboot()
        note = await mgr2.resume()
        assert "started that work again" in note
        await wait_done(mgr2)
        assert mgr2.job.status == "ready" and mgr2.job.attempts == 2
        assert mgr2.job.branch.endswith("-r2")      # fresh names for the retry

    asyncio.run(flow())


def test_retries_after_restarts_are_limited(setup) -> None:
    _, gate, mgr, _, _ = setup

    async def flow():
        _, card = mgr.request("Add a new ability")
        mgr.job.status, mgr.job.attempts = "working", 2
        mgr._save(mgr.job)
        gate.withdraw(card.card_id)
        _, mgr2 = mgr.reboot()
        assert "stopped" in await mgr2.resume()
        assert mgr2.job.status == "failed" and mgr2.worker is None

    asyncio.run(flow())


def test_a_rejected_upgrade_doesnt_block_the_next_one(setup) -> None:
    _, gate, mgr, _, _ = setup

    async def flow():
        _, card = mgr.request("First thing")
        await gate.decide(card.card_id, "reject")
        text, second = mgr.request("Second thing")
        assert second, text
        await gate.decide(second.card_id, "reject")
        _, mgr2 = mgr.reboot()                     # also after a restart
        assert await mgr2.resume() is None
        assert mgr2.request("Third thing")[1]

    asyncio.run(flow())


def test_saved_cards_hold_no_code_and_unknown_kinds_are_dropped(tmp_path) -> None:
    store = tmp_path / "approvals.json"

    async def act():
        return "done"

    gate = ApprovalGate(store=store)
    gate.register("thing", lambda card: act)
    gate.propose("Do a thing", "", act, data={"x": 1}, kind="thing")
    gate.propose("Memory only", "", act)            # no kind: never saved
    saved = json.loads(store.read_text())
    assert [e["card"]["title"] for e in saved] == ["Do a thing"]

    fresh = ApprovalGate(store=store)                # nobody registered "thing"
    assert fresh.restore() == 0 and not fresh.pending()
    assert json.loads(store.read_text()) == []       # and it's gone from disk

    gate2 = ApprovalGate(store=store)
    gate2.propose("Stale", "", act, kind="thing")
    gate3 = ApprovalGate(store=store)
    gate3.register("thing", lambda card: None)       # owner says it no longer applies
    assert gate3.restore() == 0


# ---- tool definitions the API would refuse ----------------------------------

def _tool(name: str, optional: int) -> dict:
    props = {f"p{i}": {"type": "string"} for i in range(optional)}
    return {"name": name, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": []}}


def test_tool_schema_limits() -> None:
    assert tool_schema_problems([_tool("a", 12), _tool("b", 12)]) == []
    over = tool_schema_problems([_tool("a", 13), _tool("b", 13)])   # 26: what broke Jarvis
    assert over and "26 optional" in over[0]
    assert tool_schema_problems([_tool("a", 0), _tool("a", 0)])     # duplicate names
    loose = dict(_tool("c", 30), strict=False)                       # only strict tools count
    assert tool_schema_problems([loose]) == []
    twenty = [_tool(f"t{i}", 0) for i in range(20)]
    assert tool_schema_problems(twenty) == []
    too_many = tool_schema_problems(twenty + [_tool("t20", 0)])      # 25 broke Jarvis
    assert too_many and "21 tools are marked strict" in too_many[0]
    assert tool_schema_problems(twenty + [dict(_tool("t20", 0), strict=False)]) == []


def test_strict_schemas_that_are_too_big_together() -> None:
    from jarvis_brain.selfupgrade import MAX_STRICT_PROPERTIES
    many = [{"name": f"t{i}", "strict": True, "input_schema": {
        "type": "object", "properties": {f"p{j}": {"type": "string"} for j in range(5)},
        "required": [f"p{j}" for j in range(5)]}} for i in range(7)]          # 35 settings
    problems = tool_schema_problems(many)
    assert problems and "35 settings" in problems[0] and "too complex" in problems[0]
    assert tool_schema_problems(many[:MAX_STRICT_PROPERTIES // 5]) == []


def test_jarvis_leaves_room_for_new_strict_tools() -> None:
    from jarvis_brain.selfupgrade import MAX_STRICT_PROPERTIES, MAX_STRICT_TOOLS
    from jarvis_brain.tools import TOOLS
    strict = [t for t in TOOLS if t.get("strict")]
    settings = sum(len(t["input_schema"]["properties"]) for t in strict)
    assert len(strict) <= MAX_STRICT_TOOLS - 4, [t["name"] for t in strict]
    assert settings <= MAX_STRICT_PROPERTIES - 8, settings


def test_jarvis_own_tools_fit_the_api_limits() -> None:
    from jarvis_brain.tools import TOOLS
    assert tool_schema_problems(TOOLS) == []


def test_upgrade_that_breaks_the_tools_is_blocked(setup, monkeypatch) -> None:
    repo, gate, mgr, said, restarts = setup
    monkeypatch.setenv("FAKE_TOOLS", json.dumps([_tool("a", MAX_STRICT_OPTIONAL + 2)]))

    async def flow():
        _, card = mgr.request("Add a to-do list")
        await gate.decide(card.card_id, "approve")
        await wait_done(mgr)
        assert mgr.job.status == "blocked", mgr.job
        assert "optional settings" in mgr.job.summary
        assert said[-1][1].kind == "info" and not gate.pending()   # nothing to approve
        assert not (repo / "feature.txt").exists() and restarts == []

    asyncio.run(flow())


def test_upgrade_whose_tools_wont_load_is_blocked(setup, monkeypatch) -> None:
    _, gate, mgr, _, _ = setup
    monkeypatch.setenv("FAKE_TOOLS", "not json")

    async def flow():
        _, card = mgr.request("Add a to-do list")
        await gate.decide(card.card_id, "approve")
        await wait_done(mgr)
        assert mgr.job.status == "blocked" and "couldn't load" in mgr.job.summary

    asyncio.run(flow())
