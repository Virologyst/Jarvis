# Coding jobs: Jarvis and Claude Code

Jarvis gets software built in Sir's project folders by handing the work to **Claude Code** on
Sir's PC and acting as the go-between: "J, in the Jarvis project, add a dark-mode button",
"make me a new Python app that renames my photos by date", "how's the coder doing?", "make it
blue", "stop it".

## How it works

- Sir's PC's terminal runs the coder (`terminal/jarvis_terminal/coder.py`), using Anthropic's
  Agent SDK, which drives the Claude Code installed there with its sign-in (`claude login`).
  Its Hello says `coder=True`; the brain's tools `code_projects`, `code_start` (an existing
  project, or `new=true` for a new folder built from scratch), `code_reply`, `code_status` and
  `code_stop` reach it as `coder_*` FileRequests.
- A job runs in the background in **one project folder** under `PycharmProjects` or
  `WebstormProjects` (`JARVIS_CODE_ROOTS`, `;`-separated). New projects get a new folder there.
- Claude Code **edits files in that folder by itself** (accept-edits mode). Reading, searching
  and a short list of look-only and test commands (`git status`, `pytest`, `npm test`,
  `npm run build`, ...; nothing chained or redirected) run without asking.
- **Everything else asks Sir**: installing, other commands, deleting, committing, pushing,
  touching other folders. The terminal sends a `CoderEvent("permission")`; the brain puts up an
  approval card and says what it wants; approving or rejecting (tap or "approve") sends a
  `CoderDecision` back. Unanswered after 20 minutes, it's refused.
- **No git in `WebstormProjects\API`** (`JARVIS_CODE_NO_GIT`): Sir does git there himself, so
  any git command is refused there without asking, and the coder is told so.
- One job at a time. Follow-ups while it works are passed on when it finishes its current step;
  after it's done they carry on the same Claude Code session (remembered in `data/coder.json`,
  so even after a restart).
- When it finishes (`CoderEvent("done"/"failed")`), Jarvis says the gist of its report once the
  terminal is quiet, like a reminder. Its final message is written to be heard.

## Setting it up (Sir's PC)

1. Claude Code installed and signed in (`claude` in a terminal, then `/login`).
2. `.venv\Scripts\python -m pip install "claude-agent-sdk>=0.2"` (or rerun
   `scripts\setup-dev.ps1`).
3. Restart the terminal (it starts at sign-in: `Stop-ScheduledTask` / `Start-ScheduledTask
   "Jarvis terminal"`). Its log says "Jarvis can run coding jobs here", and the brain's says the
   terminal connected "(runs coding jobs)". `JARVIS_CODER=off` turns it off.

Settings (that PC's `.env`): `JARVIS_CODE_ROOTS`, `JARVIS_CODE_NO_GIT`, `JARVIS_CODE_MODEL`
(otherwise Claude Code's own default).
