"""Microphone in, speaker out (sounddevice / PortAudio)."""

from __future__ import annotations

import asyncio
import logging
import threading

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
            try:
                self._stream = sd.RawOutputStream(
                    samplerate=sample_rate, channels=1, dtype="int16",
                    device=self.device, callback=self._callback,
                )
                self._stream.start()
            except (sd.PortAudioError, ValueError) as e:
                # No speaker plugged in: drop Jarvis's audio rather than queue it forever
                log.warning("no speaker (%s); replies are shown on screen only", e)
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


def rms(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
