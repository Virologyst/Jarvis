"""Tracker geometry: source position -> node bearings -> tracker -> same direction/position.

Ported from EGH490 acoustic-proto (host/tests/test_tracker.py @ dac085a), with Jarvis's
one-node-beside-the-screen layout and the mute switch added. Run with pytest or directly.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from jarvis_terminal.eye import _MutableTracker
from jarvis_terminal.eye.registry import NodePose, Registry, default_pose
from jarvis_terminal.eye.tracker import Tracker

# EGH490's two-node demo layout: screen between two nodes 1 m apart, angled in
TWO_NODES = {
    1: NodePose("node 1", x=-0.5, y=0.0, heading=45.0),
    2: NodePose("node 2", x=0.5, y=0.0, heading=135.0),
}


def theta_for(pose: NodePose, sx: float, sy: float) -> float:
    """The bearing a node at `pose` should report for a source at (sx, sy)."""
    ang = math.degrees(math.atan2(sy - pose.y, sx - pose.x))
    s = 1.0 if pose.m1_side == "left" else -1.0
    diff = (ang - pose.heading + 180.0) % 360.0 - 180.0
    return 90.0 - diff * s


def make_tracker(poses: dict[int, NodePose], cls=Tracker) -> Tracker:
    reg = Registry.load(Path(tempfile.mkdtemp()) / "nodes.json")
    for nid, pose in poses.items():
        reg.nodes[f"N{nid}"] = pose
    tr = cls(reg)
    for nid in poses:
        tr.heartbeat(f"N{nid}", nid)
    return tr


def feed(tr: Tracker, poses: dict[int, NodePose], sx: float, sy: float) -> None:
    for _ in range(5):
        for nid, pose in poses.items():
            tr.bearing(f"N{nid}", theta_for(pose, sx, sy), -40.0, True)


def test_two_nodes() -> None:
    for sx, sy in [(0.0, 2.0), (1.0, 1.5), (-1.5, 3.0), (0.3, 0.8)]:
        tr = make_tracker(TWO_NODES)
        feed(tr, TWO_NODES, sx, sy)
        snap = tr.snapshot()
        t = snap["target"]
        assert snap["state"] == "tracking" and t["mode"] == "position", snap
        assert abs(t["x"] - sx) < 0.02 and abs(t["y"] - sy) < 0.02, (t, sx, sy)
        want_az = math.degrees(math.atan2(sx, sy))     # display at origin facing +y; + = eye's right (+x)
        assert abs(snap["gaze"]["az"] - want_az) < 0.5, (snap["gaze"], want_az)


def test_one_node_beside_screen(monkeypatch) -> None:
    """Jarvis layout: one node 0.2 m to the left of the screen (as you face it), facing the room.

    With one node the range is a guess (2 m), so check the eye turns the right way and roughly
    the right amount for people standing 2 m out at different angles.
    """
    monkeypatch.setenv("JARVIS_EYE_NODE_OFFSET_M", "-0.2")
    pose = default_pose(1)
    assert pose.x == 0.2 and pose.heading == 90.0          # viewer's left = room +x
    poses = {1: pose}
    for deg in (-50, -20, 0, 20, 50):                       # + = eye's right = viewer's left
        sx, sy = 2.0 * math.sin(math.radians(deg)), 2.0 * math.cos(math.radians(deg))
        tr = make_tracker(poses)
        feed(tr, poses, sx, sy)
        snap = tr.snapshot()
        assert snap["state"] == "tracking" and snap["target"]["mode"] == "bearing", snap
        assert abs(snap["gaze"]["az"] - deg) < 6.0, (snap["gaze"], deg)


def test_offset_sign(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_EYE_NODE_OFFSET_M", "0.3")      # node to the RIGHT as you face it
    assert default_pose(1).x == -0.3


def test_states() -> None:
    tr = make_tracker({1: default_pose(1)})
    assert tr.snapshot()["state"] in ("searching", "idle")
    tr2 = Tracker(Registry.load(Path(tempfile.mkdtemp()) / "n.json"))
    assert tr2.snapshot()["state"] == "no_nodes"


def test_dropout_holds_range() -> None:
    """Node 2 goes quiet after a fix: target stays near the true point, no jump to 2 m."""
    tr = make_tracker(TWO_NODES)
    feed(tr, TWO_NODES, 0.3, 0.8)
    tr.snapshot()
    tr.nodes["N2"].bearings.clear()
    for _ in range(5):
        tr.bearing("N1", theta_for(TWO_NODES[1], 0.3, 0.8), -40.0, True)
    t = tr.snapshot()["target"]
    assert abs(t["x"] - 0.3) < 0.05 and abs(t["y"] - 0.8) < 0.05, t


def test_mute_ignores_bearings() -> None:
    """While Jarvis talks, its own voice must not pull the eye."""
    poses = {1: default_pose(1)}
    tr = make_tracker(poses, cls=_MutableTracker)
    tr.set_muted(True)
    feed(tr, poses, 1.0, 1.5)
    assert tr.snapshot()["state"] != "tracking"
    tr.set_muted(False)
    feed(tr, poses, 1.0, 1.5)
    assert tr.snapshot()["state"] == "tracking"


def test_registry_saves_new_node(tmp_path) -> None:
    reg = Registry.load(tmp_path / "eye-nodes.json")
    reg.ensure("44:B1:76:A8:DC:08", 1)
    again = Registry.load(tmp_path / "eye-nodes.json")
    assert "44:B1:76:A8:DC:08" in again.nodes
