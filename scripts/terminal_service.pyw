"""Keep this PC's Jarvis terminal running, with no window (run with pythonw.exe).

Started at sign-in by the "Jarvis terminal" scheduled task (scripts/install-terminal.ps1), or by
hand: .venv\\Scripts\\pythonw.exe scripts\\terminal_service.pyw
Runs `python -m jarvis_terminal --no-keyboard` and starts it again if it stops, waiting longer
each time it fails quickly (5 s up to a minute). The terminal's output goes to logs/terminal.log,
this service's own notes to logs/terminal-service.log. If another
terminal already has the screen's port (8080), it waits for that one to go rather than fight it.

Once the screen is up it opens it once, in its own Edge (or Chrome) window with no tabs or address
bar, full screen as if F11 was pressed (F11 leaves full screen, Alt+F4 closes it). JARVIS_SCREEN
in .env: fullscreen (default), window, or off.
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
sys.path.insert(0, str(REPO / "protocol"))
try:
    from jarvis_protocol import load_env
    load_env()
except Exception:  # noqa: BLE001 - defaults still work
    pass

PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
LOG = REPO / "logs" / "terminal.log"
SERVICE_LOG = REPO / "logs" / "terminal-service.log"   # its own file: sharing one loses lines
PORT = int(os.environ.get("JARVIS_UI_PORT", "8080"))
SCREEN = os.environ.get("JARVIS_SCREEN", "fullscreen").strip().lower()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BROWSERS = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]


def note(text: str) -> None:
    SERVICE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SERVICE_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")


def port_taken() -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def open_screen() -> None:
    """J's screen in its own browser window. Its own profile folder makes it a separate browser
    process, so the window flags apply even when Edge is already open (and it never touches
    Sir's browsing)."""
    browser = next((b for b in BROWSERS if b.is_file()), None)
    if browser is None:
        note("no Edge or Chrome found; open http://localhost:%d yourself" % PORT)
        return
    args = [str(browser), f"--app=http://localhost:{PORT}",
            f"--user-data-dir={REPO / 'data' / 'screen-browser'}",
            "--no-first-run", "--no-default-browser-check", "--disable-session-crashed-bubble"]
    subprocess.Popen(args, cwd=REPO, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    note(f"opened the screen in {browser.name}")
    if SCREEN == "fullscreen":
        # The browser ignores --start-fullscreen for app windows, so press F11 for Sir
        note("full screen" if press_f11() else "couldn't find the screen's window to go full screen")


def press_f11(title: str = "Jarvis", wait_s: float = 20.0) -> bool:
    """Find the screen's window, bring it forward and press F11, unless it already fills its
    monitor. True if it's full screen afterwards."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    def find() -> int | None:
        hits = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if user32.IsWindowVisible(hwnd) and buf.value == title:
                hits.append(hwnd)
            return True

        user32.EnumWindows(each, 0)
        return hits[0] if hits else None

    def fills_monitor(hwnd) -> bool:
        rect, info = wintypes.RECT(), MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
        m = info.rcMonitor
        return (rect.left, rect.top, rect.right, rect.bottom) == (m.left, m.top, m.right, m.bottom)

    deadline = time.monotonic() + wait_s
    hwnd = None
    while hwnd is None and time.monotonic() < deadline:
        hwnd = find()
        time.sleep(0.5)
    if hwnd is None:
        return False
    time.sleep(1.5)                      # let the page finish opening first
    for _ in range(3):
        if fills_monitor(hwnd):
            return True
        user32.ShowWindow(hwnd, 9)           # SW_RESTORE, in case it's minimised
        # Windows only lets a background program bring a window forward right after a key
        # press, so tap Alt first; and only press F11 once the screen really is in front
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        if user32.GetForegroundWindow() == hwnd:
            user32.keybd_event(0x7A, 0, 0, 0)    # F11 down, up
            user32.keybd_event(0x7A, 0, 2, 0)
            time.sleep(1.2)
        else:
            time.sleep(1.0)
    return fills_monitor(hwnd)


def main() -> None:
    wait = 5.0
    screen_shown = SCREEN in ("off", "0", "no", "none")
    note("terminal service started")
    while True:
        if port_taken():
            time.sleep(15)          # another terminal is running; take over when it stops
            continue
        started = time.monotonic()
        with LOG.open("a", encoding="utf-8") as out:
            proc = subprocess.Popen([str(PYTHON), "-m", "jarvis_terminal", "--no-keyboard"],
                                    cwd=REPO, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            if not screen_shown:    # once per sign-in, as soon as the screen answers
                for _ in range(120):
                    if proc.poll() is not None:
                        break
                    if port_taken():
                        open_screen()
                        screen_shown = True
                        break
                    time.sleep(0.5)
            code = proc.wait()
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
