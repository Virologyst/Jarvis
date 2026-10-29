"""Writing files on the brain's C: drive: create and edit text files, create folders, move and
rename. Nothing here deletes anything.

Each operation is planned first: the checks run and a Plan comes back with what the approval
card shows. tools.py proposes it through the ApprovalGate, and the Plan's apply() runs only once
someone approves it, re-checking everything first (the disk may have changed in between).

Never written, moved or created: everything the reader refuses (Jarvis's own checkout with its
settings, keys, approval and upgrade machinery, the upgrade worktrees, SSH keys, credential
stores, browser data, .env and key files), plus Windows and program folders, AppData, hidden
settings folders (names starting with a dot) and the top of the drive. Only text files can be
written; programs and scripts can't be created, renamed into being or moved.

Written for the brain, but it only needs a FileReader for its path checks, so the same class
can serve a terminal's drive later (as new ops in jarvis_terminal.files, behind the same cards).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePath

from jarvis_protocol.fileaccess import (
    MAX_FILE_BYTES, FileAccessError, FileReader, looks_binary, within,
)

MAX_WRITE_CHARS = 200_000     # text written in one go
PREVIEW_CHARS = 700           # shown on the approval card
MOVE_CHECK_ENTRIES = 5_000    # a folder bigger than this isn't moved (too big to check)
MOVE_CHECK_SECONDS = 5.0

# Files Jarvis may create or change: notes and plain data, never anything that runs
WRITABLE_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".canvas", ".json", ".csv", ".tsv", ".log", ".yaml",
    ".yml", ".xml", ".html", ".htm", ".css", ".rtf", ".tex", ".org", ".rst", ".srt",
}
# Programs and scripts: never moved, renamed into being, or carried along in a folder move
PROGRAM_EXTENSIONS = {
    ".exe", ".dll", ".msi", ".msp", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".lnk", ".url", ".scr", ".com", ".pif", ".cpl", ".hta",
    ".reg", ".py", ".pyw", ".pyc", ".sh", ".jar", ".sys", ".drv", ".ocx", ".inf", ".appx",
    ".msix", ".application", ".gadget", ".settingcontent-ms",
}
# Top-level folders on the drive that belong to Windows and installed programs
SYSTEM_FOLDERS = {
    "windows", "program files", "program files (x86)", "programdata", "recovery", "boot",
    "perflogs", "$windows.~bt", "$windows.~ws", "$recycle.bin", "system volume information",
}

_NOT_HERE = ("That's somewhere I'm not allowed to change: Jarvis's own code, settings and "
             "keys, the approval and upgrade machinery, private places like passwords and SSH "
             "keys, or Windows and program folders.")


@dataclass
class Plan:
    """A checked change, ready for an approval card. apply() does it (checking again first)
    and returns what to say; it never raises, so a failure is just said."""
    title: str
    body: str
    _apply: Callable[[], str]

    def apply(self) -> str:
        try:
            return self._apply()
        except FileAccessError as e:
            return f"I didn't change anything: {e}"
        except OSError as e:
            return f"That didn't work: {e.strerror or e}."


class FileWriter:
    """Plans and makes changes to files inside the reader's roots, never in protected places."""

    def __init__(self, reader: FileReader):
        self.reader = reader

    # --- path checks -----------------------------------------------------------------------

    def _relative(self, path: Path) -> tuple[str, ...]:
        """Path parts below the allowed root it's in (lower case)."""
        for root in self.reader.roots:
            if within(path, root):
                rel = os.path.relpath(path, root)
                return tuple(p.lower() for p in PurePath(rel).parts if p != ".")
        raise FileAccessError("I can only change files on the C drive.")

    def _check(self, path: Path, is_file: bool) -> None:
        """Refuse protected places. Run on the path as given and again once resolved."""
        if self.reader.is_excluded(path, is_file):
            raise FileAccessError(_NOT_HERE)
        rel = self._relative(path)
        if len(rel) < 2:
            raise FileAccessError("I won't change things at the very top of the drive; "
                                  "pick a folder inside it, like Documents.")
        folders = rel[:-1] if is_file else rel
        if rel[0] in SYSTEM_FOLDERS or "appdata" in rel or any(p.startswith(".") for p in folders):
            raise FileAccessError(_NOT_HERE)

    def _target(self, raw: str, is_file: bool) -> tuple[Path, list[str]]:
        """The real path for something that may not exist yet, and the folders that would have
        to be made for it (outermost first; empty if its folder exists)."""
        normal = self.reader.normalise(raw)
        self._check(normal, is_file)
        existing, missing = normal, []
        while not os.path.lexists(existing):
            if existing.parent == existing:
                raise FileAccessError(f"I can't find where {normal} would go.")
            missing.insert(0, existing.name)
            existing = existing.parent
        try:
            base = existing.resolve(strict=True)
        except (OSError, RuntimeError):
            raise FileAccessError(f"I can't find {existing}.") from None
        real = base.joinpath(*missing)
        if not self.reader.allowed_root(real):
            raise FileAccessError("That leads outside the C drive, so I can't change it.")
        self._check(real, is_file)
        if missing and not base.is_dir():
            raise FileAccessError(f"{base} is a file, so nothing can go inside it.")
        new_dirs = [str(base.joinpath(*missing[:i + 1])) for i in range(len(missing) - 1)]
        if not is_file and missing:
            new_dirs.append(str(real))
        return real, new_dirs

    def _text_file(self, raw: str, mode: str) -> tuple[Path, bool, list[str]]:
        path, new_dirs = self._target(raw, is_file=True)
        if path.suffix.lower() not in WRITABLE_EXTENSIONS:
            raise FileAccessError(
                "I can only write text files, like notes (.md), text (.txt), canvas mind maps "
                "or JSON, so give it one of those endings.")
        exists = os.path.lexists(path)
        if exists and not path.is_file():
            raise FileAccessError(f"{path} is a folder, not a file.")
        if mode == "create" and exists:
            raise FileAccessError(f"{path.name} already exists. I can replace it, add to the "
                                  "end of it, or use a different name.")
        if mode != "create" and not exists:
            raise FileAccessError(f"I can't find {path}. I can create it instead.")
        return path, exists, new_dirs

    # --- planning --------------------------------------------------------------------------

    def write_text(self, raw: str, text: str, mode: str = "create") -> Plan:
        """mode: create (a new file), replace (all of an existing one), append (to its end)."""
        mode = mode or "create"
        if mode not in ("create", "replace", "append"):
            raise FileAccessError("I can create a file, replace one, or add to the end of one.")
        if len(text) > MAX_WRITE_CHARS:
            raise FileAccessError(f"That's too much text to write in one go "
                                  f"({len(text)} characters; the limit is {MAX_WRITE_CHARS}).")
        path, exists, new_dirs = self._text_file(raw, mode)
        if exists:
            _read_text(path)          # it must be a text file we can safely rewrite
        title = {"create": "Create", "replace": "Replace", "append": "Add to"}[mode]
        what = {"create": f"Create the file {path}",
                "replace": f"Replace everything in {path} with",
                "append": f"Add to the end of {path}"}[mode]
        if new_dirs:
            what += " (making the folder " + ", ".join(new_dirs) + ")"
        return Plan(f"{title} {path.name}", f"{what}:\n\n{_preview(text)}",
                    lambda: self._write(raw, text, mode))

    def edit_text(self, raw: str, find: str, replace: str) -> Plan:
        """Change one exact piece of an existing text file."""
        if not find:
            raise FileAccessError("Which text should I change? I need the exact words.")
        path, _, _ = self._text_file(raw, "replace")
        old, _ = _read_text(path)
        _replace_once(old, find, replace, path)
        return Plan(f"Edit {path.name}",
                    f"In {path}, change:\n\n{_preview(find, 400)}\n\nto:\n\n{_preview(replace, 400)}",
                    lambda: self._edit(raw, find, replace))

    def create_folder(self, raw: str) -> Plan:
        path, new_dirs = self._target(raw, is_file=False)
        if os.path.lexists(path):
            raise FileAccessError(f"{path} already exists.")
        body = f"Create the folder {path}"
        if len(new_dirs) > 1:
            body += " (and " + ", ".join(new_dirs[:-1]) + ")"
        return Plan(f"Create folder {path.name}", body, lambda: self._make_folder(raw))

    def move(self, raw: str, to: str) -> Plan:
        """Move or rename a file or folder. `to` is the new full path, or a folder to move into."""
        src, dest = self._move_paths(raw, to)
        verb = "Rename" if os.path.normcase(str(src.parent)) == os.path.normcase(str(dest.parent)) else "Move"
        body = (f"Rename {src}\nto {dest.name}" if verb == "Rename" else f"Move {src}\nto {dest}")
        return Plan(f"{verb} {src.name}", body, lambda: self._move(raw, to))

    # --- doing (only from an approved Plan) --------------------------------------------------

    def _write(self, raw: str, text: str, mode: str) -> str:
        path, exists, new_dirs = self._text_file(raw, mode)
        if new_dirs or not path.parent.exists():
            os.makedirs(path.parent, exist_ok=True)
        if mode == "create":
            with open(path, "x", encoding="utf-8", newline="") as f:   # never over a file
                f.write(text)
            return f"Done. I've created {path.name}."
        old, bom = _read_text(path)
        if mode == "append":
            text = old + ("\n" if old and not old.endswith("\n") else "") + text
        _atomic_write(path, text, bom)
        return f"Done. I've {'replaced the text in' if mode == 'replace' else 'added to'} {path.name}."

    def _edit(self, raw: str, find: str, replace: str) -> str:
        path, _, _ = self._text_file(raw, "replace")
        old, bom = _read_text(path)
        _atomic_write(path, _replace_once(old, find, replace, path), bom)
        return f"Done. I've made that change to {path.name}."

    def _make_folder(self, raw: str) -> str:
        path, _ = self._target(raw, is_file=False)
        os.makedirs(path)   # fails if it appeared in the meantime
        return f"Done. I've created the folder {path.name}."

    def _move(self, raw: str, to: str) -> str:
        src, dest = self._move_paths(raw, to)
        os.makedirs(dest.parent, exist_ok=True)
        if os.path.lexists(dest):
            raise FileAccessError(f"there's already something called {dest.name} there.")
        os.rename(src, dest)
        return f"Done. {src.name} is now {dest}."

    # --- moves -------------------------------------------------------------------------------

    def _move_paths(self, raw: str, to: str) -> tuple[Path, Path]:
        src = self.reader.resolve(raw)
        is_file = src.is_file()
        self._check(src, is_file)
        homes = {Path(os.path.realpath(h)) for h in (Path.home(), self.reader.home) if h}
        rel = self._relative(src)
        if (not is_file and (rel[0] == "users" and len(rel) <= 2
                             or any(within(h, src) for h in homes))):
            raise FileAccessError("That's a whole user folder; I won't move that.")
        if is_file and src.suffix.lower() in PROGRAM_EXTENSIONS:
            raise FileAccessError("That's a program or script, and I don't move those.")
        if not (to or "").strip():
            raise FileAccessError("Where should it go? I need the new name or folder.")
        dest, _ = self._target(to, is_file=is_file)
        if dest.is_dir() and not os.path.normcase(str(dest)) == os.path.normcase(str(src)):
            dest, _ = self._target(str(dest / src.name), is_file=is_file)
        if os.path.normcase(str(dest)) == os.path.normcase(str(src)):
            raise FileAccessError("That's where it already is.")
        if os.path.lexists(dest):
            raise FileAccessError(f"There's already something called {dest.name} there.")
        if within(dest, src):
            raise FileAccessError("I can't move a folder inside itself.")
        if is_file and dest.suffix.lower() != src.suffix.lower() \
                and dest.suffix.lower() in PROGRAM_EXTENSIONS:
            raise FileAccessError("I won't rename a file into a program or script.")
        if not is_file:
            self._check_folder_contents(src)
        return src, dest

    def _check_folder_contents(self, folder: Path) -> None:
        """A folder moves only if nothing inside it is private, protected or a program."""
        if any(within(ex, folder) for ex in self.reader.excluded):
            raise FileAccessError(_NOT_HERE)
        seen, deadline = 0, time.monotonic() + MOVE_CHECK_SECONDS
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            here = Path(dirpath)
            for name, is_file in [(d, False) for d in dirnames] + [(f, True) for f in filenames]:
                seen += 1
                if seen > MOVE_CHECK_ENTRIES or time.monotonic() > deadline:
                    raise FileAccessError("That folder is too big for me to check, so I won't "
                                          "move it; it's best done by hand.")
                path = here / name
                if self.reader.is_excluded(path, is_file):
                    raise FileAccessError("That folder has private things inside it, like keys "
                                          "or passwords, so I won't move it.")
                if is_file and path.suffix.lower() in PROGRAM_EXTENSIONS:
                    raise FileAccessError("That folder has programs or scripts inside it, so I "
                                          "won't move it; it's best done by hand.")
                if path.is_symlink() or (not is_file and _is_link_dir(path)):
                    raise FileAccessError("That folder has links to other places inside it, so "
                                          "I won't move it.")


