"""Permanent listening: the spoken on/off switch and the noise filter. No models or network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.listenmode import addressed, is_noise, listen_mode_command  # noqa: E402
from jarvis_brain.voicecommands import voice_decision  # noqa: E402


def test_switch_on_by_voice() -> None:
    for text in ["Hey Jarvis, I want you to listen to me from now on.",
                 "Listen to me from now on", "Stay listening.", "Keep listening, please",
                 "Permanent listening on", "Jarvis, turn on permanent listening",
                 "OK Jarvis, permanent listening mode on"]:
        assert listen_mode_command(text) is True, text


def test_switch_off_by_voice() -> None:
    for text in ["Stop listening.", "You can stop listening now", "Permanent listening off",
                 "Jarvis, stop listening please", "Turn off permanent listening",
                 "Go back to the wake word"]:
        assert listen_mode_command(text) is False, text


def test_ordinary_talk_is_not_a_switch() -> None:
    for text in ["", "Stop listening to that song", "Don't stop listening",
                 "I keep listening to the radio", "What does permanent listening mean?",
                 "Listen", "Stop", "Keep listening and stop listening",
                 "Tell me about the weather, I want to listen to music from now on"]:
        assert listen_mode_command(text) is None, text


def test_noise_and_scraps_are_ignored_while_always_listening() -> None:
    for text in ["", "you", "Thank you.", "Thanks for watching!", "Hmm.", "Okay", "Bye."]:
        assert is_noise(text), text
    for text in ["What time is it?", "Turn the lights off", "Good morning Jarvis"]:
        assert not is_noise(text), text


def test_spoken_approve_needs_the_name_while_always_listening() -> None:
    assert voice_decision("Yes, approve") == "approve" and not addressed("Yes, approve")
    assert addressed("Jarvis, approve") and voice_decision("Jarvis, approve") == "approve"
