"""Cross-platform global key state helpers.

Windows uses the `keyboard` package. Linux prefers direct evdev reads from
/dev/input/event* so PTT works under Wayland without X11. This requires permission
to read keyboard event devices (usually membership in the `input` group, a udev
rule, or running only this client with suitable privileges). If evdev is not
available, Linux falls back to pynput or X11 xinput/xmodmap.
"""
from __future__ import annotations

import glob
import os
import platform
import select
import shutil
import struct
import subprocess
import threading
import time


_SPECIAL_NAMES = {
    "ctrl_l": "left ctrl", "ctrl_r": "right ctrl",
    "shift_l": "left shift", "shift_r": "right shift",
    "alt_l": "left alt", "alt_r": "right alt",
    "cmd_l": "left win", "cmd_r": "right win",
    "enter": "enter", "space": "space", "esc": "escape", "tab": "tab",
}

# Linux input-event key codes. Same namespace ydotool uses.
_EVDEV_CODE = {
    "esc": 1, "escape": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "-": 12, "minus": 12, "=": 13, "equals": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "[": 26, "leftbracket": 26, "]": 27, "rightbracket": 27, "enter": 28, "return": 28,
    "ctrl": 29, "control": 29, "ctrlleft": 29, "left ctrl": 29, "left_ctrl": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    ";": 39, "semicolon": 39, "'": 40, "quote": 40, "`": 41, "grave": 41,
    "shift": 42, "shiftleft": 42, "left shift": 42, "left_shift": 42, "\\": 43, "backslash": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    ",": 51, "comma": 51, ".": 52, "period": 52, "/": 53, "slash": 53,
    "shiftright": 54, "right shift": 54, "right_shift": 54,
    "alt": 56, "altleft": 56, "left alt": 56, "left_alt": 56,
    "space": 57, "capslock": 58, "scroll lock": 70, "scroll_lock": 70,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "ctrlright": 97, "right ctrl": 97, "right_ctrl": 97,
    "altright": 100, "right alt": 100, "right_alt": 100,
    "win": 125, "windows": 125, "left win": 125, "left_win": 125,
}

_KEYSYM = {
    "right ctrl": ["Control_R"], "left ctrl": ["Control_L"], "ctrl": ["Control_L", "Control_R"],
    "control": ["Control_L", "Control_R"], "right shift": ["Shift_R"], "left shift": ["Shift_L"],
    "shift": ["Shift_L", "Shift_R"], "right alt": ["Alt_R", "ISO_Level3_Shift"],
    "left alt": ["Alt_L"], "alt": ["Alt_L", "Alt_R", "ISO_Level3_Shift"],
    "space": ["space"], "enter": ["Return", "KP_Enter"], "return": ["Return", "KP_Enter"],
    "tab": ["Tab"], "escape": ["Escape"], "esc": ["Escape"], "scroll lock": ["Scroll_Lock"],
}

_EVENT_STRUCT = "llHHI"
_EVENT_SIZE = struct.calcsize(_EVENT_STRUCT)
_EV_KEY = 1


def _norm(name: str) -> str:
    return name.replace("_", " ").lower().strip()


