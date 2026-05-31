"""Piper TTS integration with switchable / downloadable voices.

Runs the piper binary as a subprocess (text in on stdin, raw 16-bit PCM out on
stdout) and wraps the PCM in a WAV in-memory. The active voice can be changed at
runtime and new Piper voices downloaded from the rhasspy/piper-voices repo. The
active voice is persisted in the voices dir so it survives restarts.
"""
from __future__ import annotations

import asyncio
import io
import logging
import wave
from pathlib import Path
from typing import Optional

import httpx

from .config import TTSConfig

log = logging.getLogger("stella.tts")

_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _voice_url(voice: str, suffix: str) -> str:
    """Build the HF URL for a Piper voice file.

    A voice name like 'en_US-amy-medium' maps to en/en_US/amy/medium/<voice>.<suffix>.
    """
    region, name, quality = voice.split("-", 2)
    lang = region.split("_")[0]
    return f"{_HF_BASE}/{lang}/{region}/{name}/{quality}/{voice}.onnx{suffix}"


class TTSHandler:
    def __init__(self, cfg: TTSConfig):
        self._cfg = cfg
        self._dir = cfg.voices_dir
        self._active_file = self._dir / "active.txt"
        self._voice = self._load_active() or cfg.voice

    # -- active voice persistence ----------------------------------------
    def _load_active(self) -> Optional[str]:
        try:
            v = self._active_file.read_text(encoding="utf-8").strip()
            if v and (self._dir / f"{v}.onnx").exists():
                return v
        except OSError:
            pass
        return None

    def _save_active(self) -> None:
        try:
            self._active_file.write_text(self._voice, encoding="utf-8")
        except OSError:
            log.warning("could not persist active voice")

    @property
    def voice(self) -> str:
        return self._voice

    def _model_path(self, voice: Optional[str] = None) -> Path:
        return self._dir / f"{voice or self._voice}.onnx"

    @property
    def ready(self) -> bool:
        return self._cfg.enabled and self._model_path().exists()

    # -- voice catalogue --------------------------------------------------
    def available_voices(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return sorted(p.stem for p in self._dir.glob("*.onnx"))

    def set_voice(self, voice: str) -> None:
        if not (self._dir / f"{voice}.onnx").exists():
            raise FileNotFoundError(f"voice not installed: {voice}")
        self._voice = voice
        self._save_active()
        log.info("active voice set to %s", voice)

    async def download_voice(self, voice: str) -> None:
        """Fetch a Piper voice (.onnx + .onnx.json) by name into the voices dir.

        Both files are written to temp paths and only committed once BOTH succeed,
        so a mid-download failure never leaves a half-installed (unusable) voice.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp: dict[str, Path] = {}
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                for suffix in ("", ".json"):
                    r = await client.get(_voice_url(voice, suffix))
                    r.raise_for_status()
                    t = self._dir / f"{voice}.onnx{suffix}.part"
                    t.write_bytes(r.content)
                    tmp[suffix] = t
        except Exception:
            for t in tmp.values():
                t.unlink(missing_ok=True)
            raise
        for suffix, t in tmp.items():
            t.replace(self._dir / f"{voice}.onnx{suffix}")
        log.info("downloaded voice %s", voice)

    # -- synthesis --------------------------------------------------------
    def _wrap_wav(self, pcm: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._cfg.sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()

    async def synthesize(self, text: str) -> Optional[bytes]:
        if not self.ready or not text.strip():
            return None
        proc = await asyncio.create_subprocess_exec(
            self._cfg.piper_bin,
            "--model", str(self._model_path()),
            "--output-raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pcm, err = await proc.communicate(input=text.encode("utf-8"))
        if proc.returncode != 0 or not pcm:
            raise RuntimeError(
                f"piper failed (rc={proc.returncode}): {err.decode('utf-8', 'ignore')[:300]}"
            )
        return self._wrap_wav(pcm)
