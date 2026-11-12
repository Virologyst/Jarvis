"""The coloured progress bar in Sir's project notes (Obsidian), shared by the brain's vault tools
and the terminal's live Obsidian bridge so both keep it in step with the note's `progress`.

The bar is one line of HTML (Obsidian shows it in reading view and live preview) marked with
class="jarvis-progress", so it can be found and redrawn: red under a third, amber under two
thirds, green from there, blue when done.
"""

from __future__ import annotations

import re

MARK = 'class="jarvis-progress"'
_BAR_LINE = re.compile(r'^[ \t]*<div class="jarvis-progress".*$', re.M)


def bar_colour(percent: int) -> str:
    if percent >= 100:
        return "#2f81f7"      # done: blue
    if percent >= 67:
        return "#3fb950"      # green
    if percent >= 34:
        return "#d29922"      # amber
    return "#f85149"          # red


def progress_bar(percent) -> str:
    """The bar for a percentage (anything with a number in it; clamped to 0..100)."""
    m = re.search(r"\d+", str(percent or 0))
    p = max(0, min(100, int(m.group()))) if m else 0
    return (f'<div {MARK} title="{p}% done" style="display:flex;align-items:center;gap:10px;'
            f'margin:6px 0 10px"><div style="flex:1;height:14px;border-radius:7px;'
            f'background:rgba(127,127,127,.22);overflow:hidden"><div style="width:{p}%;'
            f'height:100%;border-radius:7px;background:{bar_colour(p)}"></div></div>'
            f'<b style="color:{bar_colour(p)};min-width:3.2em;text-align:right">{p}%</b></div>')


def refresh_bar(text: str, percent) -> str:
    """The note with its bar redrawn for `percent`; unchanged if it has no bar."""
    return _BAR_LINE.sub(lambda _: progress_bar(percent), text, count=1)


def has_bar(text: str) -> bool:
    return bool(_BAR_LINE.search(text))
