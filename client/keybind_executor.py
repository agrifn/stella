"""Execute server-resolved keybinds in Star Citizen via virtual keystrokes.

Uses pydirectinput, which sends DirectInput-style scancodes through the Windows
SendInput API - the same external-virtual-input approach VoiceAttack uses, which
the SC community runs under EAC. EAC blocks DLL injection and memory tampering,
not synthetic input, so this pattern is compatible. (Anti-cheat behavior can
change; the live in-game test is the real confirmation.)

The parse step is pure and import-free so it can be unit-tested on any platform;
pydirectinput is imported lazily only when actually sending keys (it is Windows
only and needs a desktop session).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger("stella.exec")

# Map our friendly key names to pydirectinput key names.
KEY_ALIASES = {
    "period": ".",
    "comma": ",",
    "semicolon": ";",
    "slash": "/",
    "backslash": "\\",
    "minus": "-",
    "equals": "=",
    "grave": "`",
    "leftbracket": "[",
    "rightbracket": "]",
    "control": "ctrl",
    "win": "win",
    "windows": "win",
    "return": "enter",
    # left/right-specific modifiers (as used by SC bindings)
    "left_alt": "altleft", "right_alt": "altright",
    "left_shift": "shiftleft", "right_shift": "shiftright",
    "left_ctrl": "ctrlleft", "right_ctrl": "ctrlright",
}

MODIFIERS = {
    "ctrl", "alt", "shift", "win",
    "altleft", "altright", "shiftleft", "shiftright", "ctrlleft", "ctrlright",
}


@dataclass(frozen=True)
class ParsedKeybind:
    modifiers: tuple[str, ...]
    key: str
    taps: int = 1


def _normalize_token(tok: str) -> str:
    tok = tok.strip().lower()
    return KEY_ALIASES.get(tok, tok)


def parse_keybind(keybind: str) -> ParsedKeybind:
    """Parse 'alt+y' / '0' / 'period' / 'f10+f10' (double-tap) into a ParsedKeybind.

    Handles modifier+key combos, a standalone modifier used AS the key (e.g.
    'left_shift' for boost), and same-key-twice double taps ('f10+f10'). Pure."""
    if not keybind or not keybind.strip():
        raise ValueError("empty keybind")
    tokens = [_normalize_token(t) for t in keybind.split("+") if t.strip()]

    # double-tap: same key repeated (e.g. f10+f10)
    if len(tokens) == 2 and tokens[0] == tokens[1] and tokens[0] not in MODIFIERS:
        return ParsedKeybind(modifiers=(), key=tokens[0], taps=2)

    mods = [t for t in tokens if t in MODIFIERS]
    keys = [t for t in tokens if t not in MODIFIERS]
    if not keys and mods:
        # a lone modifier used as the actual key (e.g. boost = left_shift held)
        return ParsedKeybind(modifiers=tuple(mods[:-1]), key=mods[-1])
    if len(keys) != 1:
        raise ValueError(f"keybind must have exactly one non-modifier key: {keybind!r}")
    return ParsedKeybind(modifiers=tuple(mods), key=keys[0])


class KeybindExecutor:
    def __init__(self, hold_duration: float = 1.5, enabled: bool = True):
        self.hold_duration = hold_duration
        self.enabled = enabled
        self._pdi = None

    def _backend(self):
        if self._pdi is None:
            import pydirectinput  # lazy: Windows-only

            pydirectinput.PAUSE = 0.0  # we manage timing ourselves
            pydirectinput.FAILSAFE = False
            self._pdi = pydirectinput
        return self._pdi

    def execute(self, keybind: str, hold: bool = False) -> ParsedKeybind:
        """Send the keystroke. Returns the parsed keybind (for logging/tests)."""
        parsed = parse_keybind(keybind)
        if not self.enabled:
            log.info("[dry-run] would press %s%s",
                     "+".join((*parsed.modifiers, parsed.key)),
                     " (hold)" if hold else "")
            return parsed

        pdi = self._backend()
        for m in parsed.modifiers:
            pdi.keyDown(m)
        try:
            if hold:
                pdi.keyDown(parsed.key)
                time.sleep(self.hold_duration)
                pdi.keyUp(parsed.key)
            elif parsed.taps > 1:
                for _ in range(parsed.taps):
                    pdi.press(parsed.key)
                    time.sleep(0.08)
            else:
                pdi.press(parsed.key)
        finally:
            for m in reversed(parsed.modifiers):
                pdi.keyUp(m)
        log.info("pressed %s%s", "+".join((*parsed.modifiers, parsed.key)),
                 " (held)" if hold else f" (x{parsed.taps})" if parsed.taps > 1 else "")
        return parsed

    def execute_sequence(self, steps) -> None:
        """Run a macro: an ordered list of steps (dicts or objects) with fields
        key, hold, taps, delay. Each step presses its key, then waits `delay`."""
        for i, step in enumerate(steps):
            get = step.get if isinstance(step, dict) else (lambda k, d=None: getattr(step, k, d))
            key = get("key")
            if not key:
                continue
            hold = bool(get("hold", False))
            taps = max(1, int(get("taps", 1) or 1))
            delay = float(get("delay", 0.1) or 0.0)
            if hold:
                self.execute(key, hold=True)
            else:
                for _ in range(taps):
                    self.execute(key, hold=False)
            log.info("macro step %d: %s (hold=%s taps=%s)", i, key, hold, taps)
            time.sleep(max(0.0, delay))
