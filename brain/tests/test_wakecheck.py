"""Wake-phrase matching on speech-to-text output. Needs no models: run with pytest."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.wakecheck import heard_wake_phrase, phrases_from_env  # noqa: E402

PHRASES = phrases_from_env()


def test_default_phrases_match_typical_transcripts() -> None:
    for text in [
        "Hey Jarvis.", "Hey, Jarvis, what's the time?", "OK Jarvis", "Okay, Jarvis.",
        "Good morning, Jarvis!", "Good morning Jarvis, what's on today?",
        "Are you awake, Jarvis?", "are you awake jarvis", "Okay Jervis.",
    ]:
        assert heard_wake_phrase(text, PHRASES), text


def test_ordinary_speech_does_not_match() -> None:
    for text in [
        "", "Good morning.", "Are you awake?", "Jarvis is a great name for an assistant.",
        "I said good morning to Jarvis's mum", "Okay, that's fine.", "Pass me that jar.",
    ]:
        assert not heard_wake_phrase(text, PHRASES), text


def test_phrase_list_from_env(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_WAKE_PHRASES", "wake up jarvis|daddy's home")
    phrases = phrases_from_env()
    assert heard_wake_phrase("Wake up, Jarvis.", phrases)
    assert heard_wake_phrase("Daddy's home!", phrases)
    assert not heard_wake_phrase("Hey Jarvis", phrases)
