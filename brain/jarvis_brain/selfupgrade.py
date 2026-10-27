"""Self-upgrade: Jarvis asks Claude Code to change Jarvis's own code, with a human in charge.

Flow (every arrow that changes anything needs an approval tap):
  request(task) --approve--> Claude Code works in a separate git worktree on its own branch
      -> Jarvis commits, checks protected files, runs the tests itself
      -> "ready" card --approve--> fast-forward the live checkout, restart via the supervisor
      -> the supervisor rolls back automatically if the new version doesn't come up healthy
  request_undo() --approve--> reset the live checkout to before the last upgrade, restart

Guard rails, enforced here in code (not by asking the model nicely):
- Claude Code never touches the live checkout; it works in ../jarvis-upgrades/<id>.
- Its tools are limited: file edits in its folder, tests, read-only git. No web, no push, no commit.
- PROTECTED files (the approval gate, this module, the supervisor, their tests, settings)
  can't be changed by an upgrade: any diff touching them is blocked, not offered.
- One job at a time, with a time limit. Everything is logged under data/upgrades/<id>/.

Restarts: the current job is saved to data/upgrades/current.json at every step, and its cards
are saved by the approval gate. After a restart, a job waiting for an approval gets its card
back; one that was mid-work (its start was already approved) stops any leftover Claude Code
process and starts the work again, at most MAX_ATTEMPTS times in all. Nothing is ever applied
without the second approval.

This file is protected: an upgrade can never loosen its own rules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jarvis_protocol import Card

from .approvals import Action, ApprovalGate

log = logging.getLogger(__name__)

# Paths an upgrade may not change. Exact files, or folders/prefixes ending in "/".
PROTECTED = (
    "brain/jarvis_brain/approvals.py",
    "brain/jarvis_brain/selfupgrade.py",
    "brain/jarvis_brain/supervisor.py",
    "brain/tests/test_selfupgrade.py",
    ".env",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    ".claude/",
    ".git/",
)

RESTART_EXIT_CODE = 75          # tells the supervisor "restart me" (see supervisor.py)
HEALTH_MARKER = "pending_health.json"
HISTORY = "history.json"
ROLLBACK_NOTE = "rolled_back.json"
CURRENT = "current.json"        # the latest job, saved at every step so a restart can pick it up
MAX_ATTEMPTS = 2                # an approved job cut short by restarts is started at most this often

# Card kinds the approval gate saves across restarts; this module rebuilds their actions
START, APPLY, UNDO = "upgrade.start", "upgrade.apply", "upgrade.undo"
ACTIVE = ("awaiting approval", "working", "testing", "ready")

ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(python -m pytest:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
]
DISALLOWED_TOOLS = [
    "WebFetch", "WebSearch",
    "Bash(git push:*)", "Bash(git commit:*)", "Bash(git reset:*)", "Bash(git checkout:*)",
    "Bash(git worktree:*)", "Bash(rm:*)", "Bash(curl:*)", "Bash(pip:*)", "Bash(uv:*)",
]

PROMPT = """\
You are improving Jarvis, the office voice assistant, at its owner's request. The owner asked by
voice, so the wording may be loose; do what they clearly meant, and keep the change focused.

The task:
{task}

This folder is a separate git worktree of the Jarvis repository, on its own branch. Rules:
- Read README.md and docs/architecture.md first to learn how Jarvis is put together.
- Work only inside this folder. Do not commit, push or change git history; the caller does that.
- Do NOT modify these protected files or folders: {protected}.
  If the task can't be done without changing them, change nothing and explain why at the end.
- Never add a way around the approval gate: anything with side effects outside Jarvis's own
  reply (sending, deleting, buying, running commands) must go through ApprovalGate.propose().
- No new network services, no telemetry, no secrets in code.
- Add or update tests for what you change, and make sure this passes:
  python -m pytest terminal/tests brain/tests -q
- Finish with two or three plain spoken sentences summarising what you changed, written to be
  read aloud by Jarvis (no markdown, no file paths, no code).
