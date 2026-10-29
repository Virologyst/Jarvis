"""Lets the brain read this terminal's own drive (e.g. Craig's Obsidian vault on his PC).

The brain sends FileRequest messages; this answers them with the same read-only reader and
checks the brain uses (jarvis_protocol.fileaccess), applied here, on the machine whose files
they are. Jarvis's checkout on this terminal (its .env, data) and any upgrade worktrees are
private, as are SSH keys, credential stores, browser data and password files.

Only the operations in TerminalFiles.ops are answered; anything else is refused. Writing will come
later as new ops here, each one only ever sent by the brain after an approval card.
Off on machines without a C: drive (a Pi), and with --no-files / JARVIS_TERMINAL_FILES=0.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from jarvis_protocol.fileaccess import FileAccessError, FileReader

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TerminalFiles:
    def __init__(self, reader: FileReader | None = None):
        self.reader = reader or FileReader(excluded=[REPO_ROOT, REPO_ROOT.parent / "jarvis-upgrades"])
        # op -> handler(args) -> text. Read-only, every one.
        self.ops: dict[str, Callable[[dict], str]] = {
            "list_directory": lambda a: self.reader.list_directory(str(a.get("path", ""))),
            "read_file": lambda a: self.reader.read_file(str(a.get("path", "")),
                                                         int(a.get("start") or 0)),
            "search_files": lambda a: self.reader.search_files(
                str(a.get("query", "")), a.get("root") or None, bool(a.get("contents"))),
        }

    @property
    def available(self) -> bool:
        return bool(self.reader.roots)

    def handle(self, op: str, args: dict) -> tuple[bool, str]:
        """(ok, text) for a FileRequest. Never raises."""
        handler = self.ops.get(op)
        if handler is None or not self.available:
            return False, "This terminal doesn't allow that; it only lets me read its files."
        log.info("brain reads files here: %s %s", op,
                 (args or {}).get("path") or (args or {}).get("query") or "")
        try:
            return True, handler(args if isinstance(args, dict) else {})
        except FileAccessError as e:
            return False, str(e)
        except (OSError, ValueError, TypeError) as e:
            log.warning("file request %s failed: %s", op, e)
            return False, "Something went wrong reading that on the terminal."
