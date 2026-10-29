"""Choosing the mic and speaker: the picker's list, finding a saved device, falling back to the
default, remembering the choice, and the page's actions. A fake device list, no real audio."""
from __future__ import annotations

import asyncio
import json
import sys
import types

import numpy as np
import pytest

pytest.importorskip("aiohttp")
try:
    import sounddevice  # noqa: F401
except (ImportError, OSError):
    sys.modules["sounddevice"] = types.ModuleType("sounddevice")
from jarvis_terminal import audio, client  # noqa: E402

HOSTS = [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}]


def dev(name, host, inputs=0, outputs=0):
    return {"name": name, "hostapi": host, "max_input_channels": inputs,
            "max_output_channels": outputs}


# Like the dev PC: every device listed once per audio system, names cut short under MME
DEVICES = [
    dev("Microsoft Sound Mapper - Input", 0, inputs=2),           # 0
    dev("Microphone (Streamer X Main)", 0, inputs=2),             # 1
    dev("Microphone (2- Arctis Nova Pro ", 0, inputs=1),          # 2
    dev("Speakers (High Definition Audio", 0, outputs=2),         # 3
    dev("Primary Sound Capture Driver", 1, inputs=2),             # 4
    dev("Microphone (Streamer X Main)", 1, inputs=2),             # 5
    dev("Microphone (2- Arctis Nova Pro Wireless)", 1, inputs=1),  # 6
    dev("Primary Sound Driver", 1, outputs=2),                    # 7
    dev("Speakers (High Definition Audio Device)", 1, outputs=2),  # 8
    dev("Headphones (2- Arctis Nova Pro Wireless)", 1, outputs=2),  # 9
    dev("Headphones (2- Arctis Nova Pro Wireless)", 2, outputs=2),  # 10 (WASAPI)
]


class FakeSd(types.SimpleNamespace):
    def query_hostapis(self):
        return HOSTS

    def query_devices(self, index=None):
        return DEVICES if index is None else DEVICES[index]


@pytest.fixture
def fake_sd(monkeypatch):
    sd = FakeSd(default=types.SimpleNamespace(hostapi=0, device=[1, 3]))
    monkeypatch.setattr(audio, "sd", sd)
    monkeypatch.setattr(audio, "_PREFERRED_HOST", "Windows DirectSound")
    return sd


def test_picker_lists_each_device_once_with_full_names(fake_sd):
    assert audio.list_devices() == {
        "inputs": ["Microphone (Streamer X Main)", "Microphone (2- Arctis Nova Pro Wireless)"],
        "outputs": ["Speakers (High Definition Audio Device)",
                    "Headphones (2- Arctis Nova Pro Wireless)"],
    }


def test_find_device_by_name_prefers_the_pickers_audio_system(fake_sd):
    assert audio.find_device("Microphone (Streamer X Main)", "input") == 5
    assert audio.find_device("Headphones (2- Arctis Nova Pro Wireless)", "output") == 9
    assert audio.find_device("arctis", "input") == 2          # part of a name, any system
    assert audio.find_device("Arctis", "output") == 9
    assert audio.find_device("RODE NT-USB", "input") is None   # not plugged in: the default
    assert audio.find_device("", "input") is None
    assert audio.find_device(None, "output") is None
    assert audio.find_device(13, "input") == 13               # an index from --mic


def test_device_name_for_the_screen(fake_sd):
    assert audio.device_name(None, "input") == "System default (Microphone (Streamer X Main))"
    fake_sd.default.device = [2, 3]                      # names cut short by MME
    assert audio.device_name(None, "input") == \
        "System default (Microphone (2- Arctis Nova Pro Wireless))"
    assert audio.device_name(None, "output") == \
        "System default (Speakers (High Definition Audio Device))"
    assert audio.device_name(9, "output") == "Headphones (2- Arctis Nova Pro Wireless)"


