"""Mood tags, spoken approve/reject, themes, and per-theme voices. No models or network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.emotions import EMOTIONS, TagStripper, parse_sentence  # noqa: E402
from jarvis_brain.themes import THEMES, ThemeStore, resolve  # noqa: E402
from jarvis_brain.tools import TOOLS  # noqa: E402
from jarvis_brain.voicecommands import parse_voice_decision, pick_card, voice_decision  # noqa: E402
from jarvis_protocol import Card  # noqa: E402


def test_mood_tag_parsing() -> None:
    assert parse_sentence("[amused] Very good, Sir.") == ("amused", "Very good, Sir.")
    assert parse_sentence("[ Question ] Shall I?") == ("question", "Shall I?")
    assert parse_sentence("Shall I?") == ("question", "Shall I?")        # guessed from the "?"
    assert parse_sentence("It's done.") == ("statement", "It's done.")
    assert parse_sentence("[made-up] Fine.")[0] == "statement"            # unknown tag ignored...
    assert parse_sentence("[bogus] Fine.")[1] == "Fine."                  # ...and never spoken
    assert set(EMOTIONS) >= {"amused", "question", "statement", "thinking", "sad"}


def test_tags_never_reach_the_screen_even_split_across_pieces() -> None:
    s = TagStripper()
    pieces = ["[amu", "sed] Very ", "good. [ques", "tion] Shall I? See [1] and [", "x"]
    out = "".join(s.feed(p) for p in pieces) + s.flush()
    assert out == "Very good. Shall I? See [1] and [x"
    assert "amused" not in out and "question" not in out


def test_spoken_approve_and_reject() -> None:
    for text in ["Approve.", "approve", "Approved!", "Yes, approve.", "Approve it please",
                 "Hey Jarvis, approve", "Jarvis approve", "OK Jarvis, approve that"]:
        assert voice_decision(text) == "approve", text
    for text in ["Reject.", "Reject that", "Decline", "Jarvis, reject it"]:
        assert voice_decision(text) == "reject", text


def test_anything_else_is_conversation_not_a_decision() -> None:
    for text in ["", "Don't approve that", "Do not approve", "Should I approve?",
                 "What would you approve?", "Approve the email to Jo",
                 "I approve of the new voice, it's lovely", "Approve and reject", "Okay",
                 "Yes", "Go ahead", "Cancel"]:
        assert voice_decision(text) is None, text
    for text in ["Approve the upgrade later", "Can you approve the upgrade?",
                 "Approve the upgrade and the email", "Approve the upgrade to version two",
                 "Don't apply the upgrade", "Approve approve reject"]:
        assert voice_decision(text) is None, text


def test_natural_phrases_name_what_to_approve() -> None:
    cases = {
        "Approve the upgrade": ("approve", "upgrade"),
        "Jarvis, approve the upgrade please": ("approve", "upgrade"),
        "Hey Jarvis, approve the self-upgrade": ("approve", "upgrade"),
        "Go ahead and approve it, Jarvis": ("approve", None),
        "Yes, go ahead and apply the upgrade": ("approve", "upgrade"),
        "Approve that change, Sir": ("approve", "upgrade"),
        "Approve the undo": ("approve", "undo"),
        "Reject the upgrade": ("reject", "upgrade"),
        "Decline that email": ("reject", "email"),
        "Approve the latest one": ("approve", None),
    }
    for text, expected in cases.items():
        assert parse_voice_decision(text) == expected, text


def test_spoken_target_picks_the_right_card() -> None:
    upgrade = Card("u1", "approval", "Start a self-upgrade", data={"upgrade_id": "a", "step": "start"})
    email = Card("e1", "approval", "Send the email to Jo")
    undo = Card("d1", "approval", "Undo the last upgrade", data={"upgrade_id": "b", "step": "undo"})
    pending = [upgrade, email]                      # newest last
    assert pick_card(pending, None) is email
    assert pick_card(pending, "upgrade") is upgrade
    assert pick_card(pending, "email") is email
    assert pick_card(pending, "undo") is None
    assert pick_card([email, undo], "upgrade") is undo   # "the upgrade" covers undoing one
    assert pick_card([], None) is None


def test_theme_lookup() -> None:
    assert resolve("the eye theme").name == "eye"
    assert resolve("Arc").name == "arc"
    assert resolve("lightning").name == "arc"
    assert resolve("purple unicorn") is None
    assert THEMES["arc"].voice == "kokoro:bf_lily"     # the Arc theme speaks with Lily


def test_theme_choice_is_remembered(tmp_path) -> None:
    store = ThemeStore(tmp_path / "themes.json")
    assert store.get("office-pi").name == "eye"
    store.set("office-pi", THEMES["arc"])
    assert ThemeStore(tmp_path / "themes.json").get("office-pi").name == "arc"
    assert ThemeStore(tmp_path / "themes.json").get("other").name == "eye"


def test_set_theme_tool_offers_only_real_themes() -> None:
    tool = next(t for t in TOOLS if t["name"] == "set_theme")
    assert tool["input_schema"]["properties"]["theme"]["enum"] == list(THEMES)


def test_no_tool_can_approve() -> None:
    """Approving is a person's job: no tool name or description offers it."""
    for t in TOOLS:
        assert "approve" not in t["name"]
        assert not t["name"].startswith(("decide", "accept", "confirm"))
