"""Pointed answers: the per-screen brief/detailed switch."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.answerstyle import (  # noqa: E402
    BRIEF, DETAILED, DETAILED_NOTE, AnswerStyles, style_command,
)
from jarvis_brain.persona import SYSTEM_PROMPT  # noqa: E402


def test_spoken_switches_only_on_their_own():
    for said in ("Be brief.", "Jarvis, be brief please", "hey jarvis keep it short",
                 "Pointed answers from now on", "J, get to the point"):
        assert style_command(said) == BRIEF, said
    for said in ("Give me the detail", "Jarvis, give me the details please", "Detailed answers",
                 "longer answers from now on"):
        assert style_command(said) == DETAILED, said
    for said in ("give me more detail on the quote", "be brief with Simon's email",
                 "what's the weather", "brief me on today"):
        assert style_command(said) is None, said


def test_remembered_per_screen(tmp_path):
    styles = AnswerStyles(tmp_path / "answer-style.json")
    assert styles.get("craig-pc") == BRIEF
    assert styles.set("craig-pc", BRIEF) == "[statement] Already keeping it brief, Sir."
    assert styles.set("craig-pc", DETAILED) == "[statement] I'll give you the detail, Sir."
    again = AnswerStyles(tmp_path / "answer-style.json")
    assert again.get("craig-pc") == DETAILED and again.get("pi") == BRIEF
    assert again.set("craig-pc", BRIEF) == "[statement] Brief it is, Sir."


def test_prompt_asks_for_pointed_answers():
    assert "Lead with the answer itself" in SYSTEM_PROMPT
    assert "never repeat his question" in SYSTEM_PROMPT
    assert "detailed" in DETAILED_NOTE and DETAILED_NOTE.startswith("\n\n(")
