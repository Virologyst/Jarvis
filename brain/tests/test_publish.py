"""Getting the brain's own commits onto GitHub (against a fake git)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.publish import publish  # noqa: E402
from jarvis_brain.selfupgrade import is_protected  # noqa: E402


def fake_git(answers):
    calls = []

    async def run(repo, *args, timeout=120):
        calls.append(args)
        for prefix, reply in answers:
            if args[:len(prefix)] == prefix:
                return reply
        return 0, ""
    return run, calls


def test_pushes_the_mirror_and_eye():
    run, calls = fake_git([(("rev-parse",), (0, "abc1234\n"))])
    assert asyncio.run(publish(Path("."), "github", run)) == "pushed abc1234 to eye and jarvis/brain-pc"
    assert ("push", "--force", "github", "HEAD:refs/heads/jarvis/brain-pc") in calls
    assert ("push", "github", "HEAD:refs/heads/eye") in calls             # never forced


def test_eye_refused_keeps_the_mirror():
    run, _ = fake_git([(("rev-parse",), (0, "abc1234\n")),
                       (("push", "github", "HEAD:refs/heads/eye"),
                        (1, " ! [rejected] HEAD -> eye (fetch first)"))])
    assert asyncio.run(publish(Path("."), "github", run)) == (
        "pushed abc1234 to jarvis/brain-pc; not to eye (it has newer work): merge "
        "jarvis/brain-pc into eye on the dev PC")


def test_no_remote_or_no_access():
    run, calls = fake_git([(("remote", "get-url"), (2, "error: No such remote"))])
    assert "no 'github' remote" in asyncio.run(publish(Path("."), "github", run))
    assert not any(c[0] == "push" for c in calls)
    run, _ = fake_git([(("push", "--force"), (128, "Permission denied (publickey).\nfatal"))])
    assert asyncio.run(publish(Path("."), "github", run)).startswith(
        "couldn't push to GitHub: Permission denied (publickey).")


def test_upgrades_cant_change_what_gets_pushed():
    assert is_protected("brain/jarvis_brain/publish.py")
