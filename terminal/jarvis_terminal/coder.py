"""Jarvis's coder: Claude Code (through the Agent SDK) working in Sir's project folders on this PC.

The brain asks with FileRequests whose op starts with "coder_" (start, reply, status, stop,
projects); answers are text for Jarvis. A job runs in the background in one project folder,
under PycharmProjects or WebstormProjects (JARVIS_CODE_ROOTS), or in a new folder made for it.
Claude Code edits files in that folder by itself; anything else it wants to do (run a command,
install, delete, commit, touch other folders) goes to the brain as a CoderEvent("permission"),
which becomes an approval card, and waits for Sir's CoderDecision. A few read-only and test
commands are allowed without asking. Folders in JARVIS_CODE_NO_GIT (WebstormProjects\\API: Sir
runs git there himself) never get git. When a job finishes, a CoderEvent("done"/"failed") lets
Jarvis say so. Uses this PC's Claude Code sign-in (`claude login`), or ANTHROPIC_API_KEY if set.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_protocol import CoderEvent

log = logging.getLogger(__name__)

HOME = Path(os.path.expanduser("~"))
DEFAULT_ROOTS = [HOME / "PycharmProjects", HOME / "WebstormProjects"]
DEFAULT_NO_GIT = [HOME / "WebstormProjects" / "API"]
PERMISSION_WAIT_S = 20 * 60          # an unanswered request is refused after this
STATE = Path(__file__).resolve().parents[2] / "data" / "coder.json"

# Commands that only look or test, allowed without a card when nothing is chained or redirected
SAFE_COMMANDS = [
    "ls", "dir", "pwd", "cat", "type", "head", "tail", "wc", "tree", "where", "which",
    "git status", "git diff", "git log", "git show", "git branch", "git init",
    "python --version", "python -m pytest", "py -m pytest", "pytest", "pip list", "pip show",
    "node --version", "npm --version", "npm test", "npm run test", "npm run lint",
    "npm run build", "npx tsc --noEmit", "cargo test", "cargo build", "go test", "go build",
]
CHAINING = re.compile(r"[;&|><`]|\$\(")

_PROMPT = """\
You're being driven by Jarvis, Craig's voice assistant; Craig ("Sir") asked for this by voice. \
Work only inside {cwd}. Nobody can answer questions mid-task: make sensible choices and say what \
you chose, or if something truly needs Craig's decision, stop and explain. Anything beyond \
editing files in this folder is shown to Craig as an approval card and may be refused; if \
refused, carry on without it or explain. Don't commit or push unless the task says so. Your \
final message is read aloud: end with a short plain summary in a few sentences (what you did, \
whether it runs and tests pass, what's left or needs his decision), with no code, paths or \
lists in it.{extra}"""
_NO_GIT = (" Never run git in this folder, not even to look: Craig handles git here himself. "
           "Say what's ready to commit instead.")
_NEW = (" This is a brand new, empty project folder: set the project up from scratch (sensible "
        "structure, a README saying how to run it, a .gitignore, dependencies declared, a first "
        "working version and tests where it makes sense).")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _paths(env: str, default: list[Path]) -> list[Path]:
    raw = os.environ.get(env, "")
    return [Path(p.strip()) for p in raw.split(";") if p.strip()] or default


@dataclass
class Job:
    id: str
    project: Path
    task: str
    new: bool = False
    status: str = "running"          # running, waiting (for approval), done, failed, stopped
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    session_id: str | None = None
    summary: str = ""
    activity: deque = field(default_factory=lambda: deque(maxlen=8))
    waiting_for: str = ""
    cost: float = 0.0
    queued: list[str] = field(default_factory=list)


class Coder:
    def __init__(self, send: Callable[[object], Awaitable[None]], roots: list[Path] | None = None,
                 no_git: list[Path] | None = None, client_factory=None, state: Path = STATE):
        self.send = send                   # to the brain (drops it while disconnected)
        self.roots = roots or _paths("JARVIS_CODE_ROOTS", DEFAULT_ROOTS)
        self.no_git = no_git or _paths("JARVIS_CODE_NO_GIT", DEFAULT_NO_GIT)
        self.state = state
        self._client_factory = client_factory or self._sdk_client
        self.job: Job | None = None
        self._task: asyncio.Task | None = None
        self._client = None
        self._asks: dict[str, tuple[asyncio.Future, CoderEvent]] = {}
        self._unsent: list[CoderEvent] = []     # job endings to say once the brain is back

    @property
    def available(self) -> bool:
        if os.environ.get("JARVIS_CODER", "on").strip().lower() in ("0", "off", "no", "false"):
            return False
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return False
        return any(r.is_dir() for r in self.roots)

    # --- Requests from the brain --------------------------------------------------------------

    async def handle(self, op: str, args: dict) -> tuple[bool, str]:
        try:
            fn = getattr(self, "op_" + op.removeprefix("coder_"), None)
            if fn is None:
                return False, f"The coder can't {op}."
            return True, await fn(**{k: v for k, v in (args or {}).items() if v not in (None, "")})
        except CoderError as e:
            return False, str(e)
        except TypeError as e:
            return False, f"That didn't make sense to the coder ({e})."

    async def op_projects(self) -> str:
        lines = []
        for root in self.roots:
            if root.is_dir():
                names = sorted(p.name for p in root.iterdir()
                               if p.is_dir() and not p.name.startswith("."))
                lines.append(f"{root.name}: {', '.join(names) or 'none'}")
        return "Project folders. " + "; ".join(lines) + "."

    def _find(self, project: str) -> Path:
        name = project.strip().strip("\\/")
        candidate = Path(name)
        if candidate.is_absolute():
            if candidate.is_dir() and any(_inside(candidate, r) for r in self.roots):
                return candidate
            raise CoderError("I can only work in the project folders: "
                             + ", ".join(str(r) for r in self.roots) + ".")
        folders = [p for r in self.roots if r.is_dir() for p in r.iterdir() if p.is_dir()]
        key = re.sub(r"[\s_.-]+", "", name.lower())
        exact = [p for p in folders if re.sub(r"[\s_.-]+", "", p.name.lower()) == key]
        close = exact or [p for p in folders if key and key in re.sub(r"[\s_.-]+", "", p.name.lower())]
        if len(close) == 1:
            return close[0]
        if close:
            raise CoderError("Several projects match: " + ", ".join(
                f"{p.name} in {p.parent.name}" for p in close) + ". Which one?")
        raise CoderError(f"There's no project called {project}. " + self._names())

    def _names(self) -> str:
        folders = [p.name for r in self.roots if r.is_dir() for p in r.iterdir()
                   if p.is_dir() and not p.name.startswith(".")]
        return "The projects are: " + ", ".join(sorted(folders)) + "."

    def _new_folder(self, project: str, where: str = "") -> Path:
        name = re.sub(r'[<>:"/\\|?*]+', "", project).strip().strip(".")
        if not name:
            raise CoderError("What should the new project be called?")
        name = re.sub(r"\s+", "-", name)
        roots = [r for r in self.roots if r.is_dir()]
        if not roots:
            raise CoderError("None of the project folders exist on this PC.")
        want = re.sub(r"\W", "", where.lower())
        root = next((r for r in roots if want and want in r.name.lower()), roots[0])
        path = root / name
        if path.exists():
            raise CoderError(f"{name} already exists in {root.name}; I can work on it instead.")
        path.mkdir(parents=True)
        return path

    async def op_start(self, project: str, task: str, new: str | bool = False,
                       where: str = "") -> str:
        if self.job and self.job.status in ("running", "waiting"):
            raise CoderError(f"I'm still working on {self.job.project.name}: "
                             f"{self.job.task[:80]}. Stop it first, or add to it.")
        if not task.strip():
            raise CoderError("What should I build?")
        is_new = str(new).strip().lower() in ("true", "1", "yes")
        folder = self._new_folder(project, where) if is_new else self._find(project)
        self.job = Job(uuid.uuid4().hex[:8], folder, task.strip(), new=is_new)
        self._task = asyncio.create_task(self._run(task.strip()))
        made = f"Made {folder} and started" if is_new else "Started"
        return (f"{made} the coding job in {folder.name}. It runs in the background; I'll say "
                "when it's done, and anything risky comes up as a card.")

    async def op_reply(self, message: str) -> str:
        job = self.job or self._last_job()
        if job is None:
            raise CoderError("There's no coding job to add to. Start one first.")
        if job.status in ("running", "waiting"):
            job.queued.append(message)
            return f"Passed on: it'll pick that up as soon as it finishes its current step."
        self.job = Job(uuid.uuid4().hex[:8], job.project, message, session_id=job.session_id)
        self._task = asyncio.create_task(self._run(message))
        return f"Carrying on in {job.project.name}."

    async def op_status(self) -> str:
        job = self.job or self._last_job()
        if job is None:
            return "No coding job has run yet."
        took = (job.finished or time.time()) - job.started
        mins = f"{took / 60:.0f} minutes" if took >= 90 else f"{took:.0f} seconds"
        state = {"running": "working", "waiting": "waiting for your approval",
                 "done": "finished", "failed": "stopped with a problem",
                 "stopped": "stopped"}.get(job.status, job.status)
        lines = [f"Coding job in {job.project.name} ({job.project.parent.name}): {state}, "
                 f"{mins}. Task: {job.task[:300]}"]
        if job.waiting_for:
            lines.append(f"Waiting on: {job.waiting_for}")
        if job.activity:
            lines.append("Recent steps: " + "; ".join(job.activity))
        if job.summary:
            lines.append(f"Its report: {job.summary[:1500]}")
        return "\n".join(lines)

    async def op_stop(self) -> str:
        if not self.job or self.job.status not in ("running", "waiting"):
            return "Nothing's running."
        self.job.status = "stopped"
        for fut, _ in list(self._asks.values()):
            if not fut.done():
                fut.set_result((False, "Stopped by Sir."))
        if self._client is not None:
            try:
                await self._client.interrupt()
            except Exception:  # noqa: BLE001 - it may already be finishing
                pass
        if self._task:
            self._task.cancel()
        return f"Stopped the job in {self.job.project.name}. Anything it already changed stays."

    def decide(self, request_id: str, allow: bool, message: str = "") -> None:
        waiting = self._asks.get(request_id)
        if waiting and not waiting[0].done():
            waiting[0].set_result((allow, message))

    async def reconnected(self) -> None:
        """The brain is back: ask again about anything still waiting, and say what finished."""
        for _, event in list(self._asks.values()):
            await self.send(event)
        while self._unsent:
            await self.send(self._unsent.pop(0))

    # --- Running a job ------------------------------------------------------------------------

    def _sdk_client(self, options):
        from claude_agent_sdk import ClaudeSDKClient
        return ClaudeSDKClient(options=options)

    def _no_git(self, folder: Path) -> bool:
        return any(_inside(folder, p) for p in self.no_git)

    def _options(self, job: Job):
        from claude_agent_sdk import ClaudeAgentOptions
        extra = (_NO_GIT if self._no_git(job.project) else "") + (_NEW if job.new else "")
        opts = dict(
            cwd=str(job.project),
            permission_mode="acceptEdits",
            can_use_tool=self._permission,
            setting_sources=["user", "project", "local"],
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": _PROMPT.format(cwd=job.project, extra=extra)},
            disallowed_tools=["Bash(git:*)", "Bash(git *)"] if self._no_git(job.project) else [],
        )
        if job.session_id:
            opts["resume"] = job.session_id
        if os.environ.get("JARVIS_CODE_MODEL"):
            opts["model"] = os.environ["JARVIS_CODE_MODEL"]
        return ClaudeAgentOptions(**opts)

    async def _run(self, prompt: str) -> None:
        job = self.job
        try:
            from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
            self._client = self._client_factory(self._options(job))
            await self._client.connect()
            while True:
                await self._client.query(prompt)
                async for msg in self._client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, ToolUseBlock):
                                job.activity.append(_describe(block.name, block.input or {}))
                            elif isinstance(block, TextBlock) and block.text.strip():
                                job.activity.append("said: " + " ".join(block.text.split())[:160])
                    elif isinstance(msg, ResultMessage):
                        job.session_id = msg.session_id or job.session_id
                        job.cost += msg.total_cost_usd or 0.0
                        job.summary = (msg.result or "").strip()
                        if msg.is_error:
                            job.status = "failed"
                if job.queued and job.status != "failed":
                    prompt = "Also, from Sir: " + "\n".join(job.queued)
                    job.queued = []
                    continue
                break
            if job.status == "running":
                job.status = "done"
        except asyncio.CancelledError:
            job.status = "stopped"
        except Exception as e:  # noqa: BLE001 - say what went wrong rather than dying quietly
            log.exception("coder job failed")
            job.status = "failed"
            job.summary = job.summary or f"It stopped with an error: {e}"
        finally:
            job.finished = time.time()
            job.waiting_for = ""
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self._client = None
            self._save(job)
        if job.status in ("done", "failed"):
            event = CoderEvent(job.id, job.status, job.summary[:3000] or "It finished.",
                               data={"project": job.project.name, "task": job.task[:300]})
            try:
                await self.send(event)
            except Exception:  # noqa: BLE001 - not connected: say it when the brain is back
                self._unsent.append(event)

    async def _permission(self, tool: str, data: dict, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        job = self.job
        verdict = self._judge(job, tool, data)
        if verdict is True:
            return PermissionResultAllow()
        if isinstance(verdict, str):
            return PermissionResultDeny(message=verdict)
        what = _describe(tool, data)
        request_id = uuid.uuid4().hex
        event = CoderEvent(job.id, "permission", what, request_id=request_id,
                           data={"project": job.project.name, "tool": tool,
                                 "detail": _detail(tool, data)})
        fut = asyncio.get_running_loop().create_future()
        self._asks[request_id] = (fut, event)
        job.status, job.waiting_for = "waiting", what
        try:
            await self.send(event)
            allow, message = await asyncio.wait_for(fut, PERMISSION_WAIT_S)
        except asyncio.TimeoutError:
            allow, message = False, "Nobody answered the approval card in time."
        except Exception as e:  # noqa: BLE001 - can't reach the brain: never allow blind
            allow, message = False, f"Couldn't ask Sir ({e})."
        finally:
            self._asks.pop(request_id, None)
            if job.status == "waiting":
                job.status, job.waiting_for = "running", ""
        job.activity.append(("allowed: " if allow else "refused: ") + what)
        if allow:
            return PermissionResultAllow()
        return PermissionResultDeny(message=message or "Sir said no. Carry on without it, or "
                                    "explain what you need in your final message.")

    def _judge(self, job: Job, tool: str, data: dict):
        """True: allow without asking. A string: refuse, saying why. None: ask Sir."""
        if tool == "Bash":
            command = " ".join(str(data.get("command", "")).split())
            if self._no_git(job.project) and re.search(r"(^|[\s;&|(])git(\s|$)", command):
                return "No git in this project: Sir handles git here himself."
            if not CHAINING.search(command) and any(
                    command == safe or command.startswith(safe + " ") for safe in SAFE_COMMANDS):
                return True
            return None
        path = data.get("file_path") or data.get("path") or data.get("notebook_path")
        if tool in ("Read", "Glob", "Grep", "LS", "TodoWrite", "TaskCreate", "TaskUpdate",
                    "TaskList", "TaskGet") and (not path or _inside(Path(path), job.project)):
            return True
        return None

    # --- Remembering the last job across restarts ---------------------------------------------

    def _save(self, job: Job) -> None:
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text(json.dumps({
                "project": str(job.project), "task": job.task, "session_id": job.session_id,
                "status": job.status, "summary": job.summary[:3000], "started": job.started,
                "finished": job.finished}), encoding="utf-8")
        except OSError as e:
            log.warning("coder: couldn't save the job (%s)", e)

    def _last_job(self) -> Job | None:
        try:
            d = json.loads(self.state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        job = Job("saved", Path(d["project"]), d.get("task", ""), status=d.get("status", "done"),
                  started=d.get("started", 0.0), session_id=d.get("session_id"),
                  summary=d.get("summary", ""))
        job.finished = d.get("finished", 0.0)
        return job


class CoderError(Exception):
    """Something to tell Sir."""


def _describe(tool: str, data: dict) -> str:
    """A step in a few words, for status and cards."""
    if tool == "Bash":
        return "run: " + " ".join(str(data.get("command", "")).split())[:200]
    path = data.get("file_path") or data.get("path") or data.get("notebook_path")
    if path:
        return f"{tool.lower()} {Path(str(path)).name}"
    for key in ("pattern", "url", "query", "description", "prompt"):
        if data.get(key):
            return f"{tool.lower()}: {str(data[key])[:120]}"
    return tool


def _detail(tool: str, data: dict) -> str:
    if tool == "Bash":
        return str(data.get("command", ""))[:2000]
    path = data.get("file_path") or data.get("path") or data.get("notebook_path") or ""
    body = data.get("content") or data.get("new_string") or ""
    return (f"{path}\n{str(body)[:1500]}" if path else json.dumps(data)[:2000])
