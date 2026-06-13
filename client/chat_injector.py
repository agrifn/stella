"""Type transcribed speech into Star Citizen's text chat (CHAT mode).

Opens the chat box, types the message as virtual keystrokes, and sends it. Uses
pydirectinput (same SendInput approach as the keybind executor). The open/send
keys are configurable since they depend on the player's SC bindings.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("stella.chat")


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
        # Always settle before typing: typing the instant PTT is released drops the
        # first character (the input field / key-up is still being processed).
        time.sleep(self.open_delay)
        # The PTT key is Right Ctrl - if it (or any modifier) is still seen as held,
        # the first character is swallowed as a Ctrl/Alt/Shift shortcut. Force every
        # modifier up and let the key-ups register before any text goes out.
        for mod in ("ctrl", "ctrlleft", "ctrlright", "shift", "shiftleft",
                    "shiftright", "alt", "altleft", "altright"):
            try:
                pdi.keyUp(mod)
            except Exception:  # noqa: BLE001 - some names vary by backend version
                pass
        time.sleep(0.06)
        # SC swallows the first one or two synthetic keystrokes right after a
        # key-release / chat-focus. Sacrifice a couple of backspaces (harmless on an
        # empty field) to absorb them, then settle so the real first character is not
        # the one that gets eaten. This is what protects the leading character.
        for _ in range(2):
            pdi.press("backspace")
            time.sleep(0.02)
        time.sleep(0.04)
        # typewrite sends each character; pydirectinput handles shift for uppercase.
        pdi.typewrite(text, interval=0.012)
        if self.send_key:
            # Let the field register the last characters before submitting; an Enter
            # on the heels of the final keystroke is otherwise dropped (same swallow
            # as the first char), so give it a clear gap.
            time.sleep(0.09)
            pdi.press(self.send_key)
        log.info("typed: %r (sent: %s)", text, bool(self.send_key))
