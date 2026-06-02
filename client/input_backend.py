"""Cross-platform virtual keyboard input for the STELLA client.

Windows keeps using pydirectinput/SendInput. Linux supports two command-line
backends:

- Wayland: ydotool (uinput-level injection through ydotoold)
- X11/XWayland: xdotool

Set STELLA_INPUT_BACKEND=ydotool or xdotool to force one. Otherwise Wayland picks
ydotool and X11 picks xdotool.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time

_XDO_ALIAS = {
    "ctrl": "Control_L", "ctrlleft": "Control_L", "ctrlright": "Control_R",
    "alt": "Alt_L", "altleft": "Alt_L", "altright": "Alt_R",
    "shift": "Shift_L", "shiftleft": "Shift_L", "shiftright": "Shift_R",
    "win": "Super_L",
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "space": "space", "backspace": "BackSpace", "tab": "Tab",
    ".": "period", ",": "comma", ";": "semicolon", "/": "slash",
    "\\": "backslash", "-": "minus", "=": "equal", "`": "grave",
    "[": "bracketleft", "]": "bracketright",
}

# Linux input-event key codes used by ydotool. This covers the ship-command keys,
# modifiers, chat text cleanup, and common punctuation. Add more as needed.
_YDO_CODE = {
    "esc": 1, "escape": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "-": 12, "minus": 12, "=": 13, "equals": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "[": 26, "leftbracket": 26, "]": 27, "rightbracket": 27, "enter": 28, "return": 28,
    "ctrl": 29, "control": 29, "ctrlleft": 29, "left_ctrl": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    ";": 39, "semicolon": 39, "'": 40, "quote": 40, "`": 41, "grave": 41,
    "shift": 42, "shiftleft": 42, "left_shift": 42, "\\": 43, "backslash": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    ",": 51, "comma": 51, ".": 52, "period": 52, "/": 53, "slash": 53,
    "shiftright": 54, "right_shift": 54, "alt": 56, "altleft": 56, "left_alt": 56,
    "space": 57, "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "ctrlright": 97, "right_ctrl": 97, "altright": 100, "right_alt": 100,
    "win": 125, "windows": 125, "left_win": 125,
}


def _xdo_key(name: str) -> str:
    low = name.lower()
    if low.startswith("f") and low[1:].isdigit():
        return low.upper()
    if len(name) == 1 and name.isalpha():
        return name.lower()
    return _XDO_ALIAS.get(low, name)


def _ydo_code(name: str) -> int:
    low = name.lower().strip()
    if low not in _YDO_CODE:
        raise RuntimeError(f"No ydotool key-code mapping for {name!r}")
    return _YDO_CODE[low]


class VirtualKeyboard:
    """Small adapter exposing the subset used by key/chat injection."""

    def __init__(self):
        forced = os.environ.get("STELLA_INPUT_BACKEND", "").strip().lower()
        if platform.system() == "Windows":
            self._backend = "pydirectinput"
        elif forced in {"ydotool", "xdotool"}:
            self._backend = forced
        elif os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            self._backend = "ydotool"
        else:
            self._backend = "xdotool"
        self._pdi = None

        exe = None if self._backend == "pydirectinput" else shutil.which(self._backend)
        if self._backend != "pydirectinput" and exe is None:
            raise RuntimeError(
                f"{self._backend} is required for Linux key injection. Install it and make "
                "sure its daemon/service is running if needed. For Wayland: ydotoold."
            )

    def _pydirectinput(self):
        if self._pdi is None:
            import pydirectinput  # lazy: Windows-only

            pydirectinput.PAUSE = 0.0
            pydirectinput.FAILSAFE = False
            self._pdi = pydirectinput
        return self._pdi

    def _run_xdotool(self, *args: str) -> None:
        subprocess.run(["xdotool", *args], check=True)

    def _run_ydotool(self, *args: str) -> None:
        env = os.environ.copy()
        env.setdefault("YDOTOOL_SOCKET", "/tmp/.ydotool_socket")
        subprocess.run(["ydotool", *args], check=True, env=env)

    def keyDown(self, key: str) -> None:  # noqa: N802 - mirror pydirectinput API
        if self._backend == "pydirectinput":
            self._pydirectinput().keyDown(key)
        elif self._backend == "ydotool":
            self._run_ydotool("key", f"{_ydo_code(key)}:1")
        else:
            self._run_xdotool("keydown", _xdo_key(key))

    def keyUp(self, key: str) -> None:  # noqa: N802 - mirror pydirectinput API
        if self._backend == "pydirectinput":
            self._pydirectinput().keyUp(key)
        elif self._backend == "ydotool":
            self._run_ydotool("key", f"{_ydo_code(key)}:0")
        else:
            self._run_xdotool("keyup", _xdo_key(key))

    def press(self, key: str) -> None:
        if self._backend == "pydirectinput":
            self._pydirectinput().press(key)
        elif self._backend == "ydotool":
            code = _ydo_code(key)
            self._run_ydotool("key", f"{code}:1", f"{code}:0")
        else:
            self._run_xdotool("key", _xdo_key(key))

    def typewrite(self, text: str, interval: float = 0.0) -> None:
        if self._backend == "pydirectinput":
            self._pydirectinput().typewrite(text, interval=interval)
            return
        if self._backend == "ydotool":
            self._run_ydotool("type", text)
        else:
            self._run_xdotool("type", "--clearmodifiers", "--delay", str(int(interval * 1000)), text)
        if interval > 0:
            time.sleep(min(interval * len(text), 0.2))
