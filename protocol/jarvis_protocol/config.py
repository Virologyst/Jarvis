"""Tiny .env loader so neither side needs python-dotenv."""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | os.PathLike | None = None) -> None:
    """Load KEY=VALUE lines into os.environ without overriding real env vars.

    Looks for the repo-root .env by default (two levels above this package).
    Empty values are skipped so a blank ANTHROPIC_API_KEY= doesn't mask a
    credential found another way.
    """
    if path is None:
        path = Path(__file__).resolve().parents[2] / ".env"
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value and key not in os.environ:
            os.environ[key] = value
