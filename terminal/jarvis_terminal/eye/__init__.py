"""The eye: an ESP32-S3 mic node on USB tells the terminal which way a sound came from, and the
touchscreen eye looks that way.

registry, tracker, sources and nodeproto are ported from EGH490 acoustic-proto (@ dac085a);
the node firmware is in firmware/node. This module is the Jarvis glue around them.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .registry import Registry
from .tracker import Tracker

log = logging.getLogger(__name__)


class Eye:
    """Tracker + its bearing source. Call snapshot() for the eye's state (~20-30 Hz)."""

    def __init__(self, config: Path, simulate: bool = False) -> None:
        self.tracker = _MutableTracker(Registry.load(config))
        self.simulate = simulate

    def start(self) -> None:
        if self.simulate:
            from .sources import Simulator
            Simulator(self.tracker).start()
            log.info("eye: simulated node")
        else:
            try:
                from .sources import NodeManager
            except ImportError as e:   # pyserial missing
                log.warning("eye disabled: %s", e)
                return
            NodeManager(self.tracker).start()
            log.info("eye: watching USB for mic nodes")

    def mute(self, muted: bool) -> None:
        """Ignore bearings while Jarvis is talking, so the eye doesn't turn to its own speaker."""
        self.tracker.set_muted(muted)

    def snapshot(self) -> dict:
        return self.tracker.snapshot()


class _MutableTracker(Tracker):
    """The EGH490 tracker, plus a mute switch and a loudness gate.

    The node flags almost any sound as "detected", including the background hum of fans and PCs
    (measured: 96% of frames in a quiet office at ~-72 dBFS). The gate keeps a running estimate of
    each node's background level and only lets a bearing through when the sound is clearly louder
    than that, so the eye follows people talking instead of the air-con.
    """

    def __init__(self, registry: Registry, margin_db: float | None = None) -> None:
        super().__init__(registry)
        self._muted = False
        self.margin_db = margin_db if margin_db is not None else float(
            os.environ.get("JARVIS_EYE_MARGIN_DB", "12"))
        self._floor: dict[str, float] = {}

    def _loud_enough(self, mac: str, level_dbfs: float) -> bool:
        """Track the background level (falls fast, rises slowly) and compare against it."""
        floor = self._floor.get(mac, level_dbfs)
        if level_dbfs < floor:
            floor += 0.3 * (level_dbfs - floor)      # quiet: follow down quickly
        else:
            floor += 0.002 * (level_dbfs - floor)    # loud: creep up only if it lasts (new hum)
        self._floor[mac] = floor
        return level_dbfs >= floor + self.margin_db

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            if muted and not self._muted:
                for st in self.nodes.values():
                    st.bearings.clear()
            self._muted = muted

    def bearing(self, mac: str, theta_deg: float, level_dbfs: float, detected: bool) -> None:
        loud = self._loud_enough(mac, level_dbfs)
        if self._muted or not loud:
            detected = False
        super().bearing(mac, theta_deg, level_dbfs, detected)
