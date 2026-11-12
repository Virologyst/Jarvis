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


# --- Deleting and moving -----------------------------------------------------------------------

MINESWEEPER = """---
type: project
updated: 2026-10-27
---
# Craig's tasks
- [ ] Flag mines
\t- [ ] Flag sprite

## Notes
- grid

# Craig's tasks
- [ ] Flag mines

## Log
- 2026-10-27: Started.
"""


def test_delete_task_with_its_sub_items_only_once_applied_and_logged(vault: Vault):
    path = vault.root / "Projects" / "J.A.R.V.I.S.md"
    before = path.read_bytes()
    plan = vault.delete_task("Jarvis", "5")
    assert plan.title == "Delete the task 'Pi terminal' from J.A.R.V.I.S"
    assert plan.body == "From J.A.R.V.I.S, under Terminals:\n- [ ] Pi terminal\n\t- [ ] Speakerphone"
    assert path.read_bytes() == before                       # planning changes nothing
    assert plan.apply() == "Deleted 'Pi terminal', with 1 sub-item from J.A.R.V.I.S."
    note = text(vault, "Projects/J.A.R.V.I.S.md").replace("\r\n", "\n")
    assert "### Terminals\n- [ ] Email via Graph #j\n" in note and "Speakerphone" not in note
    assert "## Log\n- 2026-10-28: Deleted task: Pi terminal, with 1 sub-item\n" in note
    assert path.read_bytes().count(b"\r\n") == path.read_bytes().count(b"\n")


def test_delete_task_copies_go_together_unless_a_heading_picks_one(vault: Vault):
    both = vault.delete_task("Jarvis", "installer")
    assert "under Brain PC and Next up" in both.body
    one = vault.delete_task("Jarvis", "installer", section="Next up")
    assert one.body == "From J.A.R.V.I.S, under Next up:\n- [ ] Run the installer"
    one.apply()
    note = text(vault, "Projects/J.A.R.V.I.S.md").replace("\r\n", "\n")
    # what's left: the property, the copy under Brain PC and the log line
    assert "## Next up\n\n## Tasks" in note and note.count("Run the installer") == 3
    with pytest.raises(VaultError, match="changed since"):
        both.apply()                                        # planned before: no longer the same
    with pytest.raises(VaultError, match="isn't under Ideas; it's under Brain PC"):
        vault.delete_task("Jarvis", "installer", section="Ideas")
    with pytest.raises(VaultError, match="Which one?"):
        vault.add_task("Jarvis", "Check the Pi speaker")
        vault.delete_task("Jarvis", "pi")


def test_move_task_under_a_heading_or_another_task(vault: Vault):
    out = vault.move_task("Jarvis", "pi terminal", section="Brain PC")
    assert out == "Moved 'Pi terminal' to under Brain PC in J.A.R.V.I.S."
    note = text(vault, "Projects/J.A.R.V.I.S.md").replace("\r\n", "\n")
    assert ("- [ ] Reboot and check\n- [ ] Pi terminal\n\t- [ ] Speakerphone\n### Terminals\n"
            "- [ ] Email via Graph #j\n") in note
    assert "- 2026-10-28: Moved task: Pi terminal → under Brain PC\n" in note
    vault.move_task("Jarvis", "pi terminal", under="reboot")
    note = text(vault, "Projects/J.A.R.V.I.S.md").replace("\r\n", "\n")
    assert "- [ ] Reboot and check\n\t- [ ] Pi terminal\n\t\t- [ ] Speakerphone\n" in note
    with pytest.raises(VaultError, match="Which one should move"):
        vault.move_task("Jarvis", "installer", section="Terminals")
    vault.move_task("Jarvis", "installer", section="Terminals", from_section="Next up")
    assert "- [ ] Email via Graph #j\n- [ ] Run the installer\n" in text(
        vault, "Projects/J.A.R.V.I.S.md").replace("\r\n", "\n")
    with pytest.raises(VaultError, match="no 'Budget' heading"):
        vault.move_task("Jarvis", "reboot", section="Budget")
    with pytest.raises(VaultError, match="Where should it go"):
        vault.move_task("Jarvis", "reboot")


