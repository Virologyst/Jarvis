"""Spoken "approve" / "reject": handled in code, never by the model.

Claude can't approve anything: a decision only happens when a person's own words, as heard by
speech-to-text, are a short, plain approve or reject. Natural wording is fine ("approve the
upgrade", "go ahead and approve it, Jarvis", "apply the upgrade", "reject that email"), and
naming what to approve picks that card. Anything hedged, negated, a question, or with extra
detail goes to Claude as normal conversation (and Claude has no tool to approve with).
"""

from __future__ import annotations

from jarvis_protocol import Card

from .wakecheck import normalise

MAX_WORDS = 10
_WAKE_PREFIXES = (["hey", "jarvis"], ["ok", "jarvis"], ["jarvis"])
_LEAD_INS = (["please", "go", "ahead", "and"], ["go", "ahead", "and"], ["yes", "go", "ahead", "and"])
_APPROVE = {"approve", "approved", "approval", "apply", "accept", "accepted"}
_REJECT = {"reject", "rejected", "decline", "declined", "deny", "denied"}
_NEGATIONS = {"don't", "dont", "do", "not", "no", "never", "cancel", "stop", "wait", "what",
              "why", "which", "should", "can", "could", "would", "will", "if", "before", "after",
              "but", "and", "or", "how", "of", "isn't", "shouldn't", "won't", "later"}
_FILLER = {"yes", "yeah", "yep", "sure", "ok", "please", "it", "that", "this", "the", "one",
           "i", "sir", "jarvis", "go", "ahead", "thanks", "thank", "you", "now", "new", "latest",
           "last", "pending", "card", "request", "right"}
# What a phrase can name, and the words for it
_TARGETS = {
    "upgrade": {"upgrade", "upgrades", "self", "update", "updates", "change", "changes"},
    "undo": {"undo", "rollback", "roll", "back"},
    "email": {"email", "emails", "mail", "reply", "message"},
}


def parse_voice_decision(text: str) -> tuple[str, str | None] | None:
    """('approve' | 'reject', target or None), or None if this isn't a plain decision.

    'Approve.' -> ('approve', None); 'Jarvis, approve the upgrade' -> ('approve', 'upgrade');
    'Reject that email' -> ('reject', 'email'); "Don't approve", 'Should I approve?',
    'Approve the email to Jo' -> None.
    """
    words = normalise(text)
    for prefix in _WAKE_PREFIXES:
        if words[:len(prefix)] == prefix:
            words = words[len(prefix):]
            break
    for lead in _LEAD_INS:
        if words[:len(lead)] == lead:
            words = words[len(lead):]
            break
    if not words or len(words) > MAX_WORDS:
        return None
    if any(w in _NEGATIONS for w in words):
        return None
    decisions, targets = [], set()
    for w in words:
        if w in _APPROVE:
            decisions.append("approve")
        elif w in _REJECT:
            decisions.append("reject")
        elif w in _FILLER:
            continue
        else:
            target = next((t for t, names in _TARGETS.items() if w in names), None)
            if target is None:
                return None          # a word we don't understand: let Claude handle it
            targets.add(target)
    if len(decisions) != 1 or len(targets) > 1:
        return None
    return decisions[0], (targets.pop() if targets else None)


def voice_decision(text: str) -> str | None:
    """Just the decision: 'approve', 'reject' or None."""
    parsed = parse_voice_decision(text)
    return parsed[0] if parsed else None


def card_target(card: Card) -> str | None:
    """What a card is about, in the words a person would use for it."""
    data = card.data or {}
    if data.get("upgrade_id"):
        return "undo" if data.get("step") == "undo" else "upgrade"
    if "email" in f"{card.title} {card.body}".lower():
        return "email"
    return None


def pick_card(pending: list[Card], target: str | None) -> Card | None:
    """The newest waiting card the person meant: any card if they didn't say, else one about
    what they named ("the upgrade" also covers undoing one)."""
    if target is None:
        return pending[-1] if pending else None
    for card in reversed(pending):
        if card_target(card) == target:
            return card
    if target == "upgrade":
        for card in reversed(pending):
            if card_target(card) == "undo":
                return card
    return None
