"""Sir's Obsidian vault: projects, their tasks, progress and logs, and any other note.

The vault is a folder of Markdown notes (JARVIS_VAULT in .env). A project is a note whose
properties say `type: project`; new ones go in Projects/. The rules, also written in the vault's
Success Map so Sir and Jarvis follow the same ones:

- properties: status (idea, planned, active, on hold, done), progress (0-100), next, updated
- tasks are checkboxes; #j marks one Jarvis may do or chase
- ## Log: one line per change, newest first. Jarvis adds a line for every change it makes.

Sir edits these notes in Obsidian at the same time, so every call reads the file fresh, changes
only the lines it must, and writes the file back in one step. Deleting a task or a passage (only
when Sir asks) is planned here and made only after an approval card, and is logged like any other
change; nothing deletes a whole note. Only notes found inside the vault are read or written.
Results are worded for Jarvis to say aloud; bracketed parts are for Jarvis.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis_protocol.progress import refresh_bar
from jarvis_protocol.routines import RoutineError, mark_done

from .clock import HOME_ZONE

log = logging.getLogger(__name__)

PROJECTS_DIR = "Projects"
ROUTINES_NOTE = "Projects/Me"      # where Sir keeps his Daily, Weekly and Monthly routines
TEMPLATE = Path("Templates") / "Project.md"
STATUSES = ("idea", "planned", "active", "on hold", "done")
J_TAG = "#j"
MAX_READ_CHARS = 6000
_SKIP_DIRS = {".obsidian", ".trash", ".git"}
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_TASK = re.compile(r"^(\s*)[-*+]\s+\[(.)\](?:\s(.*))?$")
_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_STOP = {"the", "a", "an", "to", "for", "my", "of", "and", "in", "on", "task", "project", "note",
         "with", "it", "that", "this", "one"}
_BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]')
_PLACEHOLDER = "What it is and what"     # first line of the Project template's description

_DEFAULT_TEMPLATE = """---
type: project
status: planned
progress: 0
next:
started: {{date}}
updated: {{date}}
tags:
  - project
---
# {{title}}

## Next up

## Tasks

## Notes

## Log
- {{date}}: Project started.
"""


class VaultError(ValueError):
    """Something to tell the person (not set up, no such note, which task?)."""


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("’", ""))) - _STOP


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _yaml_scalar(value: str) -> str:
    if re.search(r"[:#\[\]{},&*!|>'\"%@`]", value) or value[:1] in "-?" or \
            value.lower() in {"yes", "no", "true", "false", "null", "on", "off", "~"}:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _indent_width(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


# --- Properties (the YAML block at the top of a note) -------------------------------------------

def _front_bounds(lines: list[str]) -> tuple[int, int] | None:
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return 0, i
    return None


def read_properties(lines: list[str]) -> dict[str, str | list[str]]:
    """Top-level properties: plain values and lists. Nested settings are skipped."""
    bounds = _front_bounds(lines)
    props: dict[str, str | list[str]] = {}
    if not bounds:
        return props
    key = None
    for line in lines[bounds[0] + 1:bounds[1]]:
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                props[key] = [_unquote(v) for v in value[1:-1].split(",") if v.strip()]
            else:
                props[key] = [] if value == "" else _unquote(value)
        elif key and isinstance(props.get(key), list) and re.match(r"^\s+-\s+", line):
            props[key].append(_unquote(line.strip()[1:]))  # type: ignore[union-attr]
    return {k: ("" if v == [] else v) for k, v in props.items()}


def set_property(lines: list[str], key: str, value: str) -> list[str]:
    """Set one top-level property, leaving the rest of the block exactly as it was."""
    new = f"{key}: {_yaml_scalar(value)}" if value else f"{key}:"
    bounds = _front_bounds(lines)
    if not bounds:
        return ["---", new, "---", *lines]
    start, end = bounds
    for i in range(start + 1, end):
        if re.match(rf"^{re.escape(key)}:", lines[i]):
            j = i + 1
            while j < end and re.match(r"^\s+\S", lines[j]):   # an old list or nested value
                j += 1
            return lines[:i] + [new] + lines[j:]
    return lines[:end] + [new] + lines[end:]


def _redraw_bar(lines: list[str]) -> list[str]:
    """Keep the note's coloured progress bar (if it has one) in step with its progress."""
    text = "\n".join(lines)
    redrawn = refresh_bar(text, read_properties(lines).get("progress") or 0)
    return redrawn.split("\n") if redrawn != text else lines


# --- Headings, tasks and bullets -----------------------------------------------------------------

def _body_start(lines: list[str]) -> int:
    bounds = _front_bounds(lines)
    return bounds[1] + 1 if bounds else 0


