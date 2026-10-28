"""Read-only access to files on the C: drive, mainly for Craig's Obsidian vault.

Three things only: list a folder, read a text file, search by name (and optionally contents).
Nothing here creates, writes, renames, moves or deletes anything: files are only ever opened
for reading ("rb"), so these tools need no approval card.

Every path is resolved first (symlinks, junctions, "..", 8.3 short names) and checked against
the allowed roots (the C: drive) and the exclusions, both before and after resolving, so a link
can't lead somewhere hidden. Excluded: Jarvis's own checkout (settings, keys, task data, the
approval and upgrade machinery), the upgrade worktrees, and common secret stores (SSH keys,
browser profiles, Windows credential stores, .env files, key files, password databases).
Excluded files and folders are left out of listings and searches as if they weren't there.
"""

from __future__ import annotations

import fnmatch
import json
import os
import time
from pathlib import Path, PurePath

from .config import REPO_ROOT

READ_LIMIT = 12_000          # characters returned per read_file call
MAX_FILE_BYTES = 20_000_000  # won't read text files bigger than this at all
SEARCH_RESULTS = 25
SEARCH_SECONDS = 8.0
CONTENT_MAX_BYTES = 2_000_000  # content search skips files bigger than this
LIST_LIMIT = 200

# Folders never shown or entered, by name, anywhere on the drive
EXCLUDED_DIR_NAMES = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".claude", ".anthropic", ".git",
    ".password-store", "$recycle.bin", "system volume information",
}
# Runs of folder names never entered, wherever they appear in a path (lower case)
EXCLUDED_FRAGMENTS = [
    ("appdata", "roaming", "microsoft", "credentials"),
    ("appdata", "local", "microsoft", "credentials"),
    ("appdata", "roaming", "microsoft", "protect"),
    ("appdata", "roaming", "microsoft", "crypto"),
    ("appdata", "local", "microsoft", "vault"),
    ("appdata", "roaming", "microsoft", "vault"),
    ("appdata", "local", "google", "chrome", "user data"),
    ("appdata", "local", "microsoft", "edge", "user data"),
    ("appdata", "local", "bravesoftware"),
    ("appdata", "roaming", "opera software"),
    ("appdata", "roaming", "mozilla", "firefox", "profiles"),
    ("appdata", "local", "packages"),
    ("appdata", "roaming", "claude"),
    ("appdata", "roaming", "gnupg"),
    ("appdata", "roaming", "github cli"),
    ("appdata", "local", "github cli"),
    (".config", "gh"),
    (".config", "anthropic"),
    ("windows", "system32", "config"),
    ("programdata", "microsoft", "crypto"),
]
# File names never read or shown (lower case, shell patterns)
EXCLUDED_FILE_PATTERNS = [
    ".env", ".env.*", "*.env", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.pem", "*.key",
    "*.pfx", "*.p12", "*.ppk", "*.kdbx", "*.keychain", ".netrc", "_netrc", ".git-credentials",
    ".npmrc", ".pypirc", "credentials", "credentials.*", "*credentials*.json", "secrets.*",
    "*secret*.json", "known_hosts", "authorized_keys", "login data", "cookies", "web data",
    "key3.db", "key4.db", "logins.json", "ntuser.dat*", "pagefile.sys", "hiberfil.sys",
    "swapfile.sys",
]

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".canvas", ".json", ".csv", ".tsv", ".log", ".ini",
    ".cfg", ".conf", ".toml", ".yaml", ".yml", ".xml", ".html", ".htm", ".css", ".js", ".ts",
    ".py", ".ps1", ".bat", ".cmd", ".sh", ".sql", ".rtf", ".tex", ".org", ".rst", ".srt",
}
BINARY_KINDS = {
    ".pdf": "a PDF", ".docx": "a Word document", ".doc": "a Word document",
    ".xlsx": "an Excel spreadsheet", ".xls": "an Excel spreadsheet",
    ".pptx": "a PowerPoint presentation", ".png": "an image", ".jpg": "an image",
    ".jpeg": "an image", ".gif": "an image", ".webp": "an image", ".bmp": "an image",
    ".svg": "an image", ".mp3": "an audio file", ".wav": "an audio file", ".m4a": "an audio file",
    ".mp4": "a video", ".mov": "a video", ".mkv": "a video", ".zip": "a zip archive",
    ".7z": "an archive", ".rar": "an archive", ".exe": "a program", ".dll": "a program library",
    ".msi": "an installer", ".stl": "a 3D model", ".3mf": "a 3D model", ".gcode": "printer G-code",
}


class FileAccessError(Exception):
    """A polite refusal or problem, said to the person as it is."""


def _drive_root() -> list[Path]:
    return [Path("C:/")] if os.name == "nt" else []


