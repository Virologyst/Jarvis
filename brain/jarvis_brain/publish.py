"""Get Jarvis's own commits (self-upgrades, roll-backs) onto GitHub.

Self-upgrades are committed on the brain PC (selfupgrade.py) and the brain restarts to run them,
so on every start-up the brain pushes, through a git remote with write access (JARVIS_GIT_REMOTE,
default "github": an SSH deploy key on the brain PC, docs/self-upgrade.md):

- HEAD to jarvis/brain-pc, always (forced): a mirror of what the brain is running, so nothing
  ever lives only on the brain PC;
- HEAD to eye, only as a fast-forward: when GitHub's eye has work the brain hasn't got yet (or a
  roll-back moved the brain back), this is refused and the mirror branch is the copy to merge.

A brain PC with no such remote just skips this, quietly. Pushing never changes the brain's code.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from .selfupgrade import git

log = logging.getLogger(__name__)

MIRROR = "jarvis/brain-pc"
BRANCH = "eye"
START_DELAY_S = 20.0       # let the brain finish starting first
PUSH_TIMEOUT_S = 90.0


async def publish(repo: Path, remote: str | None = None, run=None) -> str:
    """Push the brain's HEAD; returns what happened, for the log."""
    remote = remote or os.environ.get("JARVIS_GIT_REMOTE", "github")
    run = run or git
    rc, _ = await run(repo, "remote", "get-url", remote)
    if rc:
        return f"not publishing: no '{remote}' remote with write access set up"
    rc, head = await run(repo, "rev-parse", "--short", "HEAD")
    head = head.strip()
    rc, out = await run(repo, "push", "--force", remote, f"HEAD:refs/heads/{MIRROR}",
                        timeout=PUSH_TIMEOUT_S)
    if rc:
        return f"couldn't push to GitHub: {' '.join(out.split())[:300]}"
    rc, out = await run(repo, "push", remote, f"HEAD:refs/heads/{BRANCH}", timeout=PUSH_TIMEOUT_S)
    if rc:
        why = "it has newer work" if "rejected" in out or "fetch first" in out else \
            " ".join(out.split())[:200]
        return (f"pushed {head} to {MIRROR}; not to {BRANCH} ({why}): merge {MIRROR} into "
                f"{BRANCH} on the dev PC")
    return f"pushed {head} to {BRANCH} and {MIRROR}"


async def publish_later(repo: Path, delay: float = START_DELAY_S) -> None:
    """At start-up, in the background: wait a little, then publish, logging the result."""
    try:
        await asyncio.sleep(delay)
        log.info("github: %s", await publish(repo))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - publishing must never upset the brain
        log.exception("github: publishing failed")