def _outside_code(lines: list[str]):
    """(index, line) for lines of the body that aren't inside ``` code blocks."""
    in_code = False
    for i in range(_body_start(lines), len(lines)):
        if lines[i].lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            yield i, lines[i]


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    out = []
    for i, line in _outside_code(lines):
        m = _HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


@dataclass
class Section:
    line: int        # the heading's line
    level: int
    title: str
    end: int         # where the whole section (subheadings included) ends
    own_end: int     # where its own text ends (the first subheading, or end)


def find_section(lines: list[str], name: str, exact: bool = False) -> Section | None:
    heads = _headings(lines)
    k = _key(name)
    found = next((h for h in heads if _key(h[2]) == k), None)
    if found is None and not exact and k:
        found = next((h for h in heads if k in _key(h[2])), None)
    if found is None:
        return None
    i, level, title = found
    end = next((h[0] for h in heads if h[0] > i and h[1] <= level), len(lines))
    own_end = next((h[0] for h in heads if i < h[0] < end), end)
    return Section(i, level, title, end, own_end)


@dataclass
class TaskLine:
    number: int
    index: int
    indent: str
    done: bool
    text: str
    section: str

    @property
    def for_j(self) -> bool:
        return J_TAG in self.text.lower().split()


def find_tasks(lines: list[str]) -> list[TaskLine]:
    out: list[TaskLine] = []
    section = ""
    for i, line in _outside_code(lines):
        m = _HEADING.match(line)
        if m:
            section = m.group(2).strip()
            continue
        m = _TASK.match(line)
        if m:
            out.append(TaskLine(len(out) + 1, i, m.group(1), m.group(2) in "xX",
                                (m.group(3) or "").strip(), section))
    return out


def _end_of_children(lines: list[str], index: int, stop: int) -> int:
    """The line after `index` and everything indented under it."""
    width = _indent_width(lines[index])
    j = index + 1
    while j < stop and lines[j].strip() and _indent_width(lines[j]) > width:
        j += 1
    return j


def _end_of_text(lines: list[str], start: int, stop: int) -> int:
    """Where to add a line to a heading's own text: after its last non-blank line."""
    j = stop
    while j > start and not lines[j - 1].strip():
        j -= 1
    return j


def _match_one(candidates: list, ref: str, names: Callable, what: str, describe: Callable):
    """Pick the candidate `ref` means: exact name, then all its words, then most words."""
    k, want = _key(ref), _words(ref)
    exact = [c for c in candidates if any(_key(n) == k for n in names(c))]
    if exact:
        return exact
    if want:
        full = [c for c in candidates if any(want <= _words(n) for n in names(c))]
        if full:
            return full
    if k and len(k) >= 3:
        part = [c for c in candidates if any(k in _key(n) for n in names(c))]
        if part:
            return part
    if want:
        scored = [(max(len(want & _words(n)) for n in names(c)), c) for c in candidates]
        best = max((s for s, _ in scored), default=0)
        if best:
            return [c for s, c in scored if s == best]
    raise VaultError(f"I can't find {what} called '{ref}'. {describe()}")


@dataclass
class Deletion:
    """A deletion planned but not made: title and body go on the approval card, and apply()
    (run only once it's approved) makes it, raising VaultError if the note changed meanwhile."""
    title: str
    body: str
    apply: Callable[[], str]


@dataclass
class _Spot:
    """One place a deletion could be: a heading's section (lines a to b) or an exact passage
    (characters a to b of the note's body, after its properties)."""
    whole_lines: bool
    a: int
    b: int
    label: str

    def cut(self, lines: list[str]) -> tuple[list[str], str]:
        """(the note without it, what goes). A passage that fills whole lines takes them too."""
        if self.whole_lines:
            return lines[:self.a] + lines[self.b:], "\n".join(lines[self.a:self.b])
        start = _body_start(lines)
        body = "\n".join(lines[start:])
        a, b = self.a, self.b
        gone = body[a:b]
        if (a == 0 or body[a - 1] == "\n") and (b == len(body) or body[b] == "\n"):
            if b < len(body):
                b += 1                          # its line break too
            elif a > 0:
                a -= 1                          # the note's last lines: the break before them
        kept = body[:a] + body[b:]
        return lines[:start] + (kept.split("\n") if kept else []), gone

    def glimpse(self, lines: list[str]) -> str:
        """Where it is, to tell copies apart."""
        if self.whole_lines:
            inside = [ln.strip() for ln in lines[self.a + 1:self.b] if ln.strip()]
            return f"line {self.a + 1}, " + (f"starting '{inside[0][:60]}'" if inside
                                             else "nothing under it")
        start = _body_start(lines)
        before = "\n".join(lines[start:])[:self.a]
        return f"line {start + before.count(chr(10)) + 1}"


# --- The vault -------------------------------------------------------------------------------------

