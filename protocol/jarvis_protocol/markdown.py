"""Finding the real headings in a Markdown note, shared by the brain's vault tools and the
terminal's live Obsidian bridge.

A line starting with '#' is only a heading outside fenced code blocks (``` or ~~~, closed by a
fence of the same character at least as long; one never closed runs to the end of the note) and
outside the YAML front matter at the top. So a ```markmap block with '# Portal' or '## Hirers'
in it, or its own '---' lines, never splits a section.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def body_start(lines: list[str]) -> int:
    """The first line after the front matter (0 if the note has none)."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                return i + 1
    return 0


def outside_code(lines: list[str], start: int | None = None) -> Iterator[tuple[int, str]]:
    """(index, line) for the lines from `start` (the end of the front matter if not given) that
    aren't in a fenced code block; the fence lines themselves are left out too."""
    fence = ""
    for i in range(body_start(lines) if start is None else start, len(lines)):
        line = lines[i]
        m = _FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                    and not line[m.end():].strip():
                fence = ""
            continue
        if m and not (m.group(1)[0] == "`" and "`" in line[m.end():]):
            fence = m.group(1)
            continue
        yield i, line


def headings(lines: list[str], start: int | None = None) -> list[tuple[int, int, str]]:
    """(line index, level, title) for each real heading."""
    out = []
    for i, line in outside_code(lines, start):
        m = HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out
