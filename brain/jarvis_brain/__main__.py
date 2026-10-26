"""Run the brain: python -m jarvis_brain [--host H] [--port P]"""

from __future__ import annotations

import argparse
import logging

from aiohttp import web

from jarvis_protocol import load_env

from .config import Settings


def main() -> None:
    load_env()
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(prog="jarvis-brain")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    settings.host, settings.port = args.host, args.port

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    for noisy in ("httpx2", "httpx", "aiohttp.access", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from .server import build_app  # heavy imports after logging is set up
    app = build_app(settings)
    logging.getLogger("jarvis_brain").info(
        "Jarvis brain listening on ws://%s:%d/ws (model %s, effort %s)",
        settings.host, settings.port, settings.model, settings.effort)
    web.run_app(app, host=settings.host, port=settings.port, print=None)


if __name__ == "__main__":
    main()
