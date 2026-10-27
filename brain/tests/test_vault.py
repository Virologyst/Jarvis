"""Sir's Obsidian vault: reading projects, ticking and adding tasks, progress, logs, new projects."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.persona import SYSTEM_PROMPT  # noqa: E402
from jarvis_brain.selfupgrade import tool_schema_problems  # noqa: E402
from jarvis_brain.tools import TOOLS, ToolBox  # noqa: E402
from jarvis_brain.vault import (  # noqa: E402
    Vault, VaultError, find_tasks, read_properties, set_property,
)

try:
    from zoneinfo import ZoneInfo
    BRISBANE = ZoneInfo("Australia/Brisbane")
except Exception:  # pragma: no cover
    BRISBANE = None

pytestmark = pytest.mark.skipif(BRISBANE is None, reason="no timezone database")

MAP = """---
type: map
updated: 2026-10-28
markmap:
  initialExpandLevel: 2
---
# Success Map

## Projects
- [[J.A.R.V.I.S]]: J
- *Your next project*: use the template

## Ideas
- Workshop
\t- 3D printers
"""

PROJECT = """---
type: project
status: active
progress: 45
next: Run the installer
updated: 2026-10-27
tags:
  - project
aliases:
  - J
  - Jarvis
---
# J.A.R.V.I.S

The office voice assistant.

```markmap
# J
## - [ ] not a task, inside code
```

## Next up
- [ ] Run the installer

## Tasks
### Brain PC
- [x] Brain running
- [ ] Run the installer
- [ ] Reboot and check
### Terminals
- [ ] Pi terminal
\t- [ ] Speakerphone
- [ ] Email via Graph #j

## Log
- 2026-10-27: Started.
"""

TEMPLATE = """---
type: project
status: planned
progress: 0
next:
updated: {{date}}
tags:
  - project
---
# {{title}}

What it is and what "done" looks like. Back to the [[Success Map]].

## Tasks
- [ ]

