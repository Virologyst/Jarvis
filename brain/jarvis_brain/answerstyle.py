"""Pointed answers: how much Jarvis says, per screen.

Brief is the default and lives in the system prompt (answer first, no preamble). Saying "be
brief" or "give me the detail" (on its own, not part of a question) switches the screen it's said
to; that's decided here in code, like permanent listening, and remembered across restarts in
data/answer-style.json. In detailed mode each turn carries a short note asking for fuller answers.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

BRIEF, DETAILED = "brief", "detailed"
DETAILED_NOTE = ("\n\n(Answer style on this screen: detailed. Sir wants fuller answers: the "
                 "answer first, then the reasoning, options and specifics he'd want, still "
                 "spoken sentences with no lists.)")

_WAKE = re.compile(r"^(hey|ok|okay)?\s*(jarvis|j)\b[\s,.!?]*", re.I)
_PLEASE = re.compile(r"\b(please|thanks|thank you|from now on|for now|again)\b", re.I)
_BRIEF = {"be brief", "brief", "brief mode", "keep it brief", "keep it short", "short answers",
          "shorter answers", "pointed answers", "get to the point", "be concise", "less detail",
          "brief answers", "quick answers"}
_DETAILED = {"give me the detail", "give me the details", "detail", "details", "detail mode",
             "detailed mode", "detailed answers", "more detail", "more detailed answers",
             "longer answers", "full answers", "go into detail", "give me more detail"}


def style_command(text: str) -> str | None:
    """BRIEF or DETAILED if what was said is just a switch ("Jarvis, be brief please"), else None
    ("give me more detail on the quote" is a request, not a switch)."""
    words = _WAKE.sub("", text.strip())
    words = _PLEASE.sub("", words)
    words = " ".join(re.sub(r"[^\w\s']", " ", words.lower()).split())
    if words in _BRIEF:
        return BRIEF
    if words in _DETAILED:
        return DETAILED
    return None


class AnswerStyles:
    """Each terminal's answer style, saved across restarts."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.choices: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.choices = {}

    def get(self, terminal_id: str) -> str:
        return DETAILED if self.choices.get(terminal_id) == DETAILED else BRIEF

    def set(self, terminal_id: str, style: str) -> str:
        """Switch a screen; returns what Jarvis says about it."""
        before = self.get(terminal_id)
        self.choices[terminal_id] = style
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.choices, indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("couldn't save answer style: %s", e)
        if style == BRIEF:
            return ("[statement] Already keeping it brief, Sir." if before == BRIEF
                    else "[statement] Brief it is, Sir.")
        return ("[statement] You're already getting the detail, Sir." if before == DETAILED
                else "[statement] I'll give you the detail, Sir.")