class FileReader:
    """Read-only file access confined to `roots`, with exclusions. Never writes anything."""

    def __init__(self, roots: list[Path] | None = None, excluded: list[Path] | None = None,
                 home: Path | None = None):
        self.roots = [Path(os.path.realpath(r)) for r in (_drive_root() if roots is None else roots)]
        if excluded is None:
            excluded = [REPO_ROOT, REPO_ROOT.parent / "jarvis-upgrades"]
        self.excluded = [Path(os.path.realpath(p)) for p in excluded]
        self.home = home

    # --- path checks -----------------------------------------------------------------------

    def _excluded_name(self, parts: tuple[str, ...], is_file: bool) -> bool:
        low = [p.lower().rstrip("\\/") for p in parts]
        folders = low[:-1] if is_file else low
        if any(p in EXCLUDED_DIR_NAMES for p in folders):
            return True
        for frag in EXCLUDED_FRAGMENTS:
            n = len(frag)
            if any(tuple(low[i:i + n]) == frag for i in range(len(low) - n + 1)):
                return True
        if is_file and low:
            name = low[-1]
            if any(fnmatch.fnmatchcase(name, pat) for pat in EXCLUDED_FILE_PATTERNS):
                return True
        return False

    def _excluded(self, path: Path, is_file: bool) -> bool:
        if self._excluded_name(PurePath(path).parts, is_file):
            return True
        return any(_within(path, ex) for ex in self.excluded)

    def _allowed_root(self, path: Path) -> bool:
        return any(_within(path, r) for r in self.roots)

    def resolve(self, raw: str, *, want: str | None = None) -> Path:
        """The real path for `raw`, or FileAccessError if it's outside bounds or excluded.

        want: "file" or "dir" to insist on one.
        """
        text = (raw or "").strip().strip('"').strip("'")
        if not text:
            raise FileAccessError("Which file or folder? I need a path.")
        if text.startswith("~"):
            text = str((self.home or Path.home())) + text[1:]
        if text.startswith(("\\\\", "//")):
            raise FileAccessError("I can only read files on the C drive, not network paths.")
        # A colon anywhere but after the drive letter means an alternate data stream: refuse it
        after_drive = text[2:] if text[1:2] == ":" else text
        if ":" in after_drive:
            raise FileAccessError("That path doesn't look right; I can only read plain paths.")
        given = Path(text)
        if not given.is_absolute():
            raise FileAccessError("Please give me the full path, starting from the C drive.")
        normal = Path(os.path.normpath(given))
        if not self.roots or not self._allowed_root(normal):
            raise FileAccessError("I can only read files on the C drive.")
        # Folders checked before resolving, file names after (once it's known to be a file)
        if self._excluded(normal, is_file=False):
            raise FileAccessError(_PRIVATE)
        try:
            real = normal.resolve(strict=True)
        except (OSError, RuntimeError):
            raise FileAccessError(f"I can't find {normal}.") from None
        if not self._allowed_root(real):
            raise FileAccessError("That leads outside the C drive, so I can't read it.")
        is_file = real.is_file()
        if self._excluded(real, is_file=is_file) or self._excluded_name(normal.parts, is_file):
            raise FileAccessError(_PRIVATE)
        if want == "file" and not is_file:
            raise FileAccessError(f"{real} is a folder, not a file. I can list it instead.")
        if want == "dir" and not real.is_dir():
            raise FileAccessError(f"{real} is a file, not a folder. I can read it instead.")
        return real

    def _visible(self, entry: Path) -> bool:
        """For listings and searches: a child that may be shown (links checked where they lead)."""
        try:
            is_file = entry.is_file()
            if self._excluded(entry, is_file=is_file):
                return False
            if entry.is_symlink() or _is_junction(entry):
                real = entry.resolve(strict=True)
                return self._allowed_root(real) and not self._excluded(real, is_file=real.is_file())
            return True
        except (OSError, RuntimeError):
            return False

    # --- the three tools -------------------------------------------------------------------

    def list_directory(self, raw: str) -> str:
        folder = self.resolve(raw, want="dir")
        try:
            children = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            raise FileAccessError(f"I can't open {folder}: {e.strerror or e}.") from None
        dirs, files = [], []
        for child in children:
            if not self._visible(child):
                continue
            try:
                if child.is_dir():
                    dirs.append(child.name + "\\")
                else:
                    files.append(f"{child.name} ({_size(child.stat().st_size)})")
            except OSError:
                continue
        total = len(dirs) + len(files)
        if not total:
            return f"{folder} is empty (or holds nothing I'm allowed to show)."
        shown = (dirs + files)[:LIST_LIMIT]
        more = f"\n(and {total - len(shown)} more not shown)" if total > len(shown) else ""
        return (f"{folder}: {len(dirs)} folders, {len(files)} files.\n" + "\n".join(shown) + more)

    def read_file(self, raw: str, start: int = 0) -> str:
        path = self.resolve(raw, want="file")
        size = path.stat().st_size
        ext = path.suffix.lower()
        if ext in BINARY_KINDS:
            return (f"{path.name} is {BINARY_KINDS[ext]} ({_size(size)}), not a text file, so I "
                    "can't read it out. I can tell you about the folder it's in instead.")
        if size > MAX_FILE_BYTES:
            return f"{path.name} is {_size(size)}, too big for me to read."
        with open(path, "rb") as f:          # read-only, always
            data = f.read()
        if _looks_binary(data) and ext not in TEXT_EXTENSIONS:
            return (f"{path.name} looks like a binary file ({_size(size)}), not text, so I can't "
                    "read it out.")
        text = _decode(data)
        if ext == ".canvas":
            text = _describe_canvas(text) or text
        start = max(0, int(start or 0))
        if start >= len(text) and text:
            return f"That's past the end of {path.name}; it's {len(text)} characters long."
        part = text[start:start + READ_LIMIT]
        end = start + len(part)
        header = f"{path} ({_size(size)}, modified {_modified(path)})\n"
        if start or end < len(text):
            header += f"[Showing characters {start} to {end} of {len(text)}."
            header += (f" Use start {end} to read on.]\n" if end < len(text) else "]\n")
        return header + part

    def search_files(self, query: str, root: str | None = None,
                     contents: bool = False) -> str:
        query = (query or "").strip()
        if not query:
            raise FileAccessError("What should I search for?")
        base = self.resolve(root or str(self.home or Path.home()), want="dir")
        words = query.lower().split()
        deadline = time.monotonic() + SEARCH_SECONDS
        by_name, by_text, timed_out = [], [], False
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            if time.monotonic() > deadline:
                timed_out = True
                break
            here = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith(".") and d.lower() not in _NOISY
                                 and self._visible(here / d))
            for name in sorted(filenames):
                if len(by_name) + len(by_text) >= SEARCH_RESULTS:
                    break
                path = here / name
                if not self._visible(path):
                    continue
                if all(w in name.lower() for w in words):
                    by_name.append(path)
                elif contents and _content_match(path, words):
                    by_text.append(path)
            if len(by_name) + len(by_text) >= SEARCH_RESULTS:
                break
        if not by_name and not by_text:
            where = "names or contents" if contents else "names"
            note = " (I stopped early: it's a big folder, so try a narrower one)" if timed_out else ""
            return f"No files under {base} with '{query}' in their {where}{note}."
        lines = [f"Search under {base} for '{query}':"]
        if by_name:
            lines.append("Name matches:")
            lines += [f"  {p}" for p in by_name]
        if by_text:
            lines.append("Text matches:")
            lines += [f"  {p}" for p in by_text]
        if len(by_name) + len(by_text) >= SEARCH_RESULTS:
            lines.append(f"(Stopped at {SEARCH_RESULTS} results; narrow it down for more.)")
        elif timed_out:
            lines.append("(Stopped early: it's a big folder, so there may be more.)")
        return "\n".join(lines)


