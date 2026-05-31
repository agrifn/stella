"""HTTP client to the STELLA server's /command and /health endpoints."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("stella.sender")


@dataclass
class CommandResult:
    intent: str
    keybind: Optional[str]
    hold: bool
    confirm_required: bool
    response_text: str
    audio_b64: Optional[str]
    sequence: Optional[list] = None  # macro steps (list of dicts), or empty


class CommandSender:
    def __init__(self, server_url: str, token: str | None = None, timeout: float = 30.0):
        self._url = server_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        # Don't reuse connections: idle keep-alive sockets to the WSL2 port-forward
        # go stale and then hang/error. A fresh connection per request to 127.0.0.1
        # is ~1ms and avoids that entirely.
        self._session.headers["Connection"] = "close"
        if token:  # optional shared secret; server requires it only when configured
            self._session.headers["Authorization"] = f"Bearer {token}"

    def send(self, text: str, speak: bool = True) -> CommandResult:
        r = self._session.post(
            f"{self._url}/command",
            json={"text": text, "speak": speak},
            timeout=self._timeout,
        )
        r.raise_for_status()
        d = r.json()
        return CommandResult(
            intent=d.get("intent", "chat"),
            keybind=d.get("keybind"),
            hold=bool(d.get("hold", False)),
            confirm_required=bool(d.get("confirm_required", False)),
            response_text=d.get("response_text", ""),
            audio_b64=d.get("audio"),
            sequence=d.get("sequence") or [],
        )

    def speak(self, text: str) -> Optional[str]:
        """Get TTS audio (base64 WAV) for arbitrary text, no intent parsing."""
        try:
            r = self._session.post(f"{self._url}/speak", json={"text": text},
                                   timeout=self._timeout)
            r.raise_for_status()
            return r.json().get("audio")
        except requests.RequestException:
            return None

    def health(self) -> dict:
        r = self._session.get(f"{self._url}/health", timeout=self._timeout)
        r.raise_for_status()
        return r.json()
