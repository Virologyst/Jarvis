"""JSON control messages. Each message is a dataclass with a `type` tag.

encode(msg) -> str, decode(str) -> message. Unknown types decode to None so an
older terminal can ignore messages added later.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "Hello", "Welcome", "ListenStart", "ListenEnd", "ListenCancel",
    "State", "Transcript", "ReplyText", "AudioStart", "AudioEnd",
    "Card", "Decision", "Error", "encode", "decode",
]

_REGISTRY: dict[str, type] = {}


def _message(type_name: str):
    def wrap(cls):
        cls.TYPE = type_name
        _REGISTRY[type_name] = cls
        return cls
    return wrap


# --- terminal -> brain ---------------------------------------------------

@_message("hello")
@dataclass
class Hello:
    terminal_id: str
    protocol: int = 1


@_message("listen_start")
@dataclass
class ListenStart:
    """Mic audio follows as binary frames. reason: 'wake' | 'button' | 'key'."""
    reason: str = "button"


@_message("listen_end")
@dataclass
class ListenEnd:
    """The user stopped talking; transcribe and answer."""


@_message("listen_cancel")
@dataclass
class ListenCancel:
    """Drop the audio (false wake, nothing said)."""


@_message("decision")
@dataclass
class Decision:
    """The user's answer to a Card. decision: 'approve' | 'reject'."""
    card_id: str
    decision: str


# --- brain -> terminal ---------------------------------------------------

@_message("welcome")
@dataclass
class Welcome:
    name: str = "Jarvis"


@_message("state")
@dataclass
class State:
    """What the brain is doing: 'idle' | 'listening' | 'thinking' | 'speaking'."""
    state: str


@_message("transcript")
@dataclass
class Transcript:
    """What the brain heard the user say."""
    text: str


@_message("reply_text")
@dataclass
class ReplyText:
    """A piece of Jarvis's reply as it is generated. done=True closes the reply."""
    text: str = ""
    done: bool = False


@_message("audio_start")
@dataclass
class AudioStart:
    """Speech audio follows as binary frames at this sample rate."""
    sample_rate: int


@_message("audio_end")
@dataclass
class AudioEnd:
    pass


@_message("card")
@dataclass
class Card:
    """Something for the user to look at or approve on the screen.

    kind: 'info' | 'approval'. For 'approval', nothing happens until the
    terminal sends a Decision with this card_id — the brain enforces that.
    """
    card_id: str
    kind: str
    title: str
    body: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@_message("error")
@dataclass
class Error:
    message: str


# --- encoding ------------------------------------------------------------

def encode(msg) -> str:
    payload = asdict(msg)
    payload["type"] = msg.TYPE
    return json.dumps(payload)


def decode(raw: str):
    data = json.loads(raw)
    cls = _REGISTRY.get(data.pop("type", None))
    if cls is None:
        return None
    known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    return cls(**known)
