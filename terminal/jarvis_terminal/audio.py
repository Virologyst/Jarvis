"""Microphone in, speaker out (sounddevice / PortAudio)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd

from jarvis_protocol import MIC_SAMPLE_RATE

FRAME_SAMPLES = 1280  # 80 ms at 16 kHz: what openWakeWord expects

log = logging.getLogger(__name__)


class Microphone:
    """Delivers 80 ms int16 frames to an asyncio queue."""

    def __init__(self, loop: asyncio.AbstractEventLoop, device=None):
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._loop = loop
        self._stream = sd.RawInputStream(
            samplerate=MIC_SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=FRAME_SAMPLES, device=device, callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status) -> None:
        self._loop.call_soon_threadsafe(self._put, bytes(indata))

    def _put(self, frame: bytes) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # fell behind: drop the oldest frame
        self.queue.put_nowait(frame)

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()


class Player:
    """Plays int16 mono PCM pushed in pieces. The stream reopens if the rate changes."""

    def __init__(self, device=None):
        self.device = device
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stream: sd.RawOutputStream | None = None
        self._rate = 0

    def begin(self, sample_rate: int) -> None:
        self.clear()
        if self._stream is None or self._rate != sample_rate:
            self.close()
            self._rate = sample_rate
            error = None
            for device in dict.fromkeys([self.device, None]):   # the chosen one, then the default
                try:
                    self._stream = sd.RawOutputStream(
                        samplerate=sample_rate, channels=1, dtype="int16",
                        device=device, callback=self._callback,
                    )
                    self._stream.start()
                    return
                except (sd.PortAudioError, ValueError) as e:
                    error = e
            # No speaker plugged in: drop Jarvis's audio rather than queue it forever
            log.warning("no speaker (%s); replies are shown on screen only", error)
            self._stream, self._rate = None, 0

    def feed(self, pcm: bytes) -> None:
        if self._stream is None:
            return
        with self._lock:
            self._buffer.extend(pcm)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    @property
    def busy(self) -> bool:
        with self._lock:
            return len(self._buffer) > 0

    @property
    def buffered_seconds(self) -> float:
        """How long until everything queued so far has been heard."""
        with self._lock:
            return len(self._buffer) / 2 / self._rate if self._rate else 0.0

    level = 0.0   # loudness of what's playing right now, 0..1 (drives the face's movement)

    def _callback(self, outdata, frames, time_info, status) -> None:
        need = frames * 2
        with self._lock:
            chunk = bytes(self._buffer[:need])
            del self._buffer[:need]
        outdata[:len(chunk)] = chunk
        if len(chunk) < need:
            outdata[len(chunk):] = b"\x00" * (need - len(chunk))
        self.level = min(1.0, rms(chunk) / 6000.0) if chunk else 0.0

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


def refresh_devices() -> None:
    """Re-scan audio devices (PortAudio only lists them at start-up), so a mic plugged in later shows up."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:  # private API; worst case we just don't see new devices
        log.debug("could not refresh audio devices: %s", e)


# --- Choosing devices --------------------------------------------------------------------------
# Windows lists every device several times, once per audio system. DirectSound has full names
# and converts sample rates itself (WASAPI won't open a mic at 16 kHz), so the picker uses it.
# "Primary"/"Sound Mapper" entries are the system default, which the picker offers separately.
_PREFERRED_HOST = "Windows DirectSound" if sys.platform == "win32" else None
_DEFAULT_ALIASES = ("primary sound", "microsoft sound mapper")


def _host_index() -> int:
    if _PREFERRED_HOST:
        for i, api in enumerate(sd.query_hostapis()):
            if api["name"] == _PREFERRED_HOST:
                return i
    return sd.default.hostapi if isinstance(sd.default.hostapi, int) else 0


def list_devices() -> dict[str, list[str]]:
    """Device names for the picker: {"inputs": [...], "outputs": [...]}."""
    host = _host_index()
    inputs, outputs = [], []
    for dev in sd.query_devices():
        name = dev["name"]
        if dev["hostapi"] != host or name.lower().startswith(_DEFAULT_ALIASES):
            continue
        if dev["max_input_channels"] > 0 and name not in inputs:
            inputs.append(name)
        if dev["max_output_channels"] > 0 and name not in outputs:
            outputs.append(name)
    return {"inputs": inputs, "outputs": outputs}


def find_device(name: str | int | None, kind: str) -> int | str | None:
    """The device to open for a saved choice: None means the system default.

    A name is looked for in the picker's list first, then anywhere (a name typed on the command
    line may come from another audio system). Not plugged in: None, so the default is used.
    """
    if name is None or name == "" or isinstance(name, int):
        return name if name != "" else None
    channels = "max_input_channels" if kind == "input" else "max_output_channels"
    devices = list(enumerate(sd.query_devices()))
    host = _host_index()
    for exact_host in (True, False):
        for i, dev in devices:
            if dev[channels] > 0 and (dev["hostapi"] == host or not exact_host) \
                    and dev["name"] == name:
                return i
    want = name.lower()
    for i, dev in devices:
        if dev[channels] > 0 and want in dev["name"].lower():
            return i
    return None


def device_name(index: int | str | None, kind: str) -> str:
    """What's actually in use, for the screen."""
    try:
        if index is None:
            default = sd.default.device[0 if kind == "input" else 1]
            if default is None or default < 0:
                return "none"
            name = sd.query_devices(default)["name"]
            # MME (the default's audio system on Windows) cuts names at 31 characters: show the
            # picker's full name for the same device
            listed = list_devices()["inputs" if kind == "input" else "outputs"]
            name = next((n for n in listed if n.startswith(name.rstrip())), name)
            return f"System default ({name})"
        return sd.query_devices(index)["name"]
    except Exception:
        return "unknown"


class DeviceChoice:
    """The chosen mic and speaker by name ("" = system default), saved across restarts."""

    def __init__(self, path: Path | None, mic: str | int | None = None,
                 speaker: str | int | None = None):
        self.path = path    # None: remember for this run only
        saved = {}
        try:
            saved = json.loads(path.read_text(encoding="utf-8")) if path else {}
        except (OSError, ValueError):
            pass
        # A device given on the command line wins over the saved one for this run
        self.mic = mic if mic is not None else saved.get("mic", "")
        self.speaker = speaker if speaker is not None else saved.get("speaker", "")

    def set(self, mic: str | None = None, speaker: str | None = None) -> None:
        if mic is not None:
            self.mic = mic
        if speaker is not None:
            self.speaker = speaker
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"mic": self.mic, "speaker": self.speaker}, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("couldn't save the audio device choice: %s", e)


def chime(rate: int = 24000) -> bytes:
    """A short two-note chime to test the speaker."""
    out = []
    for freq, secs in ((660, 0.18), (880, 0.28)):
        t = np.arange(int(rate * secs)) / rate
        fade = np.minimum(1, np.minimum(t, secs - t) / 0.02)
        out.append(0.3 * np.sin(2 * np.pi * freq * t) * fade)
    return (np.concatenate(out) * 32767).astype(np.int16).tobytes()


def rms(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
