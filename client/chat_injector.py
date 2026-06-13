"""Type transcribed speech into Star Citizen's text chat (CHAT mode).

Opens the chat box, puts the message in, and sends it. The message goes in by
CLIPBOARD PASTE (Ctrl+V) rather than per-character typing: SC reliably swallows
the first one or two synthetic keystrokes after a key-release / chat-focus, which
drops the leading character of typed text no matter how much you settle. A paste
is a single atomic event, so there is no first character to lose. Per-character
typewrite remains as a fallback if the clipboard cannot be set.

Uses pydirectinput for the keystrokes (same SendInput approach as the keybind
executor) and the Win32 clipboard via ctypes (no extra dependency, works from the
engine's worker thread). The open/send keys are configurable since they depend on
the player's SC bindings.
"""
from __future__ import annotations

import ctypes
import logging
import time

log = logging.getLogger("stella.chat")

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def _copy_to_clipboard(text: str) -> bool:
    """Put text on the Windows clipboard as CF_UNICODETEXT. Returns success.

    Restypes/argtypes are set explicitly: on 64-bit the default int return value
    truncates the HGLOBAL/pointer handles, which silently corrupts the clipboard
    write. The system takes ownership of the allocated handle on success, so it is
    only freed on the failure paths."""
    try:
        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32
        k32.GlobalAlloc.restype = ctypes.c_void_p
        k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k32.GlobalFree.argtypes = [ctypes.c_void_p]
        u32.OpenClipboard.argtypes = [ctypes.c_void_p]
        u32.SetClipboardData.restype = ctypes.c_void_p
        u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

        buf = text.encode("utf-16-le") + b"\x00\x00"
        handle = k32.GlobalAlloc(_GMEM_MOVEABLE, len(buf))
        if not handle:
            return False
        ptr = k32.GlobalLock(handle)
        if not ptr:
            k32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, buf, len(buf))
        k32.GlobalUnlock(handle)
        if not u32.OpenClipboard(None):
            k32.GlobalFree(handle)
            return False
        try:
            u32.EmptyClipboard()
            if not u32.SetClipboardData(_CF_UNICODETEXT, handle):
                k32.GlobalFree(handle)
                return False
        finally:
            u32.CloseClipboard()
        return True  # the clipboard now owns `handle`
    except Exception:  # noqa: BLE001 - any failure just falls back to typing
        log.exception("clipboard write failed")
        return False


class ChatInjector:
    def __init__(
        self,
        open_key: str = "enter",
        send_key: str = "enter",
        open_delay: float = 0.15,
        enabled: bool = True,
    ):
        self.open_key = open_key
        self.send_key = send_key
        self.open_delay = open_delay
        self.enabled = enabled
        self._pdi = None

    def _backend(self):
        if self._pdi is None:
            import pydirectinput  # lazy: Windows-only

            pydirectinput.PAUSE = 0.0
            pydirectinput.FAILSAFE = False
            self._pdi = pydirectinput
        return self._pdi

    def inject(self, text: str) -> None:
        """Type the text wherever focus currently is.

        open_key/send_key are optional: if set, the chat box is opened first and
        the message sent at the end. By default both are empty, so STELLA just types
        the transcribed text and the user manages focus + sending themselves.
        """
        text = text.strip()
        if not text:
            return
        if not self.enabled:
            log.info("[dry-run] would type: %r", text)
            return
        pdi = self._backend()
        if self.open_key:
            pdi.press(self.open_key)
        # Settle before sending input: acting the instant PTT is released is unreliable
        # (the input field / key-up is still being processed).
        time.sleep(self.open_delay)
        # The PTT key is Right Ctrl - if it (or any modifier) is still seen as held, a
        # paste/keystroke is misread as a shortcut. Force every modifier up and let the
        # key-ups register before anything else goes out.
        for mod in ("ctrl", "ctrlleft", "ctrlright", "shift", "shiftleft",
                    "shiftright", "alt", "altleft", "altright"):
            try:
                pdi.keyUp(mod)
            except Exception:  # noqa: BLE001 - some names vary by backend version
                pass
        time.sleep(0.06)
        # Absorb SC's first-input swallow with a harmless backspace (no-op on an empty
        # field), then settle, so the swallow lands on the sacrifice and not on what
        # follows. One is enough here because the paste itself is a single event.
        pdi.press("backspace")
        time.sleep(0.04)

        pasted = self._paste(pdi, text)
        if not pasted:
            # Fallback: per-character typing. A second sacrifice + settle, since here
            # the leading CHARACTER is what the swallow would eat.
            pdi.press("backspace")
            time.sleep(0.04)
            pdi.typewrite(text, interval=0.012)

        if self.send_key:
            # Let the field register the paste/last characters before submitting; an
            # Enter on the heels of the input is otherwise dropped the same way, so
            # give it a clear gap.
            time.sleep(0.09)
            pdi.press(self.send_key)
        log.info("chat sent: %r (paste=%s, enter=%s)", text, pasted, bool(self.send_key))

    def _paste(self, pdi, text: str) -> bool:
        """Copy text to the clipboard and Ctrl+V it. Returns False (so the caller can
        fall back to typing) if the clipboard write fails."""
        if not _copy_to_clipboard(text):
            return False
        try:
            pdi.keyDown("ctrl")
            time.sleep(0.01)
            pdi.press("v")
            time.sleep(0.01)
            pdi.keyUp("ctrl")
            return True
        except Exception:  # noqa: BLE001
            log.exception("paste keystroke failed; falling back to typing")
            try:
                pdi.keyUp("ctrl")  # never leave Ctrl stuck down
            except Exception:  # noqa: BLE001
                pass
            return False
