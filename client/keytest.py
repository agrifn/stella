"""Standalone keystroke diagnostic - isolates key-sending from the voice pipeline.

Usage (from the repo root, in the venv):
    python -m client.keytest                 # types "hello stella" after a countdown
    python -m client.keytest 0 l n           # sends keys 0, l, n (e.g. ship keybinds)

Focus the TARGET window (Notepad to start, then Star Citizen) during the countdown.
If text appears in Notepad but not in SC, it is an elevation/focus issue -> run this
terminal as Administrator and make sure SC is the focused window.
"""
from __future__ import annotations

import ctypes
import sys
import time

from .keybind_executor import KeybindExecutor


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _foreground_title() -> str:
    try:
        user32 = ctypes.windll.user32
        h = user32.GetForegroundWindow()
        n = user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        return buf.value
    except Exception:  # noqa: BLE001
        return "?"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    keys = argv or list("hello") + ["space"] + list("stella")

    print(f"Running as Administrator: {_is_admin()}")
    print("Focus the TARGET window now (Notepad, then try Star Citizen).")
    for i in (3, 2, 1):
        print(f"  sending in {i}...  (foreground: {_foreground_title()!r})")
        time.sleep(1)

    ex = KeybindExecutor(enabled=True)
    print(f"Foreground at send time: {_foreground_title()!r}")
    for k in keys:
        try:
            ex.execute(k)
            print(f"  sent {k!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED {k!r}: {e}")
        time.sleep(0.25)
    print("done. Did the keys land in the target window?")


if __name__ == "__main__":
    main()
