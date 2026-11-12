"""Keep this PC's Jarvis terminal running, with no window (run with pythonw.exe).

Started at sign-in by the "Jarvis terminal" scheduled task (scripts/install-terminal.ps1), or by
hand: .venv\\Scripts\\pythonw.exe scripts\\terminal_service.pyw
Runs `python -m jarvis_terminal --no-keyboard` and starts it again if it stops, waiting longer
each time it fails quickly (5 s up to a minute). Output goes to logs/terminal.log. If another
terminal already has the screen's port (8080), it waits for that one to go rather than fight it.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
LOG = REPO / "logs" / "terminal.log"
PORT = int(os.environ.get("JARVIS_UI_PORT", "8080"))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def note(text: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} SERVICE {text}\n")


def port_taken() -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main() -> None:
    wait = 5.0
    note("terminal service started")
    while True:
        if port_taken():
            time.sleep(15)          # another terminal is running; take over when it stops
            continue
        started = time.monotonic()
        with LOG.open("a", encoding="utf-8") as out:
            code = subprocess.call([str(PYTHON), "-m", "jarvis_terminal", "--no-keyboard"],
                                   cwd=REPO, stdout=out, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        ran = time.monotonic() - started
        wait = 5.0 if ran > 120 else min(wait * 2, 60.0)
        note(f"terminal stopped (exit {code}) after {ran:.0f}s; starting again in {wait:.0f}s")
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - say why in the log; there's no window to show it
        note(f"service failed: {e!r}")
        sys.exit(1)
