"""Permanent listening: the spoken on/off switch, and what to ignore while it's on.

Like spoken approve/reject, switching is decided here in code from the person's own words.
A phrase only counts when everything else said is filler ("Hey Jarvis, I want you to listen to
me from now on"), so "stop listening to that song" or "don't stop listening" stay conversation.

While permanent listening is on the mic opens for any voice, so short scraps and the things
speech-to-text "hears" in background noise ("Thank you.", "you") are dropped, not answered.
"""

from __future__ import annotations

from .wakecheck import normalise

MAX_WORDS = 14
_ON = [
    "listen to me from now on", "listen from now on", "keep listening", "stay listening",
    "keep listening to me", "listen all the time", "always listen", "always be listening",
    "permanent listening on", "permanent listening mode on", "turn on permanent listening",
    "switch on permanent listening", "start permanent listening",
    "turn permanent listening on", "switch permanent listening on",
]
_OFF = [
    "stop listening", "stop listening to me", "permanent listening off",
    "permanent listening mode off", "turn off permanent listening",
    "switch off permanent listening", "stop permanent listening", "end permanent listening",
    "turn permanent listening off", "switch permanent listening off",
    "go back to the wake word", "back to the wake word",
]
_FILLER = {"hey", "ok", "jarvis", "i", "want", "you", "to", "can", "could", "would", "will",
           "please", "sir", "now", "just", "right", "alright", "so", "well", "thanks", "thank",
           "and", "mode", "the", "go", "ahead", "for", "me", "on", "from", "it's", "fine",
           "that's", "all", "is", "like", "need", "okay"}

# Things speech-to-text produces from silence or noise, not from someone talking to Jarvis
_NOISE = {"you", "thank you", "thanks", "thank you very much", "thanks for watching",
          "thank you for watching", "bye", "bye bye", "okay", "ok", "oh", "uh", "um", "hmm",
          "mm", "ah", "yeah", "the", "so", "i", "a", "huh", "subtitles by the amara.org community"}
MIN_WORDS = 2


def _contains(words: list[str], phrase: list[str]) -> int | None:
    n = len(phrase)
    for i in range(len(words) - n + 1):
        if words[i:i + n] == phrase:
            return i
    return None


def _matches(words: list[str], phrases: list[str]) -> bool:
    for p in phrases:
        phrase = normalise(p)
        i = _contains(words, phrase)
        if i is not None and all(w in _FILLER for w in words[:i] + words[i + len(phrase):]):
            return True
    return False


def listen_mode_command(text: str) -> bool | None:
    """True = switch permanent listening on, False = off, None = not a request to switch."""
    words = normalise(text)
    if not words or len(words) > MAX_WORDS:
        return None
    on, off = _matches(words, _ON), _matches(words, _OFF)
    if on == off:          # neither, or a muddle of both
        return None
    return on


def is_noise(text: str) -> bool:
    """Too short or too empty to be someone talking to Jarvis (used while always listening)."""
    words = normalise(text)
    return len(words) < MIN_WORDS or " ".join(words) in _NOISE


def addressed(text: str) -> bool:
    """Does it mention Jarvis by name? (Spoken approve/reject needs it while always listening.)"""
    return "jarvis" in normalise(text)