_PRIVATE = ("That's somewhere private (Jarvis's own settings and machinery, or keys, passwords "
            "and credentials), so I'm not allowed to read it.")
# Folders a search skips: huge and never what anyone means
_NOISY = {"node_modules", "__pycache__", ".venv", "venv", "windows", "program files",
          "program files (x86)", "programdata", "appdata", "$windows.~bt", "$windows.~ws"}


def _within(path: Path, root: Path) -> bool:
    p, r = os.path.normcase(str(path)), os.path.normcase(str(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _is_junction(path: Path) -> bool:
    try:
        return bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return False


def _looks_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def _decode(data: bytes) -> str:
    encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _content_match(path: Path, words: list[str]) -> bool:
    ext = path.suffix.lower()
    if ext not in TEXT_EXTENSIONS:
        return False
    try:
        if path.stat().st_size > CONTENT_MAX_BYTES:
            return False
        with open(path, "rb") as f:          # read-only, always
            text = _decode(f.read()).lower()
    except OSError:
        return False
    return all(w in text for w in words)


def _describe_canvas(text: str) -> str | None:
    """An Obsidian canvas (mind map) as readable lines: its cards, then how they connect."""
    try:
        canvas = json.loads(text)
    except ValueError:
        return None
    if not isinstance(canvas, dict):
        return None
    nodes = [n for n in canvas.get("nodes") or [] if isinstance(n, dict)]
    labels = {}
    lines = ["Obsidian canvas (mind map). Cards:"]
    for n in nodes:
        kind = n.get("type")
        if kind == "text":
            label = str(n.get("text", "")).strip()
        elif kind == "file":
            label = "note " + str(n.get("file", ""))
        elif kind == "link":
            label = "link " + str(n.get("url", ""))
        elif kind == "group":
            label = "group " + str(n.get("label", "")).strip()
        else:
            label = str(kind)
        labels[n.get("id")] = label.splitlines()[0][:80] if label else "(empty)"
        lines.append("- " + label.replace("\n", " / "))
    edges = [e for e in canvas.get("edges") or [] if isinstance(e, dict)]
    if edges:
        lines.append("Connections:")
        for e in edges:
            a, b = labels.get(e.get("fromNode"), "?"), labels.get(e.get("toNode"), "?")
            tag = f" ({e['label']})" if e.get("label") else ""
            lines.append(f"- {a} -> {b}{tag}")
    return "\n".join(lines)


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"


def _modified(path: Path) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
