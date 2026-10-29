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


# --- A wake phrase on its own is answered at once, without Claude ------------------------------

def test_bare_wake_gets_a_quick_reply() -> None:
    from jarvis_brain.wakecheck import bare_wake_reply
    assert bare_wake_reply("Hey Jarvis.", PHRASES) == "[question] Yes, Sir?"
    assert bare_wake_reply("Um, okay Jarvis?", PHRASES) == "[question] Yes, Sir?"
    assert bare_wake_reply("Good morning, Jarvis!", PHRASES) == "[happy] Good morning, Sir."
    assert bare_wake_reply("Are you awake, Jarvis?", PHRASES) == "[amused] For you, Sir, always."
    for text in ["Hey Jarvis, what's the time?", "Good morning Jarvis, what's on today?",
                 "Hey", "Jarvis", "", "Thank you."]:
        assert bare_wake_reply(text, PHRASES) is None, text


def test_session_answers_a_bare_wake_without_calling_claude() -> None:
    import asyncio
    from types import SimpleNamespace as NS

    import pytest
    server = pytest.importorskip("jarvis_brain.server")
    from jarvis_protocol import AudioStart, Error, ReplyText, decode

    class FakeWS:
        closed = False

        def __init__(self):
            self.sent = []

        async def send_str(self, raw):
            self.sent.append(decode(raw))

        async def send_bytes(self, data):
            pass

    class NoClaude:
        @property
        def beta(self):
            raise AssertionError("Claude was called for a bare wake word")

    synthesized = []
    tts = NS(sample_rate=16000,
             synthesize=lambda text, voice, speed: synthesized.append(text) or b"\0" * 3200)
    brain = NS(settings=NS(model="m", effort="low", idle_reset_minutes=10), client=NoClaude(),
               upgrades=None, tasks=None, tts=tts, stt=NS(transcribe=lambda pcm: "Hey Jarvis."),
               approvals=NS(pending=lambda: []))

    async def go():
        ws = FakeWS()
        session = server.TerminalSession(brain, ws)
        for _ in range(2):
            session.turn_t0 = 1.0
            await session._run_turn(b"\0" * 32000)
        assert not any(isinstance(m, Error) for m in ws.sent)
        assert sum(isinstance(m, AudioStart) for m in ws.sent) == 2
        assert "Yes, Sir?" in "".join(m.text for m in ws.sent if isinstance(m, ReplyText))
        assert synthesized == ["Yes, Sir?"]      # said twice, synthesised once
        # Claude will see that it was woken when the actual request comes
        assert session.conversation.messages[-2:] == [
            {"role": "user", "content": "Hey Jarvis."},
            {"role": "assistant", "content": "[question] Yes, Sir?"}]

    asyncio.run(go())
