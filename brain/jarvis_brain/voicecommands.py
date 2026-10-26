"""Spoken "approve" / "reject": handled in code, never by the model.

Claude can't approve anything: a decision only happens when a person's own words, as heard by
speech-to-text, are a short, plain approve or reject. Anything longer or hedged goes to Claude
as normal conversation (and Claude has no tool to approve with).
"""

from __future__ import annotations

from .wakecheck import normalise

MAX_WORDS = 5
_WAKE_PREFIXES = (["hey", "jarvis"], ["ok", "jarvis"], ["jarvis"])
_APPROVE = {"approve", "approved", "approval"}
_REJECT = {"reject", "rejected", "decline", "declined", "deny", "denied"}
_NEGATIONS = {"don't", "dont", "do", "not", "no", "never", "cancel", "stop", "wait", "what",
              "why", "which", "should", "can", "could", "would", "will", "if", "before", "after",
              "but", "and", "or", "the", "email", "how"}
_FILLER = {"yes", "yeah", "yep", "please", "it", "that", "this", "i", "sir", "go", "ahead",
           "thanks", "thank", "you"}


def voice_decision(text: str) -> str | None:
    """'Approve.', 'Jarvis, approve it please', 'Yes, approve' -> 'approve';
    'Reject that' -> 'reject'; anything else (questions, 'don't approve', long sentences) -> None."""
    words = normalise(text)
    for prefix in _WAKE_PREFIXES:
        if words[:len(prefix)] == prefix:
            words = words[len(prefix):]
            break
    if not words or len(words) > MAX_WORDS:
        return None
    if any(w in _NEGATIONS for w in words):
        return None
    keys = [w for w in words if w not in _FILLER]
    if len(keys) != 1:
        return None
    if keys[0] in _APPROVE:
        return "approve"
    if keys[0] in _REJECT:
        return "reject"
    return None
