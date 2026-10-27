# Sir's Obsidian vault

Jarvis reads and keeps up to date the project notes in Sir's Obsidian vault (a folder of Markdown
notes). Code: `brain/jarvis_brain/vault.py`; tools in `tools.py`; how Jarvis uses them in
`persona.py`.

## What Jarvis can do

| Say | Tool |
|---|---|
| "How are my projects going?" | `vault_projects`: status, progress, next step, open tasks, tasks marked for J |
| "What's left on Jarvis?" / "Read me the success map" | `vault_read` (a project's summary with numbered tasks, or any note, or one heading of it) |
| "Add 'order a Pi case' to the Jarvis project, under Terminals" | `vault_add_task` (also as a sub-step, or marked #j) |
| "Tick off the installer task" | `vault_update_task` (done, reopen, reword) |
| "Jarvis is at 60 percent, next step is the Pi" / "Note that we chose the Jabra" | `vault_update_project` (status, progress, next step, log line) |
| "Start a project for the workshop fit-out" | `vault_new_project` (from `Templates/Project.md`, linked from the Success Map) |
| "Add 'laser cutter' to the ideas on the success map" | `vault_add_point` (a bullet, so a branch on that note's mind map) |

## The rules (also written in the vault's Success Map)

- A project is a note with the property `type: project`; new ones go in `Projects/`. Notes in
  `Templates/` are never projects.
- Properties: `status` (idea, planned, active, on hold, done), `progress` (0-100), `next`, `updated`.
- Tasks are checkboxes. `#j` marks one Jarvis may do or chase. The same task under "Next up" and
  "Tasks" is one task: ticking it ticks both.
- `## Log`: one line per change, newest first. Jarvis adds a line for every change it makes to a
  project and sets `updated`.

## Safety

- Nothing is deleted: tasks are ticked off, never removed, and there's no tool to delete a note or
  a line. Every project change is logged, so it can be put back by hand.
- Only notes found inside the vault are touched (`.obsidian`, `.trash` and `.git` are skipped),
  only `.md` files are written, and a name like `../x` can't leave the `Projects` folder.
- Sir edits in Obsidian at the same time: every call reads the file fresh, changes only the lines
  it must (properties included: the rest of the block is left as written), keeps the file's line
  endings, and replaces the file in one step, so Obsidian never sees half a file.
- These run without an approval card, like the task lists: they only change Sir's own notes, at
  his request, additively.

## Setting it up

Set `JARVIS_VAULT` in the brain's `.env` to the vault folder and restart the brain. With it
empty, Jarvis says the notes aren't connected.

The folder must be on a disk the brain's account can read and write. On the brain PC the brain
runs as a scheduled task with no login (S4U), and that kind of logon **can't open network shares
on other computers**, so the vault can't stay on the dev PC. Keep it on the brain PC (always on)
and open it from the other PCs over the network:

1. On the brain PC, put the vault in, say, `C:\Users\Jarvis\Vault\V Source` and share
   `C:\Users\Jarvis\Vault` with Craig's account (read/write).
2. In the brain's `.env`: `JARVIS_VAULT=C:\Users\Jarvis\Vault\V Source`.
3. On the dev PC, map the share to a drive letter and, in Obsidian, *Open folder as vault* on it.

The catch: away from the office network, Obsidian can't reach it. To work anywhere, sync the vault
through a private git repository instead (Obsidian's community *Git* plugin on the PCs, and the
brain pulling before and pushing after its changes); that isn't built yet.
