"""Node <-> host serial protocol (ported from EGH490 acoustic-proto nodelink/protocol.py @ dac085a). Mirrors firmware/node/src/protocol.h; keep the two in step.

Node -> host frames:  A5 5A | type u8 | len u16 LE | payload | crc16 u16 LE
crc16 is CRC-16/CCITT-FALSE over type, len and payload.
Host -> node: ASCII command lines ending in '\n'.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterator

import numpy as np

MAGIC = b"\xA5\x5A"
PROTO_VERSION = 1

MSG_HEARTBEAT = 0x01
MSG_BEARING = 0x02
MSG_AUDIO = 0x03
MSG_LOG = 0x04

MODE_NAMES = {0: "off", 1: "bearing", 2: "raw", 3: "both"}

FLAG_DETECTED = 0x01
FLAG_LINEAR = 0x02

_HEARTBEAT = struct.Struct("<BBBBIIf2f2fII16s")
_BEARING = struct.Struct("<BBHIIfffff")
_AUDIO_HDR = struct.Struct("<BBHII")

MAX_PAYLOAD = 8192


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass
class Heartbeat:
    node_id: int
    mode: str
    mic_count: int
    uptime_ms: int
    sample_rate: int
    spacing_m: float
    rms_dbfs: tuple[float, float]
    peak_dbfs: tuple[float, float]
    i2s_overruns: int
    tx_dropped: int
    fw_version: str


@dataclass
class Bearing:
    node_id: int
    detected: bool
    linear: bool
    seq: int
    t_ms: int
    theta_deg: float
    elev_deg: float
    coherence: float
    level_dbfs: float
    tau_us: float


@dataclass
class Audio:
    node_id: int
    seq: int
    first_sample: int
    samples: np.ndarray     # shape (n_frames, channels), int32 holding signed 24-bit values


@dataclass
class Log:
    text: str


Message = Heartbeat | Bearing | Audio | Log


def decode(msg_type: int, payload: bytes) -> Message | None:
    if msg_type == MSG_HEARTBEAT and len(payload) == _HEARTBEAT.size:
        f = _HEARTBEAT.unpack(payload)
        return Heartbeat(
            node_id=f[1], mode=MODE_NAMES.get(f[2], str(f[2])), mic_count=f[3],
            uptime_ms=f[4], sample_rate=f[5], spacing_m=f[6],
            rms_dbfs=(f[7], f[8]), peak_dbfs=(f[9], f[10]),
            i2s_overruns=f[11], tx_dropped=f[12],
            fw_version=f[13].split(b"\0", 1)[0].decode(errors="replace"),
        )
    if msg_type == MSG_BEARING and len(payload) == _BEARING.size:
        f = _BEARING.unpack(payload)
        return Bearing(
            node_id=f[0], detected=bool(f[1] & FLAG_DETECTED), linear=bool(f[1] & FLAG_LINEAR),
            seq=f[3], t_ms=f[4], theta_deg=f[5], elev_deg=f[6],
            coherence=f[7], level_dbfs=f[8], tau_us=f[9],
        )
    if msg_type == MSG_AUDIO and len(payload) >= _AUDIO_HDR.size:
        node_id, channels, n_frames, seq, first = _AUDIO_HDR.unpack_from(payload)
        body = payload[_AUDIO_HDR.size:]
        if channels == 0 or len(body) != n_frames * channels * 3:
            return None
        b = np.frombuffer(body, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)          # sign-extend 24 -> 32 bit
        samples = v.astype(np.int32).reshape(n_frames, channels)
        return Audio(node_id=node_id, seq=seq, first_sample=first, samples=samples)
    if msg_type == MSG_LOG:
        return Log(payload.decode(errors="replace"))
    return None


class FrameParser:
    """Incremental parser. Feed it raw serial bytes, iterate decoded messages.

    Bytes that aren't part of a valid frame (boot ROM text, line noise) are skipped.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0
        self.skipped_bytes = 0

    def feed(self, data: bytes) -> Iterator[Message]:
        self._buf += data
        while True:
            start = self._buf.find(MAGIC)
            if start < 0:
                keep = 1 if self._buf.endswith(MAGIC[:1]) else 0
                self.skipped_bytes += len(self._buf) - keep
                del self._buf[: len(self._buf) - keep]
                return
            if start:
                self.skipped_bytes += start
                del self._buf[:start]
            if len(self._buf) < 5:
                return
            msg_type = self._buf[2]
            length = self._buf[3] | (self._buf[4] << 8)
            if length > MAX_PAYLOAD:
                del self._buf[:2]
                continue
            total = 5 + length + 2
            if len(self._buf) < total:
                return
            frame = bytes(self._buf[:total])
            crc_rx = frame[-2] | (frame[-1] << 8)
            if crc16_ccitt(frame[2:-2]) != crc_rx:
                self.crc_errors += 1
                del self._buf[:2]
                continue
            del self._buf[:total]
            msg = decode(msg_type, frame[5:-2])
            if msg is not None:
                yield msg


def theta_valid(b: Bearing) -> bool:
    return b.detected and not math.isnan(b.theta_deg)
