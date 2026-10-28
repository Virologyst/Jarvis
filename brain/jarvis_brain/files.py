"""Read-only access to files on the brain's own C: drive, mainly for Craig's Obsidian vault.

The reading itself (and every check on paths) is shared with the terminals, in
jarvis_protocol.fileaccess. This adds what's private on the brain machine: Jarvis's own checkout
(settings, keys, task data, the approval and upgrade machinery) and the upgrade worktrees.
Nothing here writes; writing on the brain is filewrite.py, behind approval cards.
"""

from __future__ import annotations

from pathlib import Path

from jarvis_protocol.fileaccess import (  # noqa: F401 - re-exported for the rest of the brain
    BINARY_KINDS, LIST_LIMIT, MAX_FILE_BYTES, PRIVATE, READ_LIMIT, SEARCH_RESULTS,
    SEARCH_SECONDS, TEXT_EXTENSIONS, FileAccessError,
)
from jarvis_protocol.fileaccess import FileReader as _SharedReader

from .config import REPO_ROOT


def jarvis_private_paths() -> list[Path]:
    """Jarvis's own checkout and its upgrade worktrees on this machine."""
    return [REPO_ROOT, REPO_ROOT.parent / "jarvis-upgrades"]


class FileReader(_SharedReader):
    """The shared reader, with Jarvis's own checkout and upgrade worktrees excluded by default."""

    def __init__(self, roots: list[Path] | None = None, excluded: list[Path] | None = None,
                 home: Path | None = None):
        super().__init__(roots, jarvis_private_paths() if excluded is None else excluded, home)
