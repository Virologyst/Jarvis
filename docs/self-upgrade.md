# Self-upgrade

Jarvis can change its own code, by asking Claude Code to do the work. A person approves every
step that changes anything.

## How it goes

1. **Ask.** "Jarvis, give yourself a kitchen timer." Jarvis restates the job and calls
   `request_self_upgrade`. An **approval card** appears: *Start a self-upgrade*.
2. **Approve the start.** Claude Code (headless, signed in with the Max plan) works in a
   separate git worktree, `../jarvis-upgrades/<id>`, on branch `jarvis/upgrade-<id>`. The live
   Jarvis keeps running. Ask "how's the upgrade going?" any time.
3. **Jarvis checks the work itself:** commits it, refuses it if it touched a protected file, and
   runs the tests plus an import check. It doesn't take Claude Code's word for it.
4. **Approve the result.** A second card shows Claude Code's summary, the files changed, and
   whether the tests passed. Approving fast-forwards the live checkout and restarts the brain.
5. **Probation.** The supervisor waits for the new brain to answer `/health`. If it crashes or
   doesn't answer within 3 minutes, the supervisor resets to the previous commit, restarts, and
   Jarvis tells you it rolled back.
6. **Undo.** "Jarvis, undo the last upgrade" offers an approval card that resets to the commit
   before it.

Logs for every job are in `data/upgrades/<id>/` on the brain PC: the request, the job's state
(`job.json`), Claude Code's output, the full diff (`diff.patch`) and the test output.

## Approving by voice

Say a short, plain approval: "Approve", "Approve the upgrade", "Jarvis, approve the upgrade
please", "Go ahead and apply the upgrade", "Reject the upgrade". Naming what you mean
("the upgrade", "the undo", "that email") picks the newest card about it; otherwise the newest
card is used. It's decided in code from your words (`voicecommands.py`), never by Claude.
Anything negated, a question, or with extra detail ("don't approve", "can you approve the
upgrade?", "approve the email to Jo") is treated as conversation, not a decision; tap the card
instead.

## Restarts

Restarting the brain doesn't lose an upgrade:

- **Cards come back.** Approval cards are saved to `data/approvals.json` and restored at
  start-up, with the same card on every screen. Only the code that made a card can rebuild what
  it does, and only if it still makes sense (the file holds cards, never code).
- **Work that was in progress starts again.** The job's progress is saved to
  `data/upgrades/current.json` at every step. If the brain restarts while Claude Code is working
  (the start was already approved), it stops any leftover Claude Code process, clears the old
  worktree and starts the work again on a fresh branch (`jarvis/upgrade-<id>-r2`). After two
  interrupted attempts it gives up and says so. Jarvis tells the first screen that connects.
- **Ready results wait.** A finished upgrade's *Apply* card comes back, still needing your
  approval.
- A rejected or expired card frees Jarvis to take the next upgrade request.

## Guard rails (enforced in code)

| Rule | Where |
|---|---|
| Nothing runs without an approval tap: start, apply, undo | `selfupgrade.py` via `approvals.py` |
| A saved card is removed from disk before its action runs, so a crash can't replay it | `approvals.py` |
| An upgrade whose tools the API would refuse (over 24 optional settings across strict tools, or duplicate names) is blocked, not offered: tests passing isn't enough | `tool_schema_problems` in `selfupgrade.py` |
| Claude Code works in a separate worktree, never the live checkout | `selfupgrade.py` |
| Claude Code's tools: edit files, run pytest, read-only git. No web, push, commit, reset, rm, pip | `ALLOWED_TOOLS` / `DISALLOWED_TOOLS` |
| Claude Code uses the Max login, never Jarvis's API key (removed from its environment) | `UpgradeManager._env` |
| Protected files can't be changed by an upgrade; such a change is blocked, not offered | `PROTECTED` |
| The health check and rollback live in the supervisor, so an upgrade can't fake them | `supervisor.py` |
| One job at a time; 30-minute limit (`JARVIS_UPGRADE_TIMEOUT_MIN`) | `selfupgrade.py` |

Protected: `approvals.py`, `selfupgrade.py`, `supervisor.py`, `brain/tests/test_selfupgrade.py`,
`.env*`, `.gitignore`, `.gitattributes`, `.claude/`, `.git/`. Change these by hand.

The real safety is the second approval: read the card (and `diff.patch` for anything big) before
tapping Approve. An upgrade can add new tools; any tool with side effects must go through the
approval gate, which upgrades can't change.

## Running it

The brain must run under the supervisor for upgrades to restart it. On the brain PC the
"Jarvis brain" scheduled task does that at every boot (set up by `brain\install.ps1`; see the
README). By hand, for testing:

```
.venv\Scripts\python -m jarvis_brain.supervisor
```

Claude Code must be installed and signed in for the account the brain runs as
(`claude` once, then `/login`). Upgrades to `terminal/`, `ui/` or `protocol/` also need the
terminals updated (`git pull` there); Jarvis mentions it when that's the case.
