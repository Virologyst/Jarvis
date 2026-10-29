"""Permanent listening on the terminal: any clear voice opens the mic, but not noise, not a
blip, and never Jarvis's own voice. No audio devices or models needed."""
from __future__ import annotations

import asyncio
import json
import sys
import types

import numpy as np
import pytest

from jarvis_protocol import AudioStart, ListenMode, ListenStart, State, decode, encode

pytest.importorskip("aiohttp")
try:
    import sounddevice  # noqa: F401
except (ImportError, OSError):   # no PortAudio here: the test never touches a real device
    sys.modules["sounddevice"] = types.ModuleType("sounddevice")
from jarvis_terminal import client  # noqa: E402


class FakePlayer:
    busy = False
    level = 0.0
    buffered_seconds = 0.0

    def clear(self): pass
    def close(self): pass
    def begin(self, rate): pass
    def feed(self, pcm): pass


class FakeBrain:
    closed = False

    def __init__(self):
        self.sent: list = []
        self.frames = 0

    async def send_str(self, raw):
        self.sent.append(decode(raw))

    async def send_bytes(self, data):
        self.frames += 1


def make_terminal(monkeypatch) -> client.Terminal:
    monkeypatch.setattr(client, "_load_vad", lambda: None)   # loudness decides instead
    monkeypatch.setattr(client, "Player", lambda device=None: FakePlayer())
    t = client.Terminal("ws://x", "test", 0, 0.5, use_wake=False)
    t.brain_ws = FakeBrain()
    return t


def frame(level: int) -> bytes:
    n = int(client.FRAME_S * 16_000)
    return (np.ones(n, dtype=np.int16) * level).tobytes()


LOUD, HUSH = frame(2000), frame(10)


def run(t: client.Terminal, frames) -> None:
    async def go():
        for f in frames:
            await t._on_frame(f)
    asyncio.run(go())


def test_off_by_default_and_voice_alone_does_nothing(monkeypatch) -> None:
    t = make_terminal(monkeypatch)
    assert t.always_listening is False
    run(t, [LOUD] * 10)
    assert t.state == "idle" and not t.brain_ws.sent


def test_on_any_clear_voice_starts_listening_with_its_start(monkeypatch) -> None:
    t = make_terminal(monkeypatch)
    t.set_always_listening(True)
    t._quiet_until = 0
    run(t, [HUSH] * 20 + [LOUD] * client.ONSET_FRAMES)
    assert t.state == "listening"
    assert t.brain_ws.sent[0].reason == "permanent"
    # only the last second goes along, not the whole 3.5 s of room noise before it
    assert t.brain_ws.frames <= int(client.ONSET_PREROLL_S / client.FRAME_S)


def test_blips_and_quiet_noise_are_ignored(monkeypatch) -> None:
    t = make_terminal(monkeypatch)
    t.set_always_listening(True)
    t._quiet_until = 0
    blip = [LOUD] * (client.ONSET_FRAMES - 1) + [HUSH] * 3     # a cough, a door
    run(t, blip * 5 + [frame(60)] * 30)                          # ...and a quiet hum
    assert t.state == "idle" and not t.brain_ws.sent


def test_never_hears_its_own_voice(monkeypatch) -> None:
    t = make_terminal(monkeypatch)
    t.set_always_listening(True)
    t._quiet_until = 0
    t.player.busy = True                    # Jarvis is speaking (e.g. an announcement)
    run(t, [LOUD] * 20)
    assert t.state == "idle" and not t.brain_ws.sent
    t.player.busy = False                   # ...and just after it stops: still the echo guard
    run(t, [LOUD] * client.ONSET_FRAMES)
    assert t.state == "idle"


def test_after_a_reply_goes_back_to_idle_not_a_follow_up(monkeypatch) -> None:
    t = make_terminal(monkeypatch)
    t.set_always_listening(True)
    t.state, t._replied = "waiting", True
    run(t, [HUSH])
    assert t.state == "idle" and not t.brain_ws.sent


def test_brain_switches_it_and_the_page_hears(monkeypatch) -> None:
    t = make_terminal(monkeypatch)
    shown = []

    async def to_ui(payload):
        shown.append(payload)
    t.to_ui = to_ui
    asyncio.run(t._from_brain(encode(ListenMode(True))))
    assert t.always_listening and shown[-1] == {"type": "listen_mode", "on": True, "quiet": False}
    asyncio.run(t._from_brain(encode(ListenMode(False))))
    assert not t.always_listening


def test_unprompted_reminder_is_followed_by_an_open_mic(monkeypatch) -> None:
    t = make_terminal(monkeypatch)
    asyncio.run(t._from_brain(encode(AudioStart(16000))))       # an ordinary announcement
    assert t.state == "idle"
    asyncio.run(t._from_brain(encode(State("speaking"))))
    asyncio.run(t._from_brain(encode(AudioStart(16000, follow_up=True))))   # a reminder
    assert t.state == "waiting"                     # its own voice isn't heard as a wake
    run(t, [LOUD])
    assert t.state == "waiting" and not t.brain_ws.sent
    asyncio.run(t._from_brain(encode(State("idle"))))
    run(t, [HUSH])                                  # finished playing: listen for an answer
    assert t.state == "listening"
    assert isinstance(t.brain_ws.sent[-1], ListenStart)
    assert t.brain_ws.sent[-1].reason == "follow_up"


def test_protocol_round_trip() -> None:
    assert decode(encode(AudioStart(22050))).follow_up is False   # old brains: no follow-up
    msg = decode(encode(ListenMode(True, quiet=True)))
    assert isinstance(msg, ListenMode) and msg.on and msg.quiet
    assert json.loads(encode(ListenMode(False)))["type"] == "listen_mode"
