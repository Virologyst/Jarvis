"""Mood tags: Claude starts each sentence with one, e.g. "[amused] Very good, Sir."

They drive the face on screen (the Arc theme's eyes), and are never spoken or shown as text.
"""

from __future__ import annotations

import re

EMOTIONS = ("neutral", "statement", "happy", "amused", "question", "thinking",
            "sad", "concerned", "surprised", "excited")

_TAG = re.compile(r"\[\s*([a-zA-Z]+)\s*\]\s*")


def parse_sentence(sentence: str) -> tuple[str, str]:
    """'[amused] Ha. [question] Really?' -> ('amused', 'Ha. Really?').

    The first known tag wins; unknown tags are dropped too. Without a tag the mood is guessed
    from the punctuation: questions squint, everything else is a statement.
    """
    emotion = None
    for m in _TAG.finditer(sentence):
        name = m.group(1).lower()
        if name in EMOTIONS:
            emotion = emotion or name
    clean = " ".join(_TAG.sub(" ", sentence).split())
    if emotion is None:
        emotion = "question" if clean.rstrip().endswith("?") else "statement"
    return emotion, clean


class TagStripper:
    """Removes [tags] from streamed text pieces, holding back a possibly-unfinished one."""

    MAX_TAG = 14

    def __init__(self):
        self.pending = ""

    def feed(self, piece: str) -> str:
        text = self.pending + piece
        self.pending = ""
        out = []
        i = 0
        while i < len(text):
            if text[i] == "[":
                end = text.find("]", i)
                if end == -1:
                    if len(text) - i <= self.MAX_TAG:
                        self.pending = text[i:]   # might be a tag still arriving
                        break
                    out.append(text[i])           # too long to be a tag: it's just a bracket
                elif end - i <= self.MAX_TAG and text[i + 1:end].strip().isalpha():
                    i = end + 1
                    while i < len(text) and text[i] == " " and (not out or out[-1].endswith(" ")):
                        i += 1                    # no double space where the tag was
                    continue
                else:
                    out.append(text[i])
            else:
                out.append(text[i])
            i += 1
        return "".join(out)

    def flush(self) -> str:
        rest, self.pending = self.pending, ""
        return rest