class Vault:
    def __init__(self, root: Path | None, now: Callable[[], datetime] | None = None):
        self.root = root.resolve() if root else None
        self._clock = now or (lambda: datetime.now(ZoneInfo(HOME_ZONE)))

    @classmethod
    def from_env(cls) -> "Vault":
        path = os.environ.get("JARVIS_VAULT", "").strip()
        return cls(Path(path).expanduser() if path else None)

    # Files

    def _need(self) -> Path:
        if self.root is None:
            raise VaultError("The vault isn't on the brain PC, so the vault_ tools can't reach it. "
                             "Use the obsidian_ tools instead, which work live in Obsidian on "
                             "Sir's PC: obsidian_new_project for a new project, obsidian_project "
                             "for progress, status and log, obsidian_read and obsidian_write for "
                             "notes and tasks.")
        if not self.root.is_dir():
            raise VaultError(f"I can't reach the notes vault at the moment ({self.root}).")
        return self.root

    def _today(self) -> str:
        return self._clock().astimezone(ZoneInfo(HOME_ZONE)).date().isoformat()

    def _notes(self) -> list[Path]:
        root = self._need()
        return sorted(p for p in root.rglob("*.md")
                      if not _SKIP_DIRS & set(p.relative_to(root).parts[:-1]))

    def _inside(self, path: Path) -> Path:
        root = self._need()
        real = path.resolve()
        if real != root and root not in real.parents:
            raise VaultError("That note is outside the vault.")
        return real

    @staticmethod
    def _read(path: Path) -> tuple[list[str], str]:
        raw = path.read_bytes().decode("utf-8-sig")    # bytes: read_text would hide \r\n
        newline = "\r\n" if "\r\n" in raw else "\n"
        lines = raw.replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        return lines, newline

    def _write(self, path: Path, lines: list[str], newline: str = "\n") -> None:
        path = self._inside(path)
        if path.suffix.lower() != ".md":
            raise VaultError("I only write notes.")
        tmp = path.with_name(f".{path.name}.jarvis-tmp")   # hidden from Obsidian
        tmp.write_bytes((newline.join(lines) + newline).encode("utf-8"))
        os.replace(tmp, path)

    def _name(self, path: Path) -> str:
        return path.stem

    def _names(self, path: Path, props: dict) -> list[str]:
        aliases = props.get("aliases") or props.get("alias") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        rel = path.relative_to(self._need()).with_suffix("").as_posix()
        return [path.stem, rel, *aliases]

    # Finding notes

    def _projects(self) -> list[tuple[Path, dict]]:
        out = []
        templates = self._need() / TEMPLATE.parent
        for p in self._notes():
            if templates in p.parents:          # the template itself isn't a project
                continue
            try:
                props = read_properties(self._read(p)[0])
            except (OSError, UnicodeDecodeError):
                continue
            if str(props.get("type", "")).lower() == "project":
                out.append((p, props))
        return out

    def _find(self, name: str, projects_only: bool = False) -> Path:
        name = _one_line(name).removesuffix(".md")
        if not name:
            raise VaultError("Which note?")
        if projects_only:
            pool = self._projects()
        else:
            pool = []
            for p in self._notes():
                try:
                    pool.append((p, read_properties(self._read(p)[0])))
                except (OSError, UnicodeDecodeError):
                    continue
        what = "a project" if projects_only else "a note"

        def describe() -> str:
            projects = [p.stem for p, _ in self._projects()]
            return f"Projects: {', '.join(projects) or 'none yet'}."

        hits = _match_one(pool, name, lambda c: self._names(*c), what, describe)
        if len(hits) > 1:
            # Prefer projects over other notes with the same name
            projects = [h for h in hits if str(h[1].get("type", "")).lower() == "project"]
            hits = projects if len(projects) == 1 else hits
        if len(hits) > 1:
            raise VaultError(f"Did you mean {', or '.join(h[0].stem for h in hits[:5])}?")
        return hits[0][0]

    # Reading

    def projects(self) -> str:
        found = self._projects()
        if not found:
            return ("There are no projects in the vault yet. [Make one with "
                    "vault_new_project.]")
        parts = []
        for path, props in found:
            tasks = find_tasks(self._read(path)[0])
            open_ = {_key(t.text): t for t in tasks if not t.done and t.text}
            mine = sum(1 for t in open_.values() if t.for_j)
            line = (f"{path.stem}: {props.get('status') or 'no status'}, "
                    f"{props.get('progress') or 0}% done")
            if props.get("next"):
                line += f", next: {props['next']}"
            line += f". {len(open_)} open tasks"
            if mine:
                line += f", {mine} marked for you"
            parts.append(line + ".")
        return f"{len(found)} projects. " + " ".join(parts)

    def read(self, name: str, section: str | None = None) -> str:
        path = self._find(name)
        lines, _ = self._read(path)
        if section:
            s = find_section(lines, section)
            if s is None:
                heads = ", ".join(h[2] for h in _headings(lines)) or "none"
                raise VaultError(f"{path.stem} has no '{section}' heading. Headings: {heads}.")
            return self._cut(f"[{path.stem}, {s.title}]\n" + "\n".join(lines[s.line + 1:s.end]))
        props = read_properties(lines)
        if str(props.get("type", "")).lower() == "project":
            return self._summary(path, lines, props)
        body = "\n".join(lines[_body_start(lines):]).strip()
        return self._cut(f"[{path.stem}]\n{body}")

    @staticmethod
    def _cut(text: str) -> str:
        if len(text) <= MAX_READ_CHARS:
            return text
        return text[:MAX_READ_CHARS] + "\n[Cut short here: ask for one section by its heading.]"

    def _summary(self, path: Path, lines: list[str], props: dict) -> str:
        tasks = find_tasks(lines)
        seen, open_ = set(), []
        for t in tasks:
            if not t.done and t.text and _key(t.text) not in seen:
                seen.add(_key(t.text))
                open_.append(t)
        done = len({_key(t.text) for t in tasks if t.done})
        out = [f"[Project {path.stem}] Status {props.get('status') or 'not set'}, "
               f"{props.get('progress') or 0}% done. Next step: {props.get('next') or 'not set'}. "
               f"Last updated {props.get('updated') or 'unknown'}."]
        body = lines[_body_start(lines):]
        first = next((i for i, ln in enumerate(body)
                      if ln.strip() and not ln.startswith(("#", "-", "|", ">", "```"))), None)
        if first is not None:                   # the first paragraph, however it's wrapped
            para = []
            for ln in body[first:]:
                if not ln.strip() or ln.startswith(("#", "```")):
                    break
                para.append(ln.strip())
            out.append("About: " + " ".join(para))
        if open_:
            out.append(f"Open tasks ({done} done so far):")
            for t in open_:
                mark = " (marked for you)" if t.for_j else ""
                out.append(f"[{t.number}] {t.text}{mark} (under {t.section or 'no heading'})")
        else:
            out.append(f"No open tasks ({done} done).")
        log_section = find_section(lines, "Log", exact=True)
        if log_section:
            entries = [ln.strip()[2:] for ln in lines[log_section.line + 1:log_section.end]
                       if ln.strip().startswith(("- ", "* "))][:5]
            if entries:
                out.append("Recent log: " + " | ".join(entries))
        return self._cut("\n".join(out))

    # Changing

    def _add_log(self, lines: list[str], entry: str) -> list[str]:
        line = f"- {self._today()}: {entry}"
        s = find_section(lines, "Log", exact=True)
        if s is None:
            return [*lines, *([""] if lines and lines[-1].strip() else []), "## Log", line]
        j = s.line + 1
        while j < s.end and not lines[j].strip():
            j += 1
        at = j if j < s.end and _BULLET.match(lines[j]) else s.line + 1
        return lines[:at] + [line] + lines[at:]

    def _save_project(self, path: Path, lines: list[str], newline: str, entries: list[str]) -> None:
        lines = set_property(lines, "updated", self._today())
        lines = _redraw_bar(lines)
        for entry in reversed(entries):      # newest first, in the order they were made
            lines = self._add_log(lines, entry)
        self._write(path, lines, newline)
        log.info("vault: %s: %s", path.stem, "; ".join(entries))

    def _locate_task(self, lines: list[str], ref: str, prefer_open: bool | None = None) -> TaskLine:
        tasks = [t for t in find_tasks(lines) if t.text]
        if not tasks:
            raise VaultError("That note has no tasks yet.")
        ref = _one_line(ref)
        if ref.lstrip("[").rstrip("]").isdigit():
            n = int(ref.lstrip("[").rstrip("]"))
            hit = next((t for t in tasks if t.number == n), None)
            if hit is None:
                raise VaultError(f"There's no task {n} there. [Read the project for the numbers.]")
            return hit
        hits = _match_one(tasks, ref, lambda t: [t.text], "a task", lambda: "")
        if prefer_open is not None and any(t.done != prefer_open for t in hits):
            hits = [t for t in hits if t.done != prefer_open]
        if len({_key(t.text) for t in hits}) > 1:
            options = "; ".join(f"[{t.number}] {t.text}" for t in hits[:6])
            raise VaultError(f"Which one? {options}")
        return hits[0]

    def add_task(self, project: str, text: str, section: str | None = None,
                 under: str | None = None, for_j: bool = False) -> str:
        path = self._find(project, projects_only=True)
        lines, newline = self._read(path)
        text = _one_line(text)
        if not text:
            raise VaultError("What's the task?")
        if for_j and J_TAG not in text.lower().split():
            text += f" {J_TAG}"
        if under:
            parent = self._locate_task(lines, under)
            at = _end_of_children(lines, parent.index, len(lines))
            lines.insert(at, f"{parent.indent}\t- [ ] {text}")
            where = f"under '{parent.text}'"
        else:
            s = find_section(lines, section or "Tasks")
            if s is None and section:
                heads = ", ".join(h[2] for h in _headings(lines)) or "none"
                raise VaultError(f"{path.stem} has no '{section}' heading. Headings: {heads}.")
            if s is None:                                    # no Tasks heading: make one
                log_at = find_section(lines, "Log", exact=True)
                at = log_at.line if log_at else len(lines)
                lines[at:at] = ["## Tasks", f"- [ ] {text}", ""]
                where = "under a new Tasks heading"
            else:
                blank = next((i for i in range(s.line + 1, s.own_end)
                              if (m := _TASK.match(lines[i])) and not (m.group(3) or "").strip()),
                             None)
                if blank is not None:                        # the template's empty checkbox
                    lines[blank] = re.sub(r"\[.\]\s*$", "[ ] " + text, lines[blank])
                else:
                    lines.insert(_end_of_text(lines, s.line + 1, s.own_end), f"- [ ] {text}")
                where = f"under {s.title}"
        self._save_project(path, lines, newline, [f"Added task: {text}"])
        return f"Added '{text}' to {path.stem}, {where}."

    def update_task(self, project: str, task: str, status: str | None = None,
                    text: str | None = None) -> str:
        path = self._find(project, projects_only=True)
        lines, newline = self._read(path)
        status = (status or "").strip().lower()
        if status not in ("", "done", "open"):
            raise VaultError("A task can be done or open.")
        text = _one_line(text)
        if not status and not text:
            raise VaultError("What should change about the task?")
        target = self._locate_task(lines, task, prefer_open=(status == "done") if status else None)
        same = [t for t in find_tasks(lines) if _key(t.text) == _key(target.text)]
        entries, said = [], []
        if status and target.done == (status == "done"):
            said.append(f"'{target.text}' is already {'done' if target.done else 'open'}")
        elif status:
            for t in same:
                m = _TASK.match(lines[t.index])
                lines[t.index] = lines[t.index][:m.start(2)] + ("x" if status == "done" else " ") \
                    + lines[t.index][m.end(2):]
            entries.append(("Done: " if status == "done" else "Reopened: ") + target.text)
            said.append(("Ticked off '" if status == "done" else "Reopened '") + target.text + "'")
        if text and text != target.text:
            if target.for_j and J_TAG not in text.lower().split():
                text += f" {J_TAG}"
            for t in same:
                m = _TASK.match(lines[t.index])
                start = m.start(3) if m.group(3) is not None else len(lines[t.index])
                prefix = lines[t.index][:start]
                lines[t.index] = prefix + ("" if prefix.endswith(" ") else " ") + text
            entries.append(f"Renamed task: {target.text} → {text}")
            said.append(f"renamed it to '{text}'")
        if not entries:
            return said[0] + "." if said else "Nothing to change."
        self._save_project(path, lines, newline, entries)
        tasks = find_tasks(lines)
        done = len({_key(t.text) for t in tasks if t.done and t.text})
        total = len({_key(t.text) for t in tasks if t.text})
        progress = read_properties(lines).get("progress") or 0
        return (f"{'; '.join(said)} in {path.stem}. {done} of {total} tasks done; progress is set "
                f"to {progress}%. [Offer to update progress if that looks wrong now.]")

    # Deleting and moving. A deletion is only planned here: the tool puts it on an approval card
    # and apply() makes it, after reading the note fresh and checking the same lines still go.

    def _task_blocks(self, lines: list[str], ref: str, section: str | None
                     ) -> tuple[TaskLine, list[tuple[int, int]]]:
        """A task and where its copies are (line, line after its sub-items). Copies of the same
        task (in "Next up" and "Tasks") are one task, unless a heading picks one."""
        target = self._locate_task(lines, ref)
        copies = [t for t in find_tasks(lines) if _key(t.text) == _key(target.text)]
        if section:
            wanted = _key(section)
            picked = [t for t in copies if _key(t.section) == wanted]
            if not picked:
                where = ", ".join(sorted({t.section or "no heading" for t in copies}))
                raise VaultError(f"'{target.text}' isn't under {section}; it's under {where}.")
            copies = picked
        blocks = [(t.index, _end_of_children(lines, t.index, len(lines))) for t in copies]
        blocks = [b for b in blocks if not any(o[0] < b[0] < o[1] for o in blocks)]  # nested copy
        return target, blocks

    def delete_task(self, project: str, task: str, section: str | None = None) -> "Deletion":
        """Plan deleting a task, its sub-items with it."""
        path = self._find(project, projects_only=True)
        lines, _ = self._read(path)
        target, blocks = self._task_blocks(lines, task, section)
        gone = [lines[a:b] for a, b in blocks]
        where = " and ".join(sorted({t.section or "no heading" for t in find_tasks(lines)
                                     if t.index in {a for a, _ in blocks}}))
        subs = sum(len(g) - 1 for g in gone)
        extra = f", with {subs} sub-item{'s' if subs != 1 else ''}" if subs else ""

        def apply() -> str:
            now, newline = self._read(path)
            _, again = self._task_blocks(now, target.text, section)
            if [now[a:b] for a, b in again] != gone:
                raise VaultError(f"{path.stem} changed since, so I left it alone. "
                                 "[Read it again and ask afresh.]")
            for a, b in sorted(again, reverse=True):
                del now[a:b]
            self._save_project(path, now, newline, [f"Deleted task: {target.text}{extra}"])
            return f"Deleted '{target.text}'{extra} from {path.stem}."

        body = f"From {path.stem}, under {where}:\n" + "\n".join("\n".join(g) for g in gone)
        return Deletion(f"Delete the task '{target.text}' from {path.stem}", body, apply)

    def move_task(self, project: str, task: str, section: str | None = None,
                  under: str | None = None, from_section: str | None = None) -> str:
        """Move a task (with its sub-items) under another heading or beneath another task.
        Nothing is lost, so it runs straight away, logged like the rest."""
        if not section and not under:
            raise VaultError("Where should it go: under which heading or task?")
        path = self._find(project, projects_only=True)
        lines, newline = self._read(path)
        target, blocks = self._task_blocks(lines, task, from_section)
        if len(blocks) > 1:
            heads = ", ".join(sorted({t.section or "no heading" for t in find_tasks(lines)
                                      if t.index in {a for a, _ in blocks}}))
            raise VaultError(f"'{target.text}' is under {heads}. Which one should move?")
        start, end = blocks[0]
        block = lines[start:end]
        rest = lines[:start] + lines[end:]
        if under:
            parent = self._locate_task(rest, under)
            if _key(parent.text) == _key(target.text):
                raise VaultError("A task can't go beneath itself.")
            at, indent = _end_of_children(rest, parent.index, len(rest)), parent.indent + "\t"
            where = f"under '{parent.text}'"
        else:
            s = find_section(rest, section)
            if s is None:
                heads = ", ".join(h[2] for h in _headings(rest)) or "none"
                raise VaultError(f"{path.stem} has no '{section}' heading. Headings: {heads}.")
            at, indent = _end_of_text(rest, s.line + 1, s.own_end), ""
            where = f"under {s.title}"
        base = re.match(r"^\s*", block[0]).group()
        moved = [indent + ln[len(base):] if ln.startswith(base) else indent + ln.lstrip()
                 for ln in block]
        rest[at:at] = moved
        self._save_project(path, rest, newline, [f"Moved task: {target.text} → {where}"])
        return f"Moved '{target.text}' to {where} in {path.stem}."

    def delete_text(self, note: str, text: str | None = None, heading: str | None = None,
                    which: int | None = None) -> "Deletion":
        """Plan deleting an exact passage, or a whole heading with its section. If it appears
        more than once, `which` (1 = first in the note) says which; otherwise nothing is guessed."""
        path = self._find(note)
        lines, _ = self._read(path)
        props = read_properties(lines)
        project = str(props.get("type", "")).lower() == "project"
        found = self._find_passage(path, lines, text, heading, project)
        if len(found) > 1 and which is None:
            options = "; ".join(f"{n}: {spot.glimpse(lines)}"
                                for n, spot in enumerate(found[:6], 1))
            raise VaultError(f"That's in {path.stem} {len(found)} times, so tell me which "
                             f"(1 is the first in the note). {options}")
        if which is not None and not 1 <= which <= len(found):
            raise VaultError(f"It's only there {len(found)} time{'s' if len(found) > 1 else ''}.")
        n = (which or 1) - 1
        label = found[n].label
        gone = found[n].cut(lines)[1]

        def apply() -> str:
            now, newline = self._read(path)
            again = self._find_passage(path, now, text, heading, project)
            if len(again) != len(found) or again[n].cut(now)[1] != gone:
                raise VaultError(f"{path.stem} changed since, so I left it alone. "
                                 "[Read it again and ask afresh.]")
            kept = again[n].cut(now)[0]
            if project:
                self._save_project(path, kept, newline, [f"Deleted {label}"])
            else:
                if read_properties(kept).get("updated"):
                    kept = set_property(kept, "updated", self._today())
                self._write(path, kept, newline)
                log.info("vault: %s: deleted %s", path.stem, label)
            return f"Deleted {label} from {path.stem}."

        return Deletion(f"Delete {label} from {path.stem}",
                        f"From {path.stem}:\n{gone.rstrip()}", apply)

    def _find_passage(self, path: Path, lines: list[str], text: str | None, heading: str | None,
                      project: bool) -> list["_Spot"]:
        """Every place it is, first in the note first."""
        heading, text = _one_line(heading).lstrip("#").strip(), (text or "").replace("\r\n", "\n")
        if heading and text.strip():
            raise VaultError("Give a heading or a passage to delete, not both.")
        if heading:
            heads = _headings(lines)
            hits = [h for h in heads if _key(h[2]) == _key(heading)]
            if not hits:
                names = ", ".join(h[2] for h in heads) or "none"
                raise VaultError(f"{path.stem} has no heading called '{heading}'. "
                                 f"Headings: {names}.")
            if project and _key(heading) == "log":
                raise VaultError("I don't delete a project's log: it's the record of changes.")
            out = []
            for i, level, title in hits:
                # A project's log is never swept up with a section (a stray "# Heading" above it
                # would otherwise hold the "## Log" under it)
                end = next((h[0] for h in heads if h[0] > i and (
                    h[1] <= level or project and _key(h[2]) == "log")), len(lines))
                subs = [h[2] for h in heads if i < h[0] < end]
                label = f"the heading '{title}' and its section" + (
                    f", with {', '.join(subs)} under it" if subs else "")
                out.append(_Spot(True, i, end, label))
            return out
        passage = text.strip("\n")
        if not passage.strip():
            raise VaultError("What should I delete? Give the exact words, or a heading.")
        body = "\n".join(lines[_body_start(lines):])
        spots, at = [], body.find(passage)
        while at >= 0:
            spots.append(at)
            at = body.find(passage, at + 1)
        if not spots:
            raise VaultError(f"I can't find those exact words in {path.stem}. [Read it and copy "
                             "the passage exactly, or give a heading.]")
        short = _one_line(passage)
        label = f"'{short[:80]}{'...' if len(short) > 80 else ''}'"
        return [_Spot(False, s, s + len(passage), label) for s in spots]

    def update_project(self, project: str, status: str | None = None, progress: str | None = None,
                       next_step: str | None = None, log_entry: str | None = None) -> str:
        path = self._find(project, projects_only=True)
        lines, newline = self._read(path)
        props = read_properties(lines)
        entries, said = [], []
        if status:
            want = status.strip().lower().replace("_", " ").replace("-", " ")
            want = {"hold": "on hold", "paused": "on hold", "finished": "done",
                    "complete": "done", "completed": "done", "in progress": "active"}.get(want, want)
            if want not in STATUSES:
                raise VaultError(f"Status can be {', '.join(STATUSES)}.")
            if want != props.get("status"):
                lines = set_property(lines, "status", want)
                entries.append(f"Status: {props.get('status') or 'none'} → {want}")
                said.append(f"status {want}")
        if progress not in (None, ""):
            m = re.search(r"\d+", str(progress))
            if not m:
                raise VaultError("Progress is a percentage, 0 to 100.")
            value = str(max(0, min(100, int(m.group()))))
            if value != str(props.get("progress", "")):
                lines = set_property(lines, "progress", value)
                entries.append(f"Progress: {props.get('progress') or 0}% → {value}%")
                said.append(f"progress {value}%")
        next_step = _one_line(next_step)
        if next_step and next_step != props.get("next"):
            lines = set_property(lines, "next", next_step)
            entries.append(f"Next step: {next_step}")
            said.append(f"next step '{next_step}'")
        log_entry = _one_line(log_entry)
        if log_entry:
            entries.append(log_entry)
            said.append("noted it in the log")
        if not entries:
            return f"Nothing changed in {path.stem}."
        self._save_project(path, lines, newline, entries)
        return f"Updated {path.stem}: {', '.join(said)}."

    def new_project(self, name: str, about: str | None = None, status: str | None = None) -> str:
        root = self._need()
        name = _one_line(_BAD_NAME.sub(" ", name or "")).strip(" .")
        if not name:
            raise VaultError("What's the project called?")
        path = root / PROJECTS_DIR / f"{name}.md"
        if path.exists() or any(p.stem.lower() == name.lower() for p, _ in self._projects()):
            raise VaultError(f"There's already a project called {name}.")
        template = root / TEMPLATE
        text = template.read_text(encoding="utf-8-sig") if template.is_file() else _DEFAULT_TEMPLATE
        today = self._today()
        text = text.replace("{{title}}", name).replace("{{date}}", today) \
            .replace("{{time}}", self._clock().strftime("%H:%M"))
        # The template's "how to fill this in" block is for whoever writes the note by hand
        text = re.sub(r"(?ms)^%%\s*\nHOW TO FILL THIS IN.*?^%%\s*\n", "", text, count=1)
        text = text.replace(" · code: <repo link or folder path>", "")
        lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
        about = _one_line(about)
        if about:
            spot = next((i for i, ln in enumerate(lines) if ln.startswith(_PLACEHOLDER)), None)
            if spot is not None:                   # keep the template's link back to the map
                back = re.search(r"\s*Back to .*\[\[.*$", lines[spot])
                lines[spot] = about + (" " + back.group().strip() if back else "")
            else:
                h1 = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), None)
                at = h1 + 1 if h1 is not None else len(lines)
                lines[at:at] = ["", about]
        if status:
            want = status.strip().lower().replace("_", " ")
            if want not in STATUSES:
                raise VaultError(f"Status can be {', '.join(STATUSES)}.")
            lines = set_property(lines, "status", want)
        if not read_properties(lines).get("type"):
            lines = set_property(lines, "type", "project")
        lines = _redraw_bar(lines)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write(path, lines)
        linked = self._link_from_map(name, about)
        log.info("vault: new project %s", name)
        return (f"Started a project note for {name} in {PROJECTS_DIR}"
                + (", and linked it from the Success Map." if linked else "."))

    def _link_from_map(self, name: str, about: str) -> bool:
        """Add the new project to the Projects list of the vault's map note, if there is one."""
        for p in self._notes():
            lines, newline = self._read(p)
            if str(read_properties(lines).get("type", "")).lower() != "map":
                continue
            s = find_section(lines, "Projects", exact=True)
            if s is None:
                continue
            links = [i for i in range(s.line + 1, s.own_end) if lines[i].lstrip().startswith("- [[")]
            at = links[-1] + 1 if links else _end_of_text(lines, s.line + 1, s.own_end)
            lines.insert(at, f"- [[{name}]]" + (f": {about}" if about else ""))
            self._write(p, lines, newline)
            return True
        return False

    def add_point(self, note: str, heading: str, text: str, under: str | None = None) -> str:
        """Add a bullet (a branch on the note's mind map) under a heading, or under a bullet."""
        path = self._find(note)
        lines, newline = self._read(path)
        text = _one_line(text)
        if not text:
            raise VaultError("What should I add?")
        heading = _one_line(heading)
        s = find_section(lines, heading) if heading else None
        if s is None and heading:                          # a new heading at the end (before Log)
            log_at = find_section(lines, "Log", exact=True)
            at = log_at.line if log_at else len(lines)
            new = [f"## {heading}", f"- {text}", ""]
            if at and lines[at - 1].strip():
                new.insert(0, "")
            lines[at:at] = new
            where = f"under a new heading, {heading}"
        else:
            start, stop = (s.line + 1, s.end) if s else (_body_start(lines), len(lines))
            if under:
                bullets = [(i, _BULLET.match(lines[i])) for i in range(start, stop)
                           if _BULLET.match(lines[i])]
                pool = [(i, m.group(2)) for i, m in bullets]
                hits = _match_one(pool, under, lambda c: [c[1]], "a point", lambda: "")
                if len({_key(h[1]) for h in hits}) > 1:
                    raise VaultError("Which one? " + "; ".join(h[1] for h in hits[:6]))
                i = hits[0][0]
                indent = re.match(r"^\s*", lines[i]).group()
                lines.insert(_end_of_children(lines, i, stop), f"{indent}\t- {text}")
                where = f"under '{hits[0][1]}'"
            else:
                own_end = s.own_end if s else stop
                lines.insert(_end_of_text(lines, start, own_end), f"- {text}")
                where = f"under {s.title}" if s else "at the end"
        props = read_properties(lines)
        if str(props.get("type", "")).lower() == "project":
            self._save_project(path, lines, newline, [f"Added to {heading or 'notes'}: {text}"])
        else:
            if props.get("updated"):
                lines = set_property(lines, "updated", self._today())
            self._write(path, lines, newline)
        return f"Added '{text}' to {path.stem}, {where}."

    # Daily priorities (priorities.py) and Sir's recurring routines (jarvis_protocol.routines)

    def project_notes(self) -> dict[str, str]:
        """Every note in Projects/, read-only: vault path -> text, for the day's priorities."""
        root = self._need()
        folder = root / PROJECTS_DIR
        notes: dict[str, str] = {}
        for p in self._notes():
            if folder not in p.parents:
                continue
            try:
                notes[p.relative_to(root).as_posix()] = "\n".join(self._read(p)[0])
            except (OSError, UnicodeDecodeError):
                continue
        return notes

    def routine_done(self, routine: str, note: str | None = None) -> str:
        """Date one routine as done today (Projects/Me unless another note is named)."""
        path = self._find(note or ROUTINES_NOTE)
        lines, newline = self._read(path)
        try:
            text, said = mark_done("\n".join(lines), routine, date.fromisoformat(self._today()))
        except RoutineError as e:
            raise VaultError(f"{e} (in {path.stem})") from None
        if text != "\n".join(lines):
            self._write(path, text.split("\n"), newline)
        return said