class KeyState:
    def __init__(self):
        self._pressed: set[str] = set()
        self._pressed_codes: set[int] = set()
        self._cond = threading.Condition()
        self._backend = "keyboard"
        self._keyboard = None
        self._listener = None
        self._xinput_id: str | None = None
        self._keycode_cache: dict[str, list[str]] = {}
        self._evdev_fds: list[int] = []
        self._evdev_stop = threading.Event()

        if platform.system() == "Windows":
            import keyboard
            self._keyboard = keyboard
            return

        if self._try_evdev():
            self._backend = "evdev"
            return
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            raise RuntimeError(
                "Wayland PTT requires read access to keyboard /dev/input/event* devices. "
                "Add your user to the input group (sudo usermod -aG input $USER) or add a "
                "udev rule, then log out/in. Run client.ptt_test to verify."
            )

        try:
            from pynput import keyboard as pynput_keyboard
            self._backend = "pynput"
            self._listener = pynput_keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            self._listener.daemon = True
            self._listener.start()
            return
        except Exception:  # noqa: BLE001 - fall through to X11 polling
            pass

        if shutil.which("xinput") and shutil.which("xmodmap"):
            self._backend = "xinput"
            self._xinput_id = self._detect_xinput_keyboard_id()
            return

        raise RuntimeError(
            "Linux PTT support needs readable /dev/input/event* keyboard devices, or pynput, "
            "or xinput+xmodmap. For Wayland, add your user to the input group or add a udev "
            "rule, then log out/in."
        )

    # -- evdev -----------------------------------------------------------
    def _keyboard_event_paths(self) -> list[str]:
        paths: set[str] = set()
        for p in glob.glob("/dev/input/by-path/*kbd*"):
            try:
                paths.add(os.path.realpath(p))
            except OSError:
                pass
        try:
            text = open("/proc/bus/input/devices", encoding="utf-8", errors="ignore").read()
            for block in text.split("\n\n"):
                low = block.lower()
                if "keyboard" not in low and "kbd" not in low:
                    continue
                for part in block.split():
                    if part.startswith("event"):
                        paths.add(f"/dev/input/{part}")
        except OSError:
            pass
        return sorted(paths)

    def _try_evdev(self) -> bool:
        paths = self._keyboard_event_paths()
        for path in paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                self._evdev_fds.append(fd)
            except PermissionError:
                continue
            except OSError:
                continue
        if not self._evdev_fds:
            return False
        t = threading.Thread(target=self._evdev_loop, name="stella-evdev", daemon=True)
        t.start()
        return True

    def _evdev_loop(self) -> None:
        while not self._evdev_stop.is_set():
            try:
                ready, _, _ = select.select(self._evdev_fds, [], [], 0.25)
            except (OSError, ValueError):
                return
            for fd in ready:
                try:
                    data = os.read(fd, _EVENT_SIZE * 32)
                except BlockingIOError:
                    continue
                except OSError:
                    continue
                for off in range(0, len(data) - _EVENT_SIZE + 1, _EVENT_SIZE):
                    _, _, ev_type, code, value = struct.unpack(_EVENT_STRUCT, data[off:off + _EVENT_SIZE])
                    if ev_type != _EV_KEY:
                        continue
                    with self._cond:
                        if value:
                            self._pressed_codes.add(code)
                        else:
                            self._pressed_codes.discard(code)
                        self._cond.notify_all()

    def _evdev_is_pressed(self, key: str) -> bool:
        code = _EVDEV_CODE.get(_norm(key))
        if code is None:
            return False
        with self._cond:
            return code in self._pressed_codes

    # -- pynput ----------------------------------------------------------
    def _key_names(self, key) -> set[str]:  # noqa: ANN001 - pynput key types vary
        names: set[str] = set()
        char = getattr(key, "char", None)
        if char:
            names.add(_norm(char))
        raw = getattr(key, "name", None) or str(key).replace("Key.", "")
        if raw:
            names.add(_norm(raw))
            mapped = _SPECIAL_NAMES.get(raw)
            if mapped:
                names.add(mapped)
                if mapped.endswith(" ctrl"):
                    names.add("ctrl")
                if mapped.endswith(" shift"):
                    names.add("shift")
                if mapped.endswith(" alt"):
                    names.add("alt")
        return names

    def _on_press(self, key) -> None:  # noqa: ANN001
        with self._cond:
            self._pressed.update(self._key_names(key))
            self._cond.notify_all()

    def _on_release(self, key) -> None:  # noqa: ANN001
        with self._cond:
            self._pressed.difference_update(self._key_names(key))
            self._cond.notify_all()

    # -- xinput ----------------------------------------------------------
    def _detect_xinput_keyboard_id(self) -> str:
        try:
            out = subprocess.check_output(["xinput", "list"], text=True, stderr=subprocess.DEVNULL)
            candidates: list[tuple[bool, str]] = []
            for line in out.splitlines():
                lower = line.lower()
                if "keyboard" not in lower or "id=" not in line or "master keyboard" in lower:
                    continue
                candidates.append(("xtest" in lower, line.split("id=", 1)[1].split()[0]))
            for _, dev_id in sorted(candidates):
                try:
                    subprocess.run(["xinput", "query-state", dev_id], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, check=True)
                    return dev_id
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError("Could not find an X11 keyboard device usable with xinput query-state")

    def _keycodes_for(self, key: str) -> list[str]:
        key = _norm(key)
        if key in self._keycode_cache:
            return self._keycode_cache[key]
        keysyms = _KEYSYM.get(key) or ([key] if len(key) == 1 else [key.replace(" ", "_")])
        out = subprocess.check_output(["xmodmap", "-pke"], text=True)
        codes: list[str] = []
        for line in out.splitlines():
            if line.startswith("keycode") and "=" in line:
                lhs, rhs = line.split("=", 1)
                if set(keysyms).intersection(rhs.split()):
                    codes.append(lhs.split()[1])
        self._keycode_cache[key] = codes
        return codes

    def _xinput_is_pressed(self, key: str) -> bool:
        codes = self._keycodes_for(key)
        if not codes:
            return False
        try:
            out = subprocess.check_output(["xinput", "query-state", self._xinput_id], text=True,
                                          stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            self._xinput_id = self._detect_xinput_keyboard_id()
            out = subprocess.check_output(["xinput", "query-state", self._xinput_id], text=True,
                                          stderr=subprocess.DEVNULL)
        return any(f"key[{code}]=down" in out for code in codes)

    # -- public ----------------------------------------------------------
    def is_pressed(self, key: str) -> bool:
        key = _norm(key)
        if self._backend == "keyboard":
            return bool(self._keyboard.is_pressed(key))
        if self._backend == "evdev":
            return self._evdev_is_pressed(key)
        if self._backend == "xinput":
            return self._xinput_is_pressed(key)
        with self._cond:
            return key in self._pressed

    def wait(self, key: str, timeout: float | None = None) -> bool:
        key = _norm(key)
        if self._backend == "keyboard":
            if timeout is None:
                self._keyboard.wait(key)
                return True
            deadline = time.monotonic() + timeout
            while not self._keyboard.is_pressed(key):
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.02)
            return True
        if self._backend in {"evdev", "xinput"}:
            deadline = None if timeout is None else time.monotonic() + timeout
            while not self.is_pressed(key):
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                time.sleep(0.02)
            return True
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while key not in self._pressed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True


_DEFAULT: KeyState | None = None


def get_key_state() -> KeyState:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = KeyState()
    return _DEFAULT
