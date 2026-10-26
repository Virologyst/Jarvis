"""Bearing sources for the tracker: real nodes on USB, or a simulator.

Ported from EGH490 acoustic-proto (eyehost/sources.py @ dac085a).
"""
from __future__ import annotations

import logging
import math
import random
import threading
import time

import serial
from serial.tools import list_ports

from . import nodeproto as proto
from .tracker import Tracker

log = logging.getLogger("jarvis_terminal.eye.nodes")

NODE_USB_VID = 0x303A    # Espressif native USB (the DevKit's "USB" port)
SCAN_S = 1.0


class NodeReader(threading.Thread):
    """Owns one node's serial port: keeps it in bearing mode, feeds the tracker, exits on unplug."""

    def __init__(self, port: str, mac: str, tracker: Tracker) -> None:
        super().__init__(name=f"node-{port}", daemon=True)
        self.port, self.mac, self.tracker = port, mac, tracker

    def run(self) -> None:
        try:
            ser = serial.Serial()
            ser.port, ser.baudrate, ser.timeout = self.port, 1_500_000, 0.05
            ser.dtr = ser.rts = False          # don't trigger the auto-reset lines
            ser.open()
        except serial.SerialException as e:
            log.warning("cannot open %s: %s", self.port, e)
            return
        log.info("node on %s (%s) connected", self.port, self.mac)
        parser = proto.FrameParser()
        last_cmd = 0.0
        mode_ok = False
        try:
            while True:
                if not mode_ok and time.monotonic() - last_cmd > 1.0:
                    ser.write(b"mode bearing\n")
                    last_cmd = time.monotonic()
                for msg in parser.feed(ser.read(4096)):
                    if isinstance(msg, proto.Heartbeat):
                        self.tracker.heartbeat(self.mac, msg.node_id)
                        mode_ok = msg.mode == "bearing"
                    elif isinstance(msg, proto.Bearing):
                        self.tracker.bearing(self.mac, msg.theta_deg, msg.level_dbfs, msg.detected)
                    elif isinstance(msg, proto.Log):
                        log.debug("[%s] %s", self.mac, msg.text)
        except (serial.SerialException, OSError) as e:
            log.info("node on %s disconnected (%s)", self.port, e)
        finally:
            self.tracker.disconnected(self.mac)
            try:
                ser.close()
            except Exception:
                pass


class NodeManager(threading.Thread):
    """Scans for nodes and starts a reader per port. Handles hot-plug."""

    def __init__(self, tracker: Tracker) -> None:
        super().__init__(name="node-manager", daemon=True)
        self.tracker = tracker
        self.readers: dict[str, NodeReader] = {}

    def run(self) -> None:
        while True:
            for p in list_ports.comports():
                if p.vid != NODE_USB_VID:
                    continue
                r = self.readers.get(p.device)
                if r is None or not r.is_alive():
                    mac = p.serial_number or p.device
                    self.readers[p.device] = NodeReader(p.device, mac, self.tracker)
                    self.readers[p.device].start()
            time.sleep(SCAN_S)


class Simulator(threading.Thread):
    """Fake nodes at their registry poses, hearing a source that wanders in front of the screen.

    Plays 12 s of sound, then 12 s of silence, so the searching and idle states show up too.
    """

    def __init__(self, tracker: Tracker, n_nodes: int = 1, noise_deg: float = 2.0) -> None:
        super().__init__(name="simulator", daemon=True)
        self.tracker, self.n_nodes, self.noise_deg = tracker, n_nodes, noise_deg

    def run(self) -> None:
        macs = {i: f"SIM:{i:02d}" for i in range(1, self.n_nodes + 1)}
        t0 = time.monotonic()
        while True:
            t = time.monotonic() - t0
            for nid, mac in macs.items():
                self.tracker.heartbeat(mac, nid)
            sounding = (t % 24.0) < 12.0
            # Figure-of-eight 1-3 m in front of the screen
            sx = 1.5 * math.sin(0.35 * t)
            sy = 2.0 + 0.8 * math.sin(0.7 * t)
            for nid, mac in macs.items():
                pose = self.tracker.registry.ensure(mac, nid)
                ang = math.degrees(math.atan2(sy - pose.y, sx - pose.x))
                s = 1.0 if pose.m1_side == "left" else -1.0
                diff = (ang - pose.heading + 180.0) % 360.0 - 180.0
                theta = 90.0 - diff * s
                visible = 0.0 <= theta <= 180.0
                self.tracker.bearing(mac, theta + random.gauss(0, self.noise_deg),
                                     -45.0 if sounding else -90.0, sounding and visible)
            time.sleep(1 / 31.25)
