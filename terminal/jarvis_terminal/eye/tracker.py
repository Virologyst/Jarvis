"""Bearings -> target. One node gives a direction; two or more give a point.

Ported unchanged from EGH490 acoustic-proto (eyehost/tracker.py @ dac085a).

Each node's recent detected bearings are median-filtered (outlier rejection), turned into a
ray in the room frame, and the rays are intersected by least squares. The result is smoothed
and converted to a gaze direction from the display.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .registry import NodePose, Registry

WINDOW_S = 0.4          # bearings older than this don't count towards the current estimate
MIN_DETECTIONS = 3      # within the window, to trust a node's bearing
TRACK_HOLD_S = 0.6      # no fresh estimate for longer than this -> searching
IDLE_AFTER_S = 8.0      # searching for longer than this -> idle
STALE_LINK_S = 2.0      # no heartbeat for this long -> node shown as disconnected
BEARING_ONLY_RANGE_M = 2.0   # with one node, assume the source is this far along the ray
RANGE_HOLD_S = 2.0           # after a position fix, keep using its range if a node briefly drops out
MAX_RANGE_M = 25.0
SMOOTH_TAU_S = 0.25


@dataclass
class NodeState:
    mac: str
    node_id: int = 0
    connected: bool = True
    last_heartbeat: float = 0.0
    bearings: deque = field(default_factory=lambda: deque(maxlen=64))   # (t, theta_deg, level_dbfs)
    theta: float | None = None
    level: float = -120.0


def node_ray(pose: NodePose, theta_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Origin and unit direction (room frame) of the ray from a node towards the sound."""
    s = 1.0 if pose.m1_side == "left" else -1.0
    a = math.radians(pose.heading + (90.0 - theta_deg) * s)
    return np.array([pose.x, pose.y]), np.array([math.cos(a), math.sin(a)])


def intersect_rays(rays: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray | None:
    """Least-squares point closest to all rays; None if they're near-parallel or meet behind a node."""
    A = np.zeros((2, 2))
    b = np.zeros(2)
    for p, d in rays:
        P = np.eye(2) - np.outer(d, d)
        A += P
        b += P @ p
    if abs(np.linalg.det(A)) < 0.05:          # rays within a few degrees of parallel
        return None
    x = np.linalg.solve(A, b)
    for p, d in rays:
        if np.dot(x - p, d) <= 0:              # the point must be in front of every node
            return None
    if np.linalg.norm(x) > MAX_RANGE_M:
        return None
    return x


class Tracker:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.nodes: dict[str, NodeState] = {}
        self._lock = threading.Lock()
        self._target: np.ndarray | None = None
        self._target_mode = "none"
        self._last_fix = 0.0
        self._last_position = 0.0
        self._last_update = time.monotonic()

    # --- inputs (called from node reader threads) ---------------------------------------
    def heartbeat(self, mac: str, node_id: int) -> None:
        self.registry.ensure(mac, node_id)
        with self._lock:
            st = self.nodes.setdefault(mac, NodeState(mac))
            st.node_id, st.connected, st.last_heartbeat = node_id, True, time.monotonic()

    def bearing(self, mac: str, theta_deg: float, level_dbfs: float, detected: bool) -> None:
        with self._lock:
            st = self.nodes.setdefault(mac, NodeState(mac))
            st.level = level_dbfs
            if detected and math.isfinite(theta_deg):
                st.bearings.append((time.monotonic(), theta_deg, level_dbfs))

    def disconnected(self, mac: str) -> None:
        with self._lock:
            if mac in self.nodes:
                self.nodes[mac].connected = False

    # --- output (called by the server at ~30 Hz) ----------------------------------------
    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._lock:
            rays, node_info, levels = [], [], []
            for mac, st in sorted(self.nodes.items(), key=lambda kv: kv[1].node_id):
                if now - st.last_heartbeat > STALE_LINK_S:
                    st.connected = False
                recent = [(t, th, lv) for t, th, lv in st.bearings if now - t < WINDOW_S]
                st.theta = float(np.median([th for _, th, _ in recent])) if len(recent) >= MIN_DETECTIONS else None
                pose = self.registry.get(mac)
                if st.connected and st.theta is not None and pose is not None:
                    rays.append(node_ray(pose, st.theta))
                    levels.append(max(lv for _, _, lv in recent))
                node_info.append({
                    "mac": mac, "id": st.node_id, "name": pose.name if pose else mac,
                    "connected": st.connected, "detecting": st.theta is not None,
                    "theta": st.theta, "level": round(st.level, 1),
                })

            fix, mode = None, "none"
            if len(rays) >= 2:
                fix = intersect_rays(rays)
                mode = "position" if fix is not None else "none"
            if fix is not None:
                self._last_position = now
            elif rays:
                p, d = rays[0]
                if self._target is not None and now - self._last_position < RANGE_HOLD_S:
                    # A node dropped out for a moment: follow the remaining bearing at the last
                    # known range instead of jumping to the nominal range
                    rng = max(0.2, float(np.dot(self._target - p, d)))
                    fix, mode = p + rng * d, "position"
                else:
                    # Direction only: put the source a nominal distance along the ray
                    fix, mode = p + BEARING_ONLY_RANGE_M * d, "bearing"

            dt = now - self._last_update
            self._last_update = now
            if fix is not None:
                if self._target is None or now - self._last_fix > TRACK_HOLD_S:
                    self._target = fix
                else:
                    k = 1.0 - math.exp(-dt / SMOOTH_TAU_S)
                    self._target = self._target + k * (fix - self._target)
                self._target_mode, self._last_fix = mode, now

            since_fix = now - self._last_fix
            if not any(n["connected"] for n in node_info):
                state = "no_nodes"
            elif fix is not None or since_fix < TRACK_HOLD_S:
                state = "tracking"
            elif since_fix < IDLE_AFTER_S:
                state = "searching"
            else:
                state = "idle"

            gaze = None
            target = None
            if self._target is not None and state in ("tracking", "searching"):
                disp = self.registry.display
                v = self._target - np.array([disp.x, disp.y])
                # Angle of the target off the display's facing direction; + = the eye's right
                facing = math.radians(disp.heading)
                fwd = np.array([math.cos(facing), math.sin(facing)])
                right = np.array([math.sin(facing), -math.cos(facing)])
                az = math.degrees(math.atan2(np.dot(v, right), np.dot(v, fwd)))
                gaze = {"az": round(az, 1), "dist": round(float(np.linalg.norm(v)), 2)}
                target = {"x": round(float(self._target[0]), 2), "y": round(float(self._target[1]), 2),
                          "mode": self._target_mode}

            return {
                "state": state,
                "gaze": gaze,
                "target": target,
                "level": round(max(levels), 1) if levels else None,
                "nodes": node_info,
            }
