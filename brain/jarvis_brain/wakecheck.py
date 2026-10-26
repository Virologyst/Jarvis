"""Does a transcript contain one of the wake phrases?

The terminal's wake model is only trained on "hey Jarvis". It also half-fires on other phrases
ending in "Jarvis", so the terminal sends those borderline moments here and speech-to-text
decides. JARVIS_WAKE_PHRASES (|-separated) sets the list.
"""

from __future__ import annotations

import os
import re

DEFAULT_PHRASES = "hey jarvis|ok jarvis|good morning jarvis|are you awake jarvis"

# Common speech-to-text spellings of the same words
_SAME = {
    "okay": "ok", "o.k.": "ok", "k": "ok",
    "jarvis's": "jarvis", "jervis": "jarvis", "javis": "jarvis", "jarvas": "jarvis",
    "jarves": "jarvis", "travis": "jarvis",
    "mornin": "morning",
}


def normalise(text: str) -> list[str]:
    words = re.sub(r"[^a-z0-9'.\s]", " ", text.lower()).split()
    out = []
    for w in words:
        w = w.strip(".'")
        out.append(_SAME.get(w, w))
    return [w for w in out if w]


def phrases_from_env() -> list[list[str]]:
    raw = os.environ.get("JARVIS_WAKE_PHRASES", DEFAULT_PHRASES)
    return [normalise(p) for p in raw.split("|") if p.strip()]


def heard_wake_phrase(text: str, phrases: list[list[str]] | None = None) -> bool:
    """True if the words of any wake phrase appear, in order and next to each other."""
    words = normalise(text)
    for phrase in phrases if phrases is not None else phrases_from_env():
        n = len(phrase)
        if any(words[i:i + n] == phrase for i in range(len(words) - n + 1)):
            return True
    return False
