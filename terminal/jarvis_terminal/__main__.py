"""Run a terminal: python -m jarvis_terminal [--brain ws://host:8765/ws] [--no-wake]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys

from jarvis_protocol import load_env


def main() -> None:
    load_env()
    p = argparse.ArgumentParser(prog="jarvis-terminal")
    p.add_argument("--brain", default=os.environ.get("JARVIS_BRAIN_URL", "ws://localhost:8765/ws"))
    p.add_argument("--id", default=os.environ.get("JARVIS_TERMINAL_ID", socket.gethostname()))
    p.add_argument("--ui-port", type=int, default=int(os.environ.get("JARVIS_UI_PORT", "8080")))
    p.add_argument("--wake-threshold", type=float,
                   default=float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5")))
    p.add_argument("--no-wake", action="store_true", help="button / Enter key only")
    p.add_argument("--no-keyboard", action="store_true", help="don't read Enter from the console")
    p.add_argument("--mic", default=None, help="input device name or index (see --list-devices)")
    p.add_argument("--speaker", default=None, help="output device name or index")
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    def device(value):
        return int(value) if value is not None and value.isdigit() else value

    from .client import Terminal
    terminal = Terminal(
        brain_url=args.brain, terminal_id=args.id, ui_port=args.ui_port,
        wake_threshold=args.wake_threshold, use_wake=not args.no_wake,
        mic_device=device(args.mic), speaker_device=device(args.speaker),
    )
    keyboard = not args.no_keyboard and sys.stdin is not None and sys.stdin.isatty()
    if keyboard:
        print("Say 'hey Jarvis', tap the screen button, or press Enter to talk. Ctrl+C quits.")
    try:
        asyncio.run(terminal.run(keyboard))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