def test_choice_is_remembered_and_command_line_wins(tmp_path):
    path = tmp_path / "data" / "audio-devices.json"
    choice = audio.DeviceChoice(path)
    assert (choice.mic, choice.speaker) == ("", "")
    choice.set(mic="Microphone (2- Arctis Nova Pro Wireless)")
    choice.set(speaker="Headphones (2- Arctis Nova Pro Wireless)")
    assert json.loads(path.read_text()) == {"mic": "Microphone (2- Arctis Nova Pro Wireless)",
                                           "speaker": "Headphones (2- Arctis Nova Pro Wireless)"}
    again = audio.DeviceChoice(path)
    assert again.mic == "Microphone (2- Arctis Nova Pro Wireless)"
    assert audio.DeviceChoice(path, mic="Streamer").mic == "Streamer"
    assert audio.DeviceChoice(path, mic=5).mic == 5
    path.write_text("not json")
    assert audio.DeviceChoice(path).mic == ""                 # a broken file is ignored
    memory = audio.DeviceChoice(None, mic="x")
    memory.set(speaker="y")                                   # nothing written anywhere
    assert (memory.mic, memory.speaker) == ("x", "y")


def test_chime_is_short_and_not_too_loud():
    pcm = np.frombuffer(audio.chime(24000), dtype=np.int16)
    assert 0.4 < len(pcm) / 24000 < 0.5
    assert 0 < np.abs(pcm).max() <= int(0.3 * 32767) + 1


# --- The terminal and the page -------------------------------------------------------------

class FakePlayer:
    busy = False
    level = 0.0
    buffered_seconds = 0.0

    def __init__(self, device=None):
        self.device = device
        self.played = []

    def clear(self): pass
    def close(self): pass
    def begin(self, rate): self.played.append(("begin", rate))
    def feed(self, pcm): self.played.append(("feed", len(pcm)))


class FakeUi:
    def __init__(self):
        self.sent = []

    async def send_str(self, raw):
        self.sent.append(json.loads(raw))


def make_terminal(monkeypatch, fake_sd, tmp_path):
    monkeypatch.setattr(client, "_load_vad", lambda: None)
    monkeypatch.setattr(client, "Player", FakePlayer)
    choice = audio.DeviceChoice(tmp_path / "audio.json", speaker="Arctis")
    t = client.Terminal("ws://x", "test", 0, 0.5, use_wake=False, devices=choice)
    t.ui_sockets.add(FakeUi())
    return t


def test_speaker_choice_opens_that_device(monkeypatch, fake_sd, tmp_path):
    t = make_terminal(monkeypatch, fake_sd, tmp_path)
    assert t.player.device == 9


def test_page_gets_the_list_choice_and_whats_in_use(monkeypatch, fake_sd, tmp_path):
    t = make_terminal(monkeypatch, fake_sd, tmp_path)
    t.mic_open = "Microphone (Streamer X Main)"
    asyncio.run(t.send_devices())
    (ui,) = t.ui_sockets
    msg = ui.sent[-1]
    assert msg["type"] == "audio_devices"
    assert msg["mic"] == "" and msg["speaker"] == "Arctis"
    assert msg["mic_open"] == "Microphone (Streamer X Main)"
    assert msg["speaker_open"] == "Headphones (2- Arctis Nova Pro Wireless)"
    assert "Microphone (2- Arctis Nova Pro Wireless)" in msg["inputs"]


def test_choosing_saves_and_asks_the_mic_loop_to_reopen(monkeypatch, fake_sd, tmp_path):
    t = make_terminal(monkeypatch, fake_sd, tmp_path)
    asyncio.run(t.choose_devices("Microphone (2- Arctis Nova Pro Wireless)", None))
    assert t._reopen_audio.is_set()
    saved = json.loads((tmp_path / "audio.json").read_text())
    assert saved == {"mic": "Microphone (2- Arctis Nova Pro Wireless)", "speaker": "Arctis"}


def test_open_mic_falls_back_to_the_default(monkeypatch, fake_sd, tmp_path):
    t = make_terminal(monkeypatch, fake_sd, tmp_path)
    t.devices.mic = "Microphone (Streamer X Main)"
    tried = []

    def try_mic(device):
        tried.append(device)
        if device is not None:
            raise RuntimeError("device busy")
        return "mic"

    monkeypatch.setattr(t, "_try_mic", try_mic)
    assert asyncio.run(t._open_mic()) == "mic"
    assert tried == [5, None]
    assert t.mic_open.startswith("System default")


def test_test_speaker_plays_a_chime_but_never_over_jarvis(monkeypatch, fake_sd, tmp_path):
    t = make_terminal(monkeypatch, fake_sd, tmp_path)
    t.test_speaker()
    assert t.player.played[0] == ("begin", 24000) and t.player.played[1][0] == "feed"
    t.player.played.clear()
    t.state = "listening"
    t.test_speaker()
    assert t.player.played == []
