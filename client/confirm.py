"""Confirmation-gate phrase matching for dangerous commands (eject / self destruct).

Pure (regex only) so it can be unit-tested without audio/keyboard/STT deps. This
gate is the only thing between a misheard reply and a destructive keypress, so the
rule is conservative: ANY explicit negation vetoes the command, even when an
affirmative word is also present, so "no, don't do it" can never fire.
"""
from __future__ import annotations

import re

_AFFIRMATIVE = ["yes", "yeah", "yep", "yup", "confirm", "confirmed", "affirmative",
                "execute", "engage", "do it"]
# 'go', 'proceed' and 'now' are intentionally NOT affirmatives: they collide with
# negative phrasing like "no go back" and "let it go".
_NEGATIVE = ["no", "nope", "negative", "cancel", "abort", "stop", "don't", "dont",
             "do not", "belay", "wait", "hold"]
_AFFIRMATIVE_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in _AFFIRMATIVE) + r")\b")
_NEGATIVE_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in _NEGATIVE) + r")\b")


def is_affirmative(text: str) -> bool:
    t = (text or "").lower().strip()
    if not t:
        return False
    if _NEGATIVE_RE.search(t):  # negation vetoes, even with an affirmative present
        return False
    return bool(_AFFIRMATIVE_RE.search(t))