"""


def is_protected(path: str) -> bool:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    for rule in PROTECTED:
        if rule.endswith("/"):
            # the folder itself anywhere in the tree (e.g. a nested .git/ or .claude/)
            if p.startswith(rule) or ("/" + rule) in ("/" + p):
                return True
        elif p.lower() == rule.lower():
            return True
    return p.startswith(".env")   # .env, .env.local, ...


def protected_changes(paths: list[str]) -> list[str]:
    return [p for p in paths if is_protected(p)]


# The API refuses a request whose strict tools have more optional parameters than this in
# total (400 "Schemas contains too many optional parameters"). Every conversation would fail,
# while /health still looks fine, so an upgrade that crosses it is blocked, not offered.
MAX_STRICT_OPTIONAL = 24
# Prints the upgraded brain's tool list as JSON, run inside the upgrade's worktree
DUMP_TOOLS = "import json, jarvis_brain.tools as t; print(json.dumps(t.TOOLS))"


def tool_schema_problems(tools: list[dict]) -> list[str]:
    """Reasons the API would reject these tool definitions outright (empty list = fine)."""
    problems = []
    optional = 0
    for tool in tools:
        if not tool.get("strict"):
            continue
        schema = tool.get("input_schema") or {}
        required = set(schema.get("required") or [])
        optional += sum(1 for p in (schema.get("properties") or {}) if p not in required)
    if optional > MAX_STRICT_OPTIONAL:
        problems.append(f"the tools have {optional} optional settings and the API allows "
                        f"{MAX_STRICT_OPTIONAL}, so every conversation would fail")
    names = [t.get("name") for t in tools]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        problems.append("two tools share a name: " + ", ".join(map(str, dupes)))
    return problems


def find_claude() -> list[str] | None:
    """Command to run Claude Code: JARVIS_CLAUDE_CODE, then PATH, then the native install."""
    env = os.environ.get("JARVIS_CLAUDE_CODE")
    if env:
        return [env]
    found = shutil.which("claude")
    if found:
        return [found]
    native = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    return [str(native)] if native.exists() else None


@dataclass
class Job:
    id: str
    task: str
    # awaiting approval -> working -> testing -> ready -> applied
    # (or rejected / blocked / failed along the way)
    status: str = "awaiting approval"
    branch: str = ""
    worktree: str = ""
    base: str = ""
    summary: str = ""
    files: list[str] = field(default_factory=list)
    diffstat: str = ""
    tests_ok: bool = False
    started: float = 0.0
    finished: float = 0.0
    attempts: int = 0       # times the work has been started (a restart mid-work starts it again)
    pid: int = 0            # Claude Code's process, so a restart can stop a leftover one

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


async def run(cmd: list[str], cwd: Path, env: dict | None = None, timeout: float = 600,
              stdin: str | None = None,
              on_start: Callable[[int], None] | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    if on_start:
        on_start(proc.pid)
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, f"timed out after {timeout:.0f} s"
    return proc.returncode, out.decode(errors="replace")


async def git(repo: Path, *args: str, timeout: float = 120) -> tuple[int, str]:
    return await run(["git", "-c", "user.name=Jarvis", "-c", "user.email=jarvis@localhost",
                      *args], repo, timeout=timeout)


def stop_leftover_claude(pid: int) -> bool:
    """Stop a Claude Code process left running by a brain that was restarted mid-upgrade.

    Only kills it if that PID still belongs to Claude Code (PIDs get reused). True if killed.
    """
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=15).stdout.lower()
            if "claude" not in out and "node" not in out:
                return False
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").lower()
            if "claude" not in cmdline:
                return False
            os.kill(pid, 15)
    except (OSError, subprocess.SubprocessError):
        return False
    log.info("stopped leftover Claude Code process %d", pid)
    return True


Announce = Callable[[str, Card | None], Awaitable[None]]


class UpgradeManager:
    def __init__(self, repo: Path, gate: ApprovalGate, announce: Announce,
                 claude_cmd: list[str] | None = None, checks: list[list[str]] | None = None,
                 python: str = sys.executable, timeout_s: float | None = None,
                 restart: Callable[[], None] | None = None,
                 tool_check: list[str] | None = None):
        self.repo = repo
        # Prints the upgraded tool list as JSON (see tool_schema_problems)
        self.tool_check = tool_check or [python, "-c", DUMP_TOOLS]
        self.gate = gate
        self.announce = announce
        self.claude_cmd = claude_cmd
        self.python = python
        # Run by Jarvis itself after Claude Code finishes; all must pass for "tests pass"
        # (Only the brain is import-checked: the brain PC doesn't have the terminal's audio libs.)
        self.checks = checks or [
            [python, "-m", "pytest", "terminal/tests", "brain/tests", "-q"],
            [python, "-c", "import jarvis_brain.server, jarvis_brain.tools"],
        ]
        self.timeout_s = timeout_s or float(os.environ.get("JARVIS_UPGRADE_TIMEOUT_MIN", "30")) * 60
        self.restart = restart or _restart_via_supervisor
        self.work_root = repo.parent / "jarvis-upgrades"
        self.log_root = repo / "data" / "upgrades"
        self.worker: asyncio.Task | None = None   # the running job (kept so it isn't garbage collected)

        # Pick up where a previous run left off (see resume()), and let the gate rebuild our cards
        saved = self._read(CURRENT, None)
        self.job: Job | None = Job.from_dict(saved) if isinstance(saved, dict) and saved.get("id") else None
        gate.register(START, self._rebuild_start)
        gate.register(APPLY, self._rebuild_apply)
        gate.register(UNDO, self._rebuild_undo)

    # ---- what the model's tools call ------------------------------------

    def request(self, task: str) -> tuple[str, Card | None]:
        task = task.strip()
        if not task:
            return "No task given.", None
        if self._busy():
            return (f"Another upgrade is already {self.job.status}: {self.job.task}. "
                    "It has to be applied or rejected first."), None
        job = Job(id=uuid.uuid4().hex[:8], task=task)
        self.job = job
        self._save(job)
        card = self._propose_start(job)
        return "An approval card to start the upgrade is on the screen.", card

    def status(self) -> str:
        j = self.job
        if not j:
            return "No upgrade has been requested yet."
        self._busy()   # notices a card that was rejected or expired
        mins = ((j.finished or time.time()) - j.started) / 60 if j.started else 0
        extra = f" It has been running for {mins:.0f} minutes." if j.status in ("working", "testing") else ""
        waiting = {"awaiting approval": " Its start card is waiting for your approval.",
                   "ready": " The result is waiting for your approval."}.get(j.status, "")
        return f"Upgrade '{j.task}' is {j.status}.{extra}{waiting} {j.summary}".strip()

    def request_undo(self) -> tuple[str, Card | None]:
        history = self._read(HISTORY, [])
        if not history:
            return "There's no applied upgrade to undo.", None
        last = history[-1]
        card = self.gate.propose(
            title="Undo the last upgrade",
            body=f"Roll back '{last['task']}' and restart Jarvis.",
            action=lambda: self._undo(last),
            data={"upgrade_id": last["id"], "step": "undo"},
            kind=UNDO)
        return "An approval card to undo the last upgrade is on the screen.", card

    # ---- after a restart --------------------------------------------------

    async def resume(self) -> str | None:
        """Carry on with a job a restart interrupted. Call once at start-up, after gate.restore().

        Returns something to tell the user, or None if there was nothing to pick up.
        """
        job = self.job
        if not job or job.status not in ACTIVE:
            return None
        if job.status in ("working", "testing"):
            stop_leftover_claude(job.pid)
            await self._cleanup(job)
            if job.attempts >= MAX_ATTEMPTS:
                job.status, job.finished = "failed", time.time()
                job.summary = (f"It was interrupted by a restart {job.attempts} times, "
                               "so I've stopped trying.")
                self._save(job)
                return f"the upgrade '{job.task}' kept getting interrupted, so I've stopped it."
            log.info("upgrade %s was interrupted by a restart; starting the work again", job.id)
            self._begin(job)
            return (f"I was restarted in the middle of the upgrade '{job.task}', "
                    "so I've started that work again.")
        if job.status == "ready":
            rc, _ = await git(self.repo, "rev-parse", "--verify", "--quiet", job.branch or "-")
            if rc:
                job.status, job.summary = "failed", "Its work went missing during a restart."
                self._save(job)
                return f"the upgrade '{job.task}' lost its work in a restart. Please ask for it again."
            if not self._card_for(job, "apply"):
                self._propose_apply(job)
            return f"the upgrade '{job.task}' is still waiting for your approval."
        # awaiting approval: the gate brought its card back, or it's gone (rejected / expired)
        if self._busy():
            return f"the upgrade '{job.task}' is still waiting for your approval to start."
        return None

    def _rebuild_start(self, card: Card) -> Action | None:
        job = self.job
        if job and job.id == card.data.get("upgrade_id") and job.status == "awaiting approval":
            return lambda: self._start(job)
        return None

    def _rebuild_apply(self, card: Card) -> Action | None:
        job = self.job
        if job and job.id == card.data.get("upgrade_id") and job.status == "ready":
            return lambda: self._apply(job)
        return None

    def _rebuild_undo(self, card: Card) -> Action | None:
        for entry in self._read(HISTORY, []):
            if entry.get("id") == card.data.get("upgrade_id"):
                return lambda: self._undo(entry)
        return None

    def _card_for(self, job: Job, step: str) -> Card | None:
        for card in self.gate.pending():
            if card.data.get("upgrade_id") == job.id and card.data.get("step") == step:
                return card
        return None

    def _busy(self) -> bool:
        """Is a job in progress? A job waiting on a card that was rejected or expired isn't."""
        job = self.job
        if not job or job.status not in ACTIVE:
            return False
        step = {"awaiting approval": "start", "ready": "apply"}.get(job.status)
        if step and not self._card_for(job, step):
            job.status = "rejected"
            job.finished = job.finished or time.time()
            self._save(job)
            if step == "apply" and job.worktree and Path(job.worktree).exists():
                try:
                    asyncio.get_running_loop().create_task(self._cleanup(job))
                except RuntimeError:
                    pass   # no event loop (tests): the worktree is tidied by the next job instead
            return False
        return True

    # ---- the work -------------------------------------------------------

    def _propose_start(self, job: Job) -> Card:
        return self.gate.propose(
            title="Start a self-upgrade",
            body=f"Let Claude Code work on Jarvis's own code:\n\n{job.task}\n\n"
                 "It works on a separate copy. Nothing changes until you approve the result.",
            action=lambda: self._start(job),
            data={"upgrade_id": job.id, "step": "start"},
            kind=START)

    def _propose_apply(self, job: Job) -> Card:
        tests = "All tests pass." if job.tests_ok else "WARNING: the tests FAILED."
        files = ", ".join(job.files[:8]) + (" and more" if len(job.files) > 8 else "")
        return self.gate.propose(
            title="Apply the self-upgrade",
            body=f"{job.task}\n\n{job.summary}\n\nChanged: {files}\n{job.diffstat}\n{tests}\n\n"
                 f"Full diff: data/upgrades/{job.id}/diff.patch on the brain PC.",
            action=lambda: self._apply(job),
            data={"upgrade_id": job.id, "step": "apply", "tests_ok": job.tests_ok},
            kind=APPLY)

    async def _start(self, job: Job) -> str:
        if not (self.claude_cmd or find_claude()):
            job.status = "failed"
            self._save(job)
            return "I can't find Claude Code on this machine, so I can't upgrade myself yet."
        self._begin(job)
        return "Right away, Sir. I'll let you know when it's ready for your review."

    def _begin(self, job: Job) -> None:
        job.status, job.started, job.finished, job.pid = "working", time.time(), 0.0, 0
        job.attempts += 1
        self._save(job)
        self.worker = asyncio.create_task(self._work(job))

    async def _work(self, job: Job) -> None:
        logs = self.log_root / job.id
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "request.txt").write_text(job.task, encoding="utf-8")
        try:
            await self._do_work(job, logs)
        except Exception as e:  # noqa: BLE001 - report, never crash the brain
            log.exception("upgrade %s crashed", job.id)
            job.status, job.summary = "failed", f"Something went wrong: {e}"
        job.finished, job.pid = time.time(), 0
        self._save(job)
        await self._report(job)

    async def _do_work(self, job: Job, logs: Path) -> None:
        rc, base = await git(self.repo, "rev-parse", "HEAD")
        if rc:
            raise RuntimeError(base)
        job.base = base.strip()
        # A retry after a restart gets fresh names, in case the old ones couldn't be cleared
        suffix = "" if job.attempts <= 1 else f"-r{job.attempts}"
        job.branch = f"jarvis/upgrade-{job.id}{suffix}"
        wt = self.work_root / f"{job.id}{suffix}"
        job.worktree = str(wt)
        self._save(job)
        self.work_root.mkdir(parents=True, exist_ok=True)
        rc, out = await git(self.repo, "worktree", "add", "-b", job.branch, str(wt), job.base)
        if rc:
            raise RuntimeError(out)

        env = self._env(wt)
        cmd = (self.claude_cmd or find_claude()) + [
            "-p", "--output-format", "json", "--permission-mode", "acceptEdits",
            "--allowedTools", *ALLOWED_TOOLS, "--disallowedTools", *DISALLOWED_TOOLS]
        prompt = PROMPT.format(task=job.task, protected=", ".join(PROTECTED))
        log.info("upgrade %s: Claude Code working in %s", job.id, wt)

        def started(pid: int) -> None:
            job.pid = pid
            self._save(job)

        rc, out = await run(cmd, wt, env=env, timeout=self.timeout_s, stdin=prompt, on_start=started)
        job.pid = 0
        (logs / "claude-code.json").write_text(out, encoding="utf-8")
        result = _parse_claude_output(out)
        job.summary = result.get("result", "").strip() or "Claude Code didn't leave a summary."
        if rc != 0 or result.get("is_error"):
            job.status = "failed"
            return

        await git(wt, "add", "-A")
        rc, _ = await git(wt, "diff", "--cached", "--quiet")
        if rc == 0:
            job.status, job.summary = "failed", "Claude Code finished without changing anything. " + job.summary
            return
        await git(wt, "commit", "-q", "-m", f"Jarvis self-upgrade: {job.task[:60]}\n\n{job.task}")
        _, names = await git(wt, "diff", "--name-only", job.base, "HEAD")
        job.files = [n for n in names.splitlines() if n.strip()]
        _, stat = await git(wt, "diff", "--stat", job.base, "HEAD")
        job.diffstat = stat.strip().splitlines()[-1].strip() if stat.strip() else ""
        _, patch = await git(wt, "diff", job.base, "HEAD")
        (logs / "diff.patch").write_text(patch, encoding="utf-8")

        blocked = protected_changes(job.files)
        if blocked:
            job.status = "blocked"
            job.summary = ("The change touched protected files (" + ", ".join(blocked) +
                           "), so I won't offer to apply it.")
            return

        job.status = "testing"
        self._save(job)
        job.tests_ok, report = True, []
        for check in self.checks:
            rc, out = await run(check, wt, env=env, timeout=900)
            job.tests_ok &= rc == 0
            report.append(f"$ {' '.join(check)}\n(exit {rc})\n{out}")
        (logs / "tests.txt").write_text("\n\n".join(report), encoding="utf-8")

        # Tests can pass while the API would still refuse the new tools on every request
        rc, out = await run(self.tool_check, wt, env=env, timeout=120)
        try:
            tools = json.loads(out.strip().splitlines()[-1]) if rc == 0 and out.strip() else None
        except ValueError:
            tools = None
        problems = (tool_schema_problems(tools) if isinstance(tools, list)
                    else ["I couldn't load the upgraded tool list to check it"])
        if problems:
            job.status = "blocked"
            job.summary = ("The change would break me: " + "; ".join(problems) +
                           ". So I won't offer to apply it. " + job.summary)
            return
        job.status = "ready"

    def _env(self, wt: Path) -> dict:
        env = dict(os.environ)
        # Claude Code should use the Max subscription login, not Jarvis's API key
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(k, None)
        # Tests and imports must see the worktree's code, not the live install
        env["PYTHONPATH"] = os.pathsep.join(str(wt / p) for p in ("brain", "terminal", "protocol"))
        env["PATH"] = str(Path(self.python).parent) + os.pathsep + env.get("PATH", "")
        return env

    async def _report(self, job: Job) -> None:
        if job.status == "ready":
            card = self._propose_apply(job)
            spoken = "Sir, the upgrade is ready for your review." + (
                "" if job.tests_ok else " Though I should mention the tests are failing.")
            await self.announce(spoken, card)
        else:
            await self.announce(f"Sir, the upgrade didn't work out. {job.summary}",
                                Card(card_id=uuid.uuid4().hex, kind="info",
                                     title=f"Upgrade {job.status}", body=f"{job.task}\n\n{job.summary}"))
            await self._cleanup(job)

    async def _apply(self, job: Job) -> str:
        rc, dirty = await git(self.repo, "status", "--porcelain", "--untracked-files=no")
        if rc or dirty.strip():
            return "My live code has uncommitted changes, so I won't apply the upgrade over them."
        rc, head = await git(self.repo, "rev-parse", "HEAD")
        if head.strip() != job.base:
            return "My code has changed since the upgrade started. Please ask for it again."
        rc, out = await git(self.repo, "merge", "--ff-only", job.branch)
        if rc:
            return f"I couldn't apply it: {out.strip()[:200]}"
        if any(f.endswith("pyproject.toml") for f in job.files):
            ok = await self._reinstall()
            if not ok:
                await git(self.repo, "reset", "--hard", job.base)
                return "The upgrade needs new packages and installing them failed, so I've put things back."
        job.status = "applied"
        self._save(job)
        history = self._read(HISTORY, [])
        history.append({"id": job.id, "task": job.task, "prev": job.base, "branch": job.branch,
                        "at": time.time()})
        self._write(HISTORY, history)
        self._write(HEALTH_MARKER, {"id": job.id, "prev": job.base, "at": time.time()})
        await self._cleanup(job)
        terminals = [f for f in job.files if f.startswith(("terminal/", "ui/", "protocol/"))]
        note = " The screens will need updating too." if terminals else ""
        self.restart()
        return f"Upgrade applied. Restarting now, Sir.{note}"

    async def _undo(self, entry: dict) -> str:
        rc, dirty = await git(self.repo, "status", "--porcelain", "--untracked-files=no")
        if rc or dirty.strip():
            return "My live code has uncommitted changes, so I won't roll back over them."
        rc, out = await git(self.repo, "reset", "--hard", entry["prev"])
        if rc:
            return f"I couldn't roll back: {out.strip()[:200]}"
        history = self._read(HISTORY, [])
        self._write(HISTORY, [h for h in history if h["id"] != entry["id"]])
        self.restart()
        return "Rolled back. Restarting now, Sir."

    async def _reinstall(self) -> bool:
        uv = shutil.which("uv")
        if not uv:
            return False
        rc, out = await run([uv, "pip", "install", "--python", self.python, "-q",
                             "-e", "protocol", "-e", "brain[gpu]"], self.repo, timeout=900)
        log.info("reinstall after upgrade: rc=%s %s", rc, out[-500:])
        return rc == 0

    async def _cleanup(self, job: Job) -> None:
        if job.worktree and Path(job.worktree).exists():
            await git(self.repo, "worktree", "remove", "--force", job.worktree)
        await git(self.repo, "worktree", "prune")

    # ---- small state files ----------------------------------------------

    def _save(self, job: Job) -> None:
        """Remember the job's progress (current.json) and keep a copy with its logs."""
        if job is not self.job:
            return
        self._write(CURRENT, asdict(job))
        logs = self.log_root / job.id
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "job.json").write_text(json.dumps(asdict(job), indent=2), encoding="utf-8")

    def _read(self, name: str, default):
        p = self.log_root / name
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _write(self, name: str, value) -> None:
        self.log_root.mkdir(parents=True, exist_ok=True)
        tmp = self.log_root / (name + ".tmp")
        tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(tmp, self.log_root / name)


def _parse_claude_output(out: str) -> dict:
    """Claude Code's --output-format json result (the last JSON object in the output)."""
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    try:
        return json.loads(out)
    except ValueError:
        return {"is_error": True, "result": out.strip()[-500:]}


def _restart_via_supervisor() -> None:
    """Exit so the supervisor restarts us on the new code (after the reply has been spoken)."""
    if os.environ.get("JARVIS_SUPERVISED") != "1":
        log.warning("not running under the supervisor: restart the brain by hand to load the upgrade")
        return
    asyncio.get_running_loop().call_later(12, os._exit, RESTART_EXIT_CODE)


def take_rollback_note(repo: Path) -> str | None:
    """If the supervisor rolled back a broken upgrade, say so once."""
    note = repo / "data" / "upgrades" / ROLLBACK_NOTE
    if not note.exists():
        return None
    info = json.loads(note.read_text(encoding="utf-8"))
    note.unlink()
    return f"The last upgrade ({info.get('id')}) didn't start properly, so I rolled it back."
