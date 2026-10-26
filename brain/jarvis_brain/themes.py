"""Screen themes: what the touchscreen looks like, and the voice that goes with each.

The page draws the theme (ui/index.html); the brain decides which one each terminal shows and
speaks with that theme's voice. A terminal's choice is remembered in data/themes.json.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Theme:
    name: str
    title: str
    description: str
    voice: str | None = None        # JARVIS_VOICE-style spec; None = the brain's default voice
    speed: float | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


THEMES = {
    "eye": Theme("eye", "The Eye",
                 "A realistic eye that watches whoever is talking, with the conversation beside it.",
                 aliases=("eyeball", "original", "classic", "default")),
    "arc": Theme("arc", "Arc",
                 "All black with neon-blue lightning; two glowing eyes that show emotion, and the "
                 "conversation fading in from the bottom.",
                 voice="kokoro:bf_lily", speed=1.05,
                 aliases=("lightning", "neon", "blue", "electric", "glowing eyes", "lily")),
}
DEFAULT_THEME = "eye"


def resolve(name: str) -> Theme | None:
    key = " ".join(name.lower().replace("theme", "").split())
    for theme in THEMES.values():
        if key == theme.name or key == theme.title.lower() or key in theme.aliases:
            return theme
    return None


def describe() -> str:
    return " ".join(f"{t.title}: {t.description}" for t in THEMES.values())


class ThemeStore:
    """Which theme each terminal is on, by terminal id, saved across restarts."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.choices: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.choices = {}

    def get(self, terminal_id: str) -> Theme:
        return THEMES.get(self.choices.get(terminal_id, DEFAULT_THEME), THEMES[DEFAULT_THEME])

    def set(self, terminal_id: str, theme: Theme) -> None:
        self.choices[terminal_id] = theme.name
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.choices, indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("couldn't save theme choice: %s", e)
