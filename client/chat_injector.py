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
        # typewrite sends each character; pydirectinput handles shift for uppercase.
        pdi.typewrite(text, interval=0.01)
        if self.send_key:
            pdi.press(self.send_key)
        log.info("typed: %r", text)
