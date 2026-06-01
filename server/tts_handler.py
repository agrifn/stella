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
import os
import re
import shlex
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import httpx

from .config import TTSConfig

log = logging.getLogger("stella.tts")

_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# A voice name is used to build file paths and the download URL, so it must not be
# able to traverse out (no '/', '\', '.' -> blocks '..', absolute and nested paths).
# Letters/digits/underscore/hyphen covers both Piper names (en_US-lessac-medium) and
# custom installed voices (e.g. cortana). The /voices/download endpoint is unauth'd,
# so this is validated BEFORE any path build or file write.
_VOICE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_voice_name(voice: str) -> None:
    if not _VOICE_RE.match(voice or ""):
        raise ValueError(f"invalid voice name: {voice!r}")


def _voice_url(voice: str, suffix: str) -> str:
    """Build the HF URL for a Piper voice file.

    A voice name like 'en_US-amy-medium' maps to en/en_US/amy/medium/<voice>.<suffix>.
    """
    region, name, quality = voice.split("-", 2)
    lang = region.split("_")[0]
    return f"{_HF_BASE}/{lang}/{region}/{name}/{quality}/{voice}.onnx{suffix}"


class TTSHandler:
    # Synthesis is the slow part of a spoken reply (neural TTS ~1s+). The LLM runs
    # at temperature 0, so a given command yields the SAME ack text every time -
    # caching by text makes repeated acks instant (no re-synthesis round trip).
    _CACHE_MAX = 128

    def __init__(self, cfg: TTSConfig):
        self._cfg = cfg
        self._dir = cfg.voices_dir
        self._active_file = self._dir / "active.txt"
        self._voice = self._load_active() or cfg.voice
        self._cache: "OrderedDict[tuple, bytes]" = OrderedDict()
        self._engine = os.environ.get("STELLA_TTS_ENGINE", "piper")
        self._chatterbox_url = os.environ.get(
            "STELLA_CHATTERBOX_URL", "http://host.docker.internal:8123")
        self._ready: Optional[bool] = None  # cached readiness (probed for chatterbox)

    @property
    def engine(self) -> str:
        return self._engine

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
        """Best-effort cached readiness. For chatterbox this reflects the last probe
        (call check_ready() to refresh); for piper it checks the voice model file."""
        if not self._cfg.enabled:
            return False
        if self._engine == "chatterbox":
            return bool(self._ready)
        return self._model_path().exists()

    async def check_ready(self) -> bool:
        """Probe whether synthesis can ACTUALLY succeed, and cache it. For chatterbox
        this pings the host service so /health never falsely reports ready when no
        service is listening; for piper it checks the voice file exists."""
        if not self._cfg.enabled:
            self._ready = False
            return False
        if self._engine != "chatterbox":
            self._ready = self._model_path().exists()
            return self._ready
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.get(self._chatterbox_url)  # any HTTP response = reachable
            ok = True
        except httpx.HTTPError:
            ok = False
        if ok != self._ready:
            log.warning("chatterbox TTS at %s: %s", self._chatterbox_url,
                        "reachable" if ok else "UNREACHABLE - no audio will be produced")
        self._ready = ok
        return ok

    # -- voice catalogue --------------------------------------------------
    def available_voices(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return sorted(p.stem for p in self._dir.glob("*.onnx"))

    def set_voice(self, voice: str) -> None:
        _validate_voice_name(voice)
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
        _validate_voice_name(voice)
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
        engine = os.environ.get("STELLA_TTS_ENGINE", "piper")
        key = (engine, self._voice, text.strip())
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)  # LRU touch
            return cached
        wav = await self._synthesize(engine, text)
        if wav:
            self._cache[key] = wav
            self._cache.move_to_end(key)
            while len(self._cache) > self._CACHE_MAX:
                self._cache.popitem(last=False)  # evict least-recently-used
        return wav

    async def _synthesize(self, engine: str, text: str) -> Optional[bytes]:
        if engine == "chatterbox":
            url = os.environ.get("STELLA_CHATTERBOX_URL", "http://host.docker.internal:8123")
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(f"{url}/synthesize", json={"text": text})
                r.raise_for_status()
                return r.content or None
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(self._cfg.piper_bin),
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
