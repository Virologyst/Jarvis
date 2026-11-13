"""Sir's recurring routines (chores) in his Obsidian notes, shared by the brain's vault tools and
the terminal's live Obsidian bridge so both read and mark them the same way.

A note (Projects/Me) has a "Routines" heading with Daily, Weekly and Monthly lists under it. The
labels can be subheadings ("### Weekly"), bold or plain lines ("**Weekly**", "Weekly:") or
bullets with the routines nested under them; the routines are bullets or checkboxes:

    ## Routines
    ### Daily
    - Clean the kitchen
    ### Weekly
    - [x] Mow the lawns (last done 2026-11-08)

When Sir says he's done one, Jarvis writes "(last done YYYY-MM-DD)" at the end of its line (and
ticks it, if it's a checkbox). That date is the record: a daily routine is due again the next
day, a weekly one seven days after it was done and a monthly one a calendar month after, so it
comes back at a sensible time and doesn't nag once done for the period. A routine never done is
due. A box ticked by hand with no date counts as done, but nobody knows when, so it's reported
for Jarvis to date it. Nothing else in the note is touched.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

PERIODS = ("daily", "weekly", "monthly")
HEADING = "routines"
_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[(.)\]\s*)?(.*?)\s*$")
_LABEL = re.compile(r"^[*_\s]*(daily|weekly|monthly)(?:\s+(?:routines?|chores|jobs|tasks))?"
                    r"[*_\s]*:?[*_\s]*$", re.I)
_DONE = re.compile(r"\s*\(last done (\d{4}-\d{2}-\d{2})\)|\s*✅\s*(\d{4}-\d{2}-\d{2})", re.I)


class RoutineError(ValueError):
    """Something to tell Sir: no routines section, no such routine, which one."""


@dataclass
class Routine:
    period: str              # daily, weekly or monthly
    name: str                # as written, without the date
    line: int                # index into the note's lines
    last_done: date | None
    ticked: bool = False     # a checkbox that's ticked

    def next_due(self) -> date | None:
        """The first day it's due again; None if it has never been done (so due now)."""
        if self.last_done is None:
            return None
        if self.period == "daily":
            return self.last_done + timedelta(days=1)
        if self.period == "weekly":
            return self.last_done + timedelta(days=7)
        return add_month(self.last_done)

    def is_due(self, day: date) -> bool:
        if self.last_done is None:
            return not self.ticked       # ticked by hand: done, but nobody knows when
        return day >= self.next_due()  # type: ignore[operator]


def add_month(d: date) -> date:
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _width(text: str) -> int:
    return len(text.expandtabs(4))


def _section(lines: list[str]) -> tuple[int, int] | None:
    """(first line, end) of the Routines heading's section, code blocks skipped."""
    start = level = None
    in_code = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        m = None if in_code else _HEAD.match(line)
        if not m:
            continue
        if start is None:
            if re.sub(r"[^a-z]", "", m.group(2).lower()) == HEADING:
                start, level = i + 1, len(m.group(1))
        elif len(m.group(1)) <= level:  # type: ignore[operator]
            return start, i
    return (start, len(lines)) if start is not None else None


def section_bounds(text: str) -> tuple[int, int] | None:
    """Line range of the Routines section (so other readers can leave its checkboxes out)."""
    return _section(text.replace("\r\n", "\n").split("\n"))


def find_routines(text: str) -> list[Routine]:
    lines = text.replace("\r\n", "\n").split("\n")
    bounds = _section(lines)
    if bounds is None:
        return []
    out: list[Routine] = []
    period = None
    label_indent: int | None = None   # a bullet label: its routines are nested deeper
    item_indent: int | None = None
    for i in range(*bounds):
        line = lines[i]
        head = _HEAD.match(line)
        bullet = _BULLET.match(line)
        label_text = head.group(2) if head else (bullet.group(3) if bullet else line)
        label = _LABEL.match(label_text)
        if label:
            period = label.group(1).lower()
            label_indent = _width(bullet.group(1)) if bullet and not head else None
            item_indent = None
            continue
        if head:                       # some other subheading ends the list
            period = None
            continue
        if not bullet or period is None:
            continue
        indent = _width(bullet.group(1))
        if label_indent is not None and indent <= label_indent:
            period = None              # a sibling of the label: the list has ended
            continue
        if item_indent is None or indent < item_indent:
            item_indent = indent
        if indent > item_indent:
            continue                   # a note under a routine, not a routine
        body = bullet.group(3)
        found = _DONE.search(body)
        when = None
        if found:
            try:
                when = date.fromisoformat(found.group(1) or found.group(2))
            except ValueError:
                when = None
        name = _DONE.sub("", body).strip()
        if name:
            out.append(Routine(period, name, i, when, (bullet.group(2) or " ") in "xX"))
    return out


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())
            if w not in {"the", "a", "an", "my", "do", "done", "i", "ve", "have"}]


def pick(routines: list[Routine], ref: str) -> Routine:
    """The routine Sir meant, by its name or part of it."""
    want = _words(ref)
    if not want:
        raise RoutineError("Which routine?")
    exact = [r for r in routines if _words(r.name) == want]
    hits = exact or [r for r in routines if set(want) <= set(_words(r.name))]
    if not hits:
        hits = [r for r in routines
                if len(set(want) & set(_words(r.name))) >= max(1, (len(want) + 1) // 2)]
    if len(hits) == 1:
        return hits[0]
    names = "; ".join(f"{r.name} ({r.period})" for r in (hits or routines)[:10])
    if hits:
        raise RoutineError(f"Several routines match '{ref}': {names}. Which one?")
    raise RoutineError(f"There's no routine like '{ref}'. The routines are: {names or 'none'}.")


def mark_done(text: str, ref: str, day: date) -> tuple[str, str]:
    """(the note with that routine marked done on `day`, what was done). Raises RoutineError."""
    routines = find_routines(text)
    if not routines:
        raise RoutineError("That note has no Routines section with Daily, Weekly or Monthly "
                           "lists.")
    r = pick(routines, ref)
    if r.last_done == day:
        return text, f"{r.name} was already marked done on {day.isoformat()}."
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    line = _DONE.sub("", lines[r.line]).rstrip()
    line = re.sub(r"^(\s*(?:[-*+]|\d+[.)])\s+)\[.\]", r"\1[x]", line, count=1)
    lines[r.line] = f"{line} (last done {day.isoformat()})"
    return newline.join(lines), f"Marked {r.name} ({r.period}) done on {day.isoformat()}."