## Log
- {{date}}: Project started.
"""


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Templates").mkdir()
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "Success Map.md").write_text(MAP, encoding="utf-8")
    (tmp_path / "Projects" / "J.A.R.V.I.S.md").write_bytes(PROJECT.replace("\n", "\r\n").encode())
    (tmp_path / "Templates" / "Project.md").write_text(TEMPLATE, encoding="utf-8")
    (tmp_path / ".obsidian" / "secret.md").write_text("---\ntype: project\n---\n", encoding="utf-8")
    when = datetime(2026, 10, 28, 9, 30, tzinfo=BRISBANE)
    return Vault(tmp_path, now=lambda: when)


def text(v: Vault, rel: str) -> str:
    return (v.root / rel).read_text(encoding="utf-8")


# --- Properties --------------------------------------------------------------------------------

def test_properties_read_and_set_keep_the_rest():
    lines = PROJECT.split("\n")
    props = read_properties(lines)
    assert props["status"] == "active" and props["progress"] == "45"
    assert props["aliases"] == ["J", "Jarvis"] and props["tags"] == ["project"]
    changed = set_property(lines, "next", "Fix: the thing #2")
    assert read_properties(changed)["next"] == "Fix: the thing #2"
    assert 'next: "Fix: the thing #2"' in changed
    assert changed[:3] == lines[:3] and "aliases:" in changed
    added = set_property(lines, "owner", "Craig")
    assert read_properties(added)["owner"] == "Craig"
    assert set_property(["# no props"], "status", "idea")[:3] == ["---", "status: idea", "---"]


def test_tasks_skip_code_blocks():
    tasks = find_tasks(PROJECT.split("\n"))
    assert [t.text for t in tasks][:2] == ["Run the installer", "Brain running"]
    assert not any("inside code" in t.text for t in tasks)
    assert tasks[-1].for_j and tasks[1].done


# --- Reading -----------------------------------------------------------------------------------

def test_overview_lists_projects_only_from_the_vault(vault: Vault):
    out = vault.projects()
    assert out.startswith("1 projects.")       # .obsidian is skipped
    assert "J.A.R.V.I.S: active, 45% done, next: Run the installer" in out
    assert "5 open tasks, 1 marked for you" in out  # "Run the installer" counted once


def test_read_project_by_alias_and_loose_name(vault: Vault):
    for name in ("Jarvis", "jarvis", "J", "j.a.r.v.i.s", "J.A.R.V.I.S.md"):
        out = vault.read(name)
        assert out.startswith("[Project J.A.R.V.I.S] Status active, 45% done.")
    out = vault.read("Jarvis")
    assert "About: The office voice assistant." in out
    assert "[1] Run the installer (under Next up)" in out
    assert "[3] Run the installer" not in out          # same task shown once
    assert "Email via Graph #j (marked for you)" in out
    assert "Recent log: 2026-10-27: Started." in out


def test_read_any_note_and_a_section(vault: Vault):
    assert vault.read("success map").startswith("[Success Map]\n# Success Map")
    assert "initialExpandLevel" not in vault.read("success map")
    part = vault.read("Jarvis", "terminals")
    assert part.startswith("[J.A.R.V.I.S, Terminals]") and "Pi terminal" in part
    with pytest.raises(VaultError, match="no 'Budget' heading"):
        vault.read("Jarvis", "Budget")
    with pytest.raises(VaultError, match="can't find a note called 'Nonsense'"):
        vault.read("Nonsense")


def test_not_set_up(tmp_path: Path):
    with pytest.raises(VaultError, match="isn't connected"):
        Vault(None).projects()
    with pytest.raises(VaultError, match="can't reach"):
        Vault(tmp_path / "missing").projects()


# --- Changing tasks ------------------------------------------------------------------------------

def test_tick_off_ticks_every_copy_and_logs(vault: Vault):
    out = vault.update_task("jarvis", "run the installer", "done")
    assert out.startswith("Ticked off 'Run the installer' in J.A.R.V.I.S. 2 of 6 tasks done")
    note = text(vault, "Projects/J.A.R.V.I.S.md")
    assert note.count("- [x] Run the installer") == 2
    raw = (vault.root / "Projects" / "J.A.R.V.I.S.md").read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")             # Windows line endings kept
    assert "updated: 2026-10-28" in note
    assert "## Log\n- 2026-10-28: Done: Run the installer\n- 2026-10-27: Started." in note
    assert "already done" in vault.update_task("jarvis", "installer", "done")


def test_task_by_number_rename_keeps_j_tag(vault: Vault):
    out = vault.update_task("Jarvis", "7", text="Email via Microsoft Graph")
    assert "renamed it to 'Email via Microsoft Graph #j'" in out
    assert "- [ ] Email via Microsoft Graph #j" in text(vault, "Projects/J.A.R.V.I.S.md")
    with pytest.raises(VaultError, match="no task 42"):
        vault.update_task("Jarvis", "42", "done")


def test_ambiguous_task_asks_which(vault: Vault):
    vault.add_task("Jarvis", "Check the Pi speaker")
    with pytest.raises(VaultError, match="Which one?"):
        vault.update_task("Jarvis", "pi", "done")


def test_add_task_to_tasks_heading_a_group_and_as_a_substep(vault: Vault):
    vault.add_task("Jarvis", "Calendar", for_j=True)
    vault.add_task("Jarvis", "Mount the eye", section="Terminals")
    vault.add_task("Jarvis", "Test echo cancelling", under="speakerphone")
    note = text(vault, "Projects/J.A.R.V.I.S.md").replace("\r\n", "\n")
    assert "## Tasks\n- [ ] Calendar #j\n### Brain PC" in note
    assert "- [ ] Email via Graph #j\n- [ ] Mount the eye\n\n## Log" in note
    assert "\t- [ ] Speakerphone\n\t\t- [ ] Test echo cancelling\n" in note
    assert "- 2026-10-28: Added task: Test echo cancelling\n" in note
    with pytest.raises(VaultError, match="no 'Budget' heading"):
        vault.add_task("Jarvis", "x", section="Budget")


# --- Projects ----------------------------------------------------------------------------------

def test_update_project_status_progress_next_and_log(vault: Vault):
    out = vault.update_project("J", status="on hold", progress="60%", next_step="Buy a Pi case",
                               log_entry="Decided to use the Jabra")
    assert out == ("Updated J.A.R.V.I.S: status on hold, progress 60%, next step 'Buy a Pi case', "
                   "noted it in the log.")
    note = text(vault, "Projects/J.A.R.V.I.S.md").replace("\r\n", "\n")
    props = read_properties(note.split("\n"))
    assert (props["status"], props["progress"], props["next"]) == ("on hold", "60", "Buy a Pi case")
    log_lines = note.split("## Log\n")[1].split("\n")
    assert log_lines[:5] == ["- 2026-10-28: Status: active → on hold",
                             "- 2026-10-28: Progress: 45% → 60%",
                             "- 2026-10-28: Next step: Buy a Pi case",
                             "- 2026-10-28: Decided to use the Jabra",
                             "- 2026-10-27: Started."]
    assert vault.update_project("J", progress="60") == "Nothing changed in J.A.R.V.I.S."
    assert vault.update_project("J", progress="250").endswith("progress 100%.")
    with pytest.raises(VaultError, match="Status can be"):
        vault.update_project("J", status="sideways")


def test_new_project_from_template_linked_from_the_map(vault: Vault):
    out = vault.new_project("Workshop / fit-out", "Fit out the workshop for printing.", "active")
    assert out == "Started a project note for Workshop fit-out in Projects, and linked it from the Success Map."
    note = text(vault, "Projects/Workshop fit-out.md")
    assert "# Workshop fit-out" in note and "{{" not in note
    assert "Fit out the workshop for printing. Back to the [[Success Map]]." in note
    assert "status: active" in note and "- 2026-10-28: Project started." in note
    assert "- [[J.A.R.V.I.S]]: J\n- [[Workshop fit-out]]: Fit out the workshop for printing.\n" \
        in text(vault, "Success Map.md")
    vault.add_task("workshop", "Get quotes")                  # fills the template's empty box
    assert "## Tasks\n- [ ] Get quotes\n" in text(vault, "Projects/Workshop fit-out.md")
    with pytest.raises(VaultError, match="already a project"):
        vault.new_project("workshop fit-out")


def test_add_point_grows_a_mind_map(vault: Vault):
    vault.add_point("Success Map", "Ideas", "Laser cutter", under="3D printers")
    vault.add_point("Success Map", "Ideas", "Coffee machine")
    vault.add_point("Success Map", "Clients", "Acme")
    note = text(vault, "Success Map.md")
    assert "- Workshop\n\t- 3D printers\n\t\t- Laser cutter\n- Coffee machine\n" in note
    assert note.rstrip().endswith("## Clients\n- Acme")
    assert "updated: 2026-10-28" in note
    vault.add_point("Jarvis", "Ideas", "A second eye")     # a project note logs it
    assert "- 2026-10-28: Added to Ideas: A second eye" in text(vault, "Projects/J.A.R.V.I.S.md")


def test_writes_stay_inside_the_vault(vault: Vault, tmp_path: Path):
    with pytest.raises(VaultError):
        vault._write(tmp_path.parent / "outside.md", ["x"])
    with pytest.raises(VaultError, match="only write notes"):
        vault._write(vault.root / "Projects.base", ["x"])
    out = vault.new_project("../../escape")
    assert (vault.root / "Projects" / "escape.md").exists() and "escape" in out
    assert not list(vault.root.rglob("*.jarvis-tmp"))


# --- As Jarvis's tools -------------------------------------------------------------------------

def test_tools_run_through_the_toolbox(vault: Vault):
    async def no_card(card):  # pragma: no cover - vault tools never show cards
        raise AssertionError("no card expected")

    box = ToolBox(None, no_card, vault=vault)
    run = lambda name, **a: asyncio.run(box.run(name, a))  # noqa: E731
    assert run("vault_projects").startswith("1 projects.")
    assert run("vault_add_task", project="Jarvis", text="Calendar", section="", under="",
               for_j=True).startswith("Added 'Calendar #j'")
    assert run("vault_update_task", project="Jarvis", task="calendar", status="done",
               text="").startswith("Ticked off 'Calendar #j'")
    assert run("vault_update_project", project="Jarvis", status="", progress="50", next_step="",
               log="").startswith("Updated J.A.R.V.I.S: progress 50%")
    assert run("vault_read", note="", section="") == "Which note?"
    assert "can't find a project called 'Nope'" in run("vault_update_project", project="Nope",
                                                       status="", progress="1", next_step="", log="")
    assert run("vault_read", note="Jarvis", section="").startswith("[Project J.A.R.V.I.S]")
    assert "isn't connected" in asyncio.run(
        ToolBox(None, no_card).run("vault_projects", {}))


def test_vault_tools_fit_the_api_limits_and_persona_mentions_them():
    assert tool_schema_problems(TOOLS) == []
    names = {t["name"] for t in TOOLS}
    for name in ("vault_projects", "vault_read", "vault_add_task", "vault_update_task",
                 "vault_update_project", "vault_new_project", "vault_add_point"):
        assert name in names and name in SYSTEM_PROMPT
