#!/usr/bin/env python3
"""Bounded-autonomy guardrails for the supervisor (Appendix A.9 step 5 / A.7).

Hard preconditions before any UNATTENDED spawn. Pure functions + a tiny
stateful detector — no broker, no transport — so they unit-test on Python 3.9.

Two complementary "this turn is going nowhere" signals:
  - give-up:  the turn's text says it can't proceed / asks the human to clarify.
              Regex patterns ported from Odysseus `src/teacher_escalation.py`
              (`evaluate_turn_regex`), MIT-licensed. NOTE: that module does
              FAILURE/give-up detection, NOT loop detection — the plan's "lift
              it for livelock" conflated the two, so the loop detector below is
              written fresh.
  - livelock: the turn keeps producing the SAME output (content-hash repeats),
              i.e. spinning without progress.
The supervisor also enforces a turn cap (max self-continues per wake). Any trip
escalates to a human channel.
"""
import hashlib
import re
from typing import Optional

# ── give-up / failure regexes (ported from teacher_escalation.py) ──────────
_REPLY_GIVE_UP_PATTERNS = [
    re.compile(r"\bI don't have (?:a )?tool\b", re.IGNORECASE),
    re.compile(r"\bI can(?:'t|not) (?:do|find|figure)\b", re.IGNORECASE),
    re.compile(r"\bI'?m not sure (?:which|how|what)\b", re.IGNORECASE),
    re.compile(r"\b[Cc]ould you (?:tell me|specify|clarify)\b"),
    re.compile(r"\bunable to (?:open|find|switch|complete)\b", re.IGNORECASE),
    re.compile(r"\bdoesn'?t (?:exist|appear to be|seem to)\b", re.IGNORECASE),
]


def looks_like_give_up(text: str) -> Optional[str]:
    """Return a short reason if the text reads like the agent gave up / is
    asking the human to step in; otherwise None."""
    if not text:
        return None
    for pat in _REPLY_GIVE_UP_PATTERNS:
        if pat.search(text):
            return f"matched give-up pattern {pat.pattern!r}"
    return None


def content_hash(text: str) -> str:
    """Stable hash of a turn's output, used to detect a spinning agent."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class LivelockDetector:
    """Flags when an agent repeats the SAME output `repeat_threshold` times in a
    row — the spinning-without-progress signal a turn cap alone won't catch
    until much later. Empty outputs are ignored (a quiesced turn produces no
    text and should not look like a loop)."""

    def __init__(self, repeat_threshold: int = 3):
        if repeat_threshold < 2:
            raise ValueError("repeat_threshold must be >= 2")
        self.repeat_threshold = repeat_threshold
        self._last: Optional[str] = None
        self._count = 0

    def record(self, output: str) -> Optional[str]:
        """Record one turn's output. Returns a reason string once the same
        non-empty output has repeated repeat_threshold times, else None."""
        if not output:
            self._last = None
            self._count = 0
            return None
        h = content_hash(output)
        if h == self._last:
            self._count += 1
        else:
            self._last = h
            self._count = 1
        if self._count >= self.repeat_threshold:
            return f"identical output repeated {self._count}x"
        return None
