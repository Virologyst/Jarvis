"""The coloured progress bar in project notes, and updating a project note live in Obsidian."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_protocol.progress import bar_colour, has_bar, progress_bar, refresh_bar  # noqa: E402
from jarvis_terminal.obsidian import ObsidianError, project_update  # noqa: E402

NOTE = ("---\ntype: project\nstatus: active\nprogress: 45\nnext: old step\nupdated: 2026-11-01\n"
        "---\n# Thing\n\n" + progress_bar(45) + "\n\nIntro\n\n## Log\n- 2026-11-01: Started.\n")


def test_bar_colours_and_clamping():
    assert [bar_colour(p) for p in (0, 33, 34, 66, 67, 99, 100)] == [
        "#f85149", "#f85149", "#d29922", "#d29922", "#3fb950", "#3fb950", "#2f81f7"]
    assert 'width:100%' in progress_bar("150") and "width:0%" in progress_bar("nope")
    assert progress_bar(70).startswith('<div class="jarvis-progress" title="70% done"')
    assert ">70%</b>" in progress_bar("70%") and "\n" not in progress_bar(70)


def test_refresh_only_touches_the_bar():
    out = refresh_bar(NOTE, 80)
    assert "width:80%" in out and "width:45%" not in out and has_bar(out)
    assert out.replace(progress_bar(80), "") == NOTE.replace(progress_bar(45), "")
    assert refresh_bar("no bar here", 80) == "no bar here"


def test_project_update_live():
    out, said = project_update(NOTE, progress="70", status="on hold", next_step="Ship it",
                               log_entry="Demoed to the team", today="2026-11-12")
    assert said == ["progress 70%", "status on hold", "next step 'Ship it'", "noted it in the log"]
    assert "progress: 70\n" in out and "status: on hold\n" in out and "next: Ship it\n" in out
    assert "updated: 2026-11-12\n" in out and "width:70%" in out
    assert ("## Log\n- 2026-11-12: Progress: 45% → 70%\n- 2026-11-12: Status: active → on hold\n"
            "- 2026-11-12: Next step: Ship it\n- 2026-11-12: Demoed to the team\n"
            "- 2026-11-01: Started.") in out
    same, nothing = project_update(NOTE, progress="45")
    assert same == NOTE and nothing == []
    with pytest.raises(ObsidianError, match="Status can be"):
        project_update(NOTE, status="sideways")
    with pytest.raises(ObsidianError, match="no project properties"):
        project_update("# Just a note\n", progress="5")
    out, _ = project_update("---\nprogress: 5\n---\n# X\n", progress="10", today="2026-11-12")
    assert out.endswith("## Log\n- 2026-11-12: Progress: 5% → 10%\n")