def _is_link_dir(path: Path) -> bool:
    try:
        return bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return False


def _read_text(path: Path) -> tuple[str, bool]:
    """An existing file's text and whether it starts with a byte-order mark, if it's a text
    file that can be rewritten without damaging it."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise FileAccessError(f"{path.name} is too big for me to change.")
    with open(path, "rb") as f:
        data = f.read()
    if looks_binary(data):
        raise FileAccessError(f"{path.name} isn't plain text, so I won't change it.")
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        return data.decode("utf-8-sig"), bom
    except UnicodeDecodeError:
        raise FileAccessError(f"{path.name} uses an unusual text encoding, so I won't change "
                              "it in case I damage it.") from None


def _replace_once(old: str, find: str, replace: str, path: Path) -> str:
    count = old.count(find)
    if not count and "\r\n" in old:   # Windows line endings in the file, not in what was asked
        find, replace = find.replace("\n", "\r\n"), replace.replace("\n", "\r\n")
        count = old.count(find)
    if not count:
        raise FileAccessError(f"I couldn't find that exact text in {path.name}.")
    if count > 1:
        raise FileAccessError(f"That text appears {count} times in {path.name}; give me a bit "
                              "more of it so I change the right one.")
    return old.replace(find, replace, 1)


def _atomic_write(path: Path, text: str, bom: bool) -> None:
    """Write next to the file, then swap it in, so a failure never leaves half a file."""
    tmp = path.with_name(f".{path.name}.jarvis-tmp")
    with open(tmp, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def _preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    if not text:
        return "(nothing)"
    return text if len(text) <= limit else text[:limit] + f"... ({len(text)} characters in all)"
