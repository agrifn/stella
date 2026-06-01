"""Split one spoken utterance into a sequence of (command_text, repeat_count) steps.

Two features handled here, both purely client-side so the LLM classifier and the
server stay single-intent and untouched:

  * Multiple commands in one breath - "lower shields and raise engine power"
    -> [("lower shields", 1), ("raise engine power", 1)]. Each part is then sent
    through /command on its own.

  * Repeat a command - "fire three flares" -> [("fire flares", 3)]. The count word
    is parsed out and the bare command is classified once, then executed N times.

Kept deliberately conservative: only small counts (2..MAX_REPEAT) from number words
or small digits trigger a repeat, so a command that legitimately contains a number is
unlikely to be mistaken for a repeat. A mis-split part that does not classify to a real
command simply resolves to chat and does nothing, so over-splitting fails safe.
"""
from __future__ import annotations

import re

MAX_REPEAT = 10  # never fire a keybind more than this many times from one utterance

# Spoken connectors that join separate commands. Matched as whole words with spaces
# around them so we never cut inside a word.
_SPLIT_RE = re.compile(r"\s+(?:and then|and|then)\s+", re.I)

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    # common casual quantities
    "couple": 2, "few": 3,
}


def _word_count(token: str) -> int | None:
    """Return the count a token represents (number word or small digit), else None."""
    t = token.lower().strip(".,!?")
    if t in _NUMBER_WORDS:
        return _NUMBER_WORDS[t]
    if t.isdigit():
        return int(t)
    return None


def parse_repeat(text: str) -> tuple[str, int]:
    """Pull a repeat count out of `text`. Returns (text_without_count, count).

    Only a count of 2..MAX_REPEAT taken from a number word/small digit that is NOT the
    last word (there has to be something to repeat after it) is honored; otherwise the
    text is returned unchanged with count 1.
    """
    words = text.split()
    for i, w in enumerate(words):
        n = _word_count(w)
        # Must be a real repeat (2+), within cap, and have a command word after it.
        if n is None or n < 2 or n > MAX_REPEAT or i >= len(words) - 1:
            continue
        rest = words[:i] + words[i + 1:]
        # Drop a trailing "times"/"x" that followed the number ("three times").
        if i < len(rest) and rest[i].lower().strip(".,") in ("times", "time", "x"):
            rest = rest[:i] + rest[i + 1:]
        cleaned = " ".join(rest).strip()
        return (cleaned or text.strip(), n)
    return (text.strip(), 1)


def split_commands(text: str) -> list[tuple[str, int]]:
    """Split an utterance into [(command_text, repeat_count), ...].

    A plain single command returns a one-item list with count 1 - i.e. exactly the old
    behavior, so simple commands are completely unchanged.
    """
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    if not parts:
        return [(text, 1)]
    return [parse_repeat(p) for p in parts]
