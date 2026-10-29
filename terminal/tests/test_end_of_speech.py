"""After "hey Jarvis": the mic closes soon after you stop, even if the voice detector mistakes a
steady hum for voice, and every stage of the turn is timed in the log. No devices or models."""
from __future__ import annotations

import asyncio
import logging
import sys
import types

import numpy as np
import pytest

from jarvis_protocol import AudioStart, ListenEnd, ReplyText, Transcript, encode

pytest.importorskip("aiohttp")
try:
    import sounddevice  # noqa: F401
except (ImportError, OSError):   # no PortAudio here: the test never touches a real device
    sys.modules["sounddevice"] = types.ModuleType("sounddevice")
from jarvis_terminal import client  # noqa: E402

from test_listen_mode import FakeBrain, FakePlayer  # noqa: E402


class StuckVAD:
    """A voice detector that calls everything voice (what a fan or hum can do to it)."""

    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def predict(self, pcm, frame_size):
        self.calls.append((len(pcm), frame_size))
        return 0.9

    def reset_states(self):
        pass


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_terminal(monkeypatch, vad) -> tuple[client.Terminal, Clock]:
    clock = Clock()
    monkeypatch.setattr(client.time, "monotonic", clock)
    monkeypatch.setattr(client, "_load_vad", lambda: vad)
    monkeypatch.setattr(client, "Player", lambda device=None: FakePlayer())
    t = client.Terminal("ws://x", "test", 0, 0.5, use_wake=False)
    t.brain_ws = FakeBrain()
    return t, clock


def frame(level: int) -> bytes:
    return (np.ones(int(client.FRAME_S * 16_000), dtype=np.int16) * level).tobytes()


HUM, VOICE = frame(300), frame(3000)


def feed(t: client.Terminal, clock: Clock, frames, stop_when_idle: bool = False) -> int:
    """Feed frames 80 ms apart; returns how many were fed."""
    async def go():
        n = 0
        for f in frames:
            clock.now += client.FRAME_S
            await t._on_frame(f)
            n += 1
            if stop_when_idle and t.state != "listening":
                break
        return n
    return asyncio.run(go())


def wake(t: client.Terminal) -> None:
    asyncio.run(t.start_listening("wake", with_preroll=True))


def test_bare_wake_ends_quickly_in_a_humming_room(monkeypatch) -> None:
    t, clock = make_terminal(monkeypatch, StuckVAD())
    feed(t, clock, [HUM] * 30)          # the room before "hey Jarvis"
    wake(t)
    n = feed(t, clock, [HUM] * 400, stop_when_idle=True)
    assert t.state == "waiting"
    assert isinstance(t.brain_ws.sent[-1], ListenEnd)
    # Ended after the usual pause, not the 25 s time limit
    assert n * client.FRAME_S < client.SILENCE_TO_END_S + 0.5


def test_real_speech_over_the_hum_keeps_listening(monkeypatch) -> None:
    t, clock = make_terminal(monkeypatch, StuckVAD())
    feed(t, clock, [HUM] * 30)
    wake(t)
    feed(t, clock, [VOICE] * 40)        # 3.2 s of someone talking
    assert t.state == "listening"
    n = feed(t, clock, [HUM] * 400, stop_when_idle=True)
    assert t.state == "waiting" and n * client.FRAME_S < client.SILENCE_TO_END_S + 0.5


def test_voice_detector_gets_whole_windows(monkeypatch) -> None:
    vad = StuckVAD()
    t, clock = make_terminal(monkeypatch, vad)
    wake(t)
    feed(t, clock, [VOICE] * 10)
    assert vad.calls and all(size == client.VAD_WINDOW and n % client.VAD_WINDOW == 0
                             for n, size in vad.calls)
    fed = 10 * int(client.FRAME_S * 16_000)
    assert sum(n for n, _ in vad.calls) + len(t._vad_carry) == fed


def test_each_stage_is_timed_in_the_log(monkeypatch, caplog) -> None:
    t, clock = make_terminal(monkeypatch, StuckVAD())
    feed(t, clock, [HUM] * 30)
    wake(t)
    with caplog.at_level(logging.INFO, logger=client.log.name):
        feed(t, clock, [HUM] * 400, stop_when_idle=True)

        async def answer():
            for msg in (Transcript("Hey Jarvis."), ReplyText("Yes, Sir?"), AudioStart(24_000)):
                clock.now += 0.1
                await t._from_brain(encode(msg))
        asyncio.run(answer())
    text = caplog.text
    assert "end of speech after" in text and "pause" in text
    assert "transcript 0.10s after end of speech" in text
    assert "first reply audio 0.30s after end of speech" in text
