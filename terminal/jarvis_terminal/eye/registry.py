"""Node registry: where each mic node is and which way it faces. Persisted as JSON.

Ported from EGH490 acoustic-proto (eyehost/registry.py @ dac085a). Changed for Jarvis: the
default layout is ONE node mounted beside the screen, facing the room.

Room frame (metres): the display is at the origin facing +y (into the room).
+x is to the right when standing BEHIND the screen looking out (the eye's own right), which is
the viewer's left. Angles are degrees counter-clockwise from +x.

Per node:
  x, y      position
  heading   direction the node's arrow (front) points
  m1_side   "left" or "right": which side M1 is on, looking along the arrow.
            If the eye looks the wrong way (you on its left, it looks right), flip this.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class NodePose:
    name: str
    x: float = 0.0
    y: float = 0.0
    heading: float = 90.0
    m1_side: str = "left"


@dataclass
class Display:
    x: float = 0.0
    y: float = 0.0
    heading: float = 90.0


def default_pose(node_id: int) -> NodePose:
    """Where a node is assumed to be the first time it's seen.

    JARVIS_EYE_NODE_OFFSET_M: how far the node sits from the middle of the screen, as you face
    the screen. Negative = to the left (the default, 0.2 m), positive = to the right.
    JARVIS_EYE_NODE_M1_SIDE: "left" or "right" (flip it if the eye looks the wrong way).
    """
    offset = float(os.environ.get("JARVIS_EYE_NODE_OFFSET_M", "-0.2"))
    m1_side = os.environ.get("JARVIS_EYE_NODE_M1_SIDE", "left")
    # The viewer's left is the room's +x, so flip the sign
    return NodePose(f"node {node_id}", x=-offset, y=0.0, heading=90.0, m1_side=m1_side)


@dataclass
class Registry:
    path: Path
    display: Display = field(default_factory=Display)
    nodes: dict[str, NodePose] = field(default_factory=dict)   # keyed by USB serial (MAC)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def load(cls, path: Path) -> "Registry":
        reg = cls(path=path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            reg.display = Display(**data.get("display", {}))
            reg.nodes = {mac: NodePose(**pose) for mac, pose in data.get("nodes", {}).items()}
        return reg

    def save(self) -> None:
        data = {
            "display": asdict(self.display),
            "nodes": {mac: asdict(p) for mac, p in self.nodes.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def ensure(self, mac: str, node_id: int) -> NodePose:
        """Return the pose for a node, adding it with the default pose the first time it's seen."""
        with self._lock:
            pose = self.nodes.get(mac)
            if pose is None:
                pose = default_pose(node_id)
                self.nodes[mac] = pose
                self.save()
            return pose

    def get(self, mac: str) -> NodePose | None:
        with self._lock:
            return self.nodes.get(mac)