def test_delete_a_stray_duplicate_heading_but_never_guess(vault: Vault):
    (vault.root / "Projects" / "Project Minesweeper.md").write_text(MINESWEEPER, encoding="utf-8")
    with pytest.raises(VaultError, match="2 times.*1: line 5, starting '- \\[ \\] Flag mines'; "
                                         "2: line 12"):
        vault.delete_text("Minesweeper", heading="Craig's tasks")
    with pytest.raises(VaultError, match="only there 2 times"):
        vault.delete_text("Minesweeper", heading="Craig's tasks", which=3)
    with pytest.raises(VaultError, match="no heading called 'Craigs'"):
        vault.delete_text("Minesweeper", heading="Craigs")      # never a partial match
    with pytest.raises(VaultError, match="don't delete a project's log"):
        vault.delete_text("Minesweeper", heading="Log")
    with pytest.raises(VaultError, match="not both"):
        vault.delete_text("Minesweeper", text="grid", heading="Notes")
    first = vault.delete_text("Minesweeper", heading="Craig's tasks", which=1)
    assert first.title.endswith("its section, with Notes under it from Project Minesweeper")
    # The stray copy: a "# heading" holds the "## Log" under it, but the log is never swept up
    plan = vault.delete_text("Minesweeper", heading="# Craig's tasks", which=2)
    assert plan.title == "Delete the heading 'Craig's tasks' and its section from Project Minesweeper"
    assert plan.body == "From Project Minesweeper:\n# Craig's tasks\n- [ ] Flag mines"
    assert plan.apply() == ("Deleted the heading 'Craig's tasks' and its section from "
                            "Project Minesweeper.")
    note = text(vault, "Projects/Project Minesweeper.md")
    assert note.count("# Craig's tasks") == 1 and note.count("- [ ] Flag mines") == 1
    assert "## Notes\n- grid\n\n## Log\n- 2026-10-28: Deleted the heading 'Craig's tasks' and " \
        "its section\n- 2026-10-27: Started." in note
    assert "updated: 2026-10-28" in note


def test_delete_an_exact_passage(vault: Vault):
    with pytest.raises(VaultError, match="can't find those exact words"):
        vault.delete_text("Success Map", text="Laser")
    with pytest.raises(VaultError, match="can't find those exact words"):
        vault.delete_text("Success Map", text="type: map")      # properties are left alone
    with pytest.raises(VaultError, match="What should I delete"):
        vault.delete_text("Success Map", text=" \n")
    plan = vault.delete_text("Success Map", text="\t- 3D printers\n")
    assert plan.body == "From Success Map:\n\t- 3D printers"
    assert plan.apply() == "Deleted '- 3D printers' from Success Map."
    note = text(vault, "Success Map.md")
    assert note.endswith("## Ideas\n- Workshop\n") and "updated: 2026-10-28" in note
    vault.delete_text("Success Map", text=": use the template").apply()   # part of a line
    assert "- *Your next project*\n" in text(vault, "Success Map.md")
    stale = vault.delete_text("Success Map", text="- Workshop")
    vault.add_point("Success Map", "Ideas", "Workshop", under="Workshop")  # now there twice
    with pytest.raises(VaultError, match="changed since"):
        stale.apply()


def test_deleting_through_the_toolbox_goes_on_an_approval_card(vault: Vault):
    from jarvis_brain.approvals import ApprovalGate
    shown = []

    async def show(card):
        shown.append(card)

    gate = ApprovalGate()
    box = ToolBox(None, show, vault=vault, approvals=gate)
    said = asyncio.run(box.run("vault_delete_task", {"project": "Jarvis", "task": "reboot"}))
    assert said == ("An approval card is up: Delete the task 'Reboot and check' from J.A.R.V.I.S. "
                    "Nothing is deleted until it's approved.")
    assert "Reboot and check" in text(vault, "Projects/J.A.R.V.I.S.md")
    assert asyncio.run(gate.decide(shown[0].card_id, "approve")) == \
        "Deleted 'Reboot and check' from J.A.R.V.I.S."
    assert "- [ ] Reboot and check" not in text(vault, "Projects/J.A.R.V.I.S.md")
    said = asyncio.run(box.run("vault_delete_text", {"note": "Success Map", "heading": "Ideas",
                                                     "text": "", "which": "first"}))
    assert "approval card is up" in said and len(shown) == 2
    asyncio.run(gate.decide(shown[1].card_id, "reject"))
    assert "## Ideas" in text(vault, "Success Map.md")
    # Missing: said straight away, no card; no approval screen: nothing deleted
    assert "can't find a task" in asyncio.run(box.run("vault_delete_task", {
        "project": "Jarvis", "task": "nonexistent thing"}))
    assert len(shown) == 2
    assert "approval screen" in asyncio.run(ToolBox(None, show, vault=vault).run(
        "vault_delete_text", {"note": "Success Map", "heading": "Ideas"}))
    assert asyncio.run(box.run("vault_move_task", {"project": "Jarvis", "task": "email",
                                                   "section": "Next up"})).startswith("Moved")
    assert len(shown) == 2                                   # moving needs no card


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
    from jarvis_brain.tools import REQUEST_TOOLS
    assert tool_schema_problems(REQUEST_TOOLS) == []
    names = {t["name"] for t in TOOLS}
    for name in ("vault_projects", "vault_read", "vault_add_task", "vault_update_task",
                 "vault_update_project", "vault_new_project", "vault_add_point",
                 "vault_move_task", "vault_delete_task", "vault_delete_text"):
        assert name in names and name in SYSTEM_PROMPT
