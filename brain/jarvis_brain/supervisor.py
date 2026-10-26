"""Keep the brain running, and undo an upgrade that breaks it.

    python -m jarvis_brain.supervisor [brain args...]

Runs `python -m jarvis_brain` as a child and watches it:
- exit code 75 (RESTART_EXIT_CODE): an upgrade was applied, start again on the new code;
- after an upgrade (data/upgrades/pending_health.json exists) the supervisor itself polls the
  brain's /health. Answering clears the marker. Dying first, or not answering within
  HEALTH_TIMEOUT_S, resets the code to the commit from before the upgrade, leaves a note and
  starts again. The check lives here, in a protected file, so an upgrade can't fake it;
- any other crash: start again after a pause, backing off if it keeps crashing;
- exit code 0 or Ctrl+C: stop.

This file is protected: an upgrade can't change how it gets rolled back.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .selfupgrade import HEALTH_MARKER, RESTART_EXIT_CODE, ROLLBACK_NOTE

REPO = Path(__file__).resolve().parents[2]
UPGRADES = REPO / "data" / "upgrades"
HEALTH_TIMEOUT_S = 180   # an upgraded brain must answer /health within this, or it's rolled back
log = logging.getLogger("jarvis_brain.supervisor")


def rollback_if_unhealthy() -> bool:
    marker = UPGRADES / HEALTH_MARKER
    if not marker.exists():
        return False
    info = json.loads(marker.read_text(encoding="utf-8"))
    log.error("upgrade %s never came up healthy: rolling back to %s", info.get("id"), info.get("prev"))
    subprocess.run(["git", "reset", "--hard", info["prev"]], cwd=REPO, check=False)
    marker.unlink()
    (UPGRADES / ROLLBACK_NOTE).write_text(json.dumps({**info, "rolled_back_at": time.time()}),
                                          encoding="utf-8")
    history = UPGRADES / "history.json"
    try:
        entries = json.loads(history.read_text(encoding="utf-8"))
        history.write_text(json.dumps([e for e in entries if e.get("id") != info.get("id")], indent=2),
                           encoding="utf-8")
    except (OSError, ValueError):
        pass
    return True


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def run_child(cmd: list[str], env: dict, port: int) -> int:
    """Run the brain until it exits. While an upgrade is on probation, check it comes up."""
    started = time.time()
    proc = subprocess.Popen(cmd, cwd=REPO, env=env)
    try:
        while True:
            try:
                return proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            marker = UPGRADES / HEALTH_MARKER
            if not marker.exists():
                continue
            if healthy(port):
                info = json.loads(marker.read_text(encoding="utf-8"))
                marker.unlink()
                log.info("upgrade %s is up and healthy; keeping it", info.get("id"))
            elif time.time() - started > HEALTH_TIMEOUT_S:
                log.error("upgraded brain not healthy after %d s; stopping it", HEALTH_TIMEOUT_S)
                proc.kill()
                proc.wait()
                return -1
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        raise


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    env = {**os.environ, "JARVIS_SUPERVISED": "1"}
    port = int(os.environ.get("JARVIS_PORT", "8765"))
    cmd = [sys.executable, "-m", "jarvis_brain", *sys.argv[1:]]
    crashes: list[float] = []
    while True:
        log.info("starting brain")
        try:
            rc = run_child(cmd, env, port)
        except KeyboardInterrupt:
            return
        if rc == 0:
            log.info("brain stopped")
            return
        if rc == RESTART_EXIT_CODE:
            log.info("brain asked to restart (upgrade applied)")
            continue
        if rollback_if_unhealthy():
            continue
        now = time.time()
        crashes = [t for t in crashes if now - t < 600] + [now]
        delay = min(5 * 2 ** (len(crashes) - 1), 300)
        log.warning("brain exited with code %s; restarting in %d s", rc, delay)
        time.sleep(delay)


if __name__ == "__main__":
    main()
