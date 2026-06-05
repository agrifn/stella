"""Detect a spoken mode switch ("switch to chat" / "command mode") in any mode.

Handled client-side and checked BEFORE the COMMAND/CHAT branch, so it works whether
STELLA is in COMMAND mode (where it would otherwise be sent to the intent classifier)
or CHAT mode (where the text would otherwise be typed into the game). Pure + testable.

Returns "CHAT", "COMMAND", or None.
"""
from __future__ import annotations

from typing import Optional

# A switch-context word must be present so plain words like "command" or "chat" inside
# a normal utterance do not flip the mode.
_SWITCH_WORDS = ("switch", "change", "go to", "enter", "set ", "mode", "put ")


def detect_mode_switch(text: str) -> Optional[str]:
    t = (text or "").lower().strip().strip(".,!?")
    if not t or not any(w in t for w in _SWITCH_WORDS):
        return None
    chat = "chat" in t
    command = "command" in t or "commands" in t
    if chat and not command:
        return "CHAT"
    if command and not chat:
        return "COMMAND"
    return None
