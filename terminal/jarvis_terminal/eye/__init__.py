"""The eye: an ESP32-S3 mic node on USB tells the terminal which way a sound came from, and the
touchscreen eye looks that way.

registry, tracker, sources and nodeproto are ported from EGH490 acoustic-proto (@ dac085a);
the node firmware is in firmware/node. This module is the Jarvis glue around them.
"""
from __future__ import annotations

import logging
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
    """The EGH490 tracker, plus a mute switch."""

    def __init__(self, registry: Registry) -> None:
        super().__init__(registry)
        self._muted = False

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            if muted and not self._muted:
                for st in self.nodes.values():
                    st.bearings.clear()
            self._muted = muted

    def bearing(self, mac: str, theta_deg: float, level_dbfs: float, detected: bool) -> None:
        if self._muted:
            detected = False
        super().bearing(mac, theta_deg, level_dbfs, detected)
