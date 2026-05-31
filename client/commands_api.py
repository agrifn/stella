"""Client for the server's /commands CRUD API (used by the GUI)."""
from __future__ import annotations

import requests


class CommandsAPI:
    def __init__(self, server_url: str, token: str | None = None, timeout: float = 15.0):
        self._url = server_url.rstrip("/")
        self._timeout = timeout
        self._s = requests.Session()
        self._s.headers["Connection"] = "close"  # avoid stale WSL2 keep-alive sockets
        if token:  # optional shared secret; server requires it only when configured
            self._s.headers["Authorization"] = f"Bearer {token}"

    def list(self) -> list[dict]:
        r = self._s.get(f"{self._url}/commands", timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def create(self, cmd: dict) -> dict:
        r = self._s.post(f"{self._url}/commands", json=cmd, timeout=self._timeout)
        if r.status_code >= 400:
            raise RuntimeError(self._detail(r))
        return r.json()

    def update(self, intent: str, patch: dict) -> dict:
        r = self._s.put(f"{self._url}/commands/{intent}", json=patch, timeout=self._timeout)
        if r.status_code >= 400:
            raise RuntimeError(self._detail(r))
        return r.json()

    def delete(self, intent: str) -> None:
        r = self._s.delete(f"{self._url}/commands/{intent}", timeout=self._timeout)
        if r.status_code >= 400:
            raise RuntimeError(self._detail(r))

    # -- voices ----------------------------------------------------------
    def voices(self) -> dict:
        r = self._s.get(f"{self._url}/voices", timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def set_voice(self, voice: str) -> dict:
        r = self._s.put(f"{self._url}/voices/active", json={"voice": voice}, timeout=self._timeout)
        if r.status_code >= 400:
            raise RuntimeError(self._detail(r))
        return r.json()

    def download_voice(self, voice: str) -> dict:
        r = self._s.post(f"{self._url}/voices/download", json={"voice": voice}, timeout=180)
        if r.status_code >= 400:
            raise RuntimeError(self._detail(r))
        return r.json()

    def test_phrase(self, text: str) -> dict:
        """Send a phrase through /command (no TTS) to see how it classifies."""
        r = self._s.post(f"{self._url}/command", json={"text": text, "speak": False},
                         timeout=max(self._timeout, 30))
        r.raise_for_status()
        return r.json()

    def health(self) -> dict:
        r = self._s.get(f"{self._url}/health", timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _detail(r: requests.Response) -> str:
        try:
            return f"{r.status_code}: {r.json().get('detail', r.text)}"
        except Exception:  # noqa: BLE001
            return f"{r.status_code}: {r.text}"
