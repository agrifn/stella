"""TTS handler. PIPER-ONLY by default; Chatterbox is an optional legacy engine.

STELLA ships Piper-only (the custom Cortana voice). Both routes default to Piper:
  - "ack"  route -> command acks on the latency-critical path ("Boosting.").
  - "chat" route -> the rare chat reply (now only over-split parts / rejects, since
                    the classifier replaced the LLM/knowledge replies).

The two-route split is kept (it is free) so an ack vs chat engine COULD differ, but
both default to Piper. Chatterbox remains reachable only if you explicitly point a
route at it and run the host-side service. Engines are chosen by env:
  STELLA_TTS_ACK_ENGINE  (default "piper")
  STELLA_TTS_CHAT_ENGINE (default: STELLA_TTS_ENGINE, else "piper")
  STELLA_TTS_ENGINE      (legacy single-engine fallback for the chat route)

Each engine's audio is wrapped/declared at ITS OWN sample rate (Piper from the
voice's .onnx.json; Chatterbox returns a full WAV at its own rate), never one
hardcoded value, so neither plays at the wrong pitch.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shlex
import wave
import zipfile
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
    # The LLM runs at temperature 0, so a given command/answer yields the SAME text
    # every time - caching by (engine, voice, text) makes repeats instant.
    _CACHE_MAX = 256

    def __init__(self, cfg: TTSConfig):
        self._cfg = cfg
        self._dir = cfg.voices_dir
        self._active_file = self._dir / "active.txt"
        self._voice = self._load_active() or cfg.voice
        # Per-voice synthesis tuning overrides (length/noise/silence). Lives beside
        # the voices so a voice keeps its dialed-in delivery across restarts; any
        # voice without an entry uses the TTSConfig defaults.
        self._tuning_file = self._dir / "tuning.json"
        self._tuning: dict[str, dict] = self._load_tuning()
        self._cache: "OrderedDict[tuple, bytes]" = OrderedDict()
        # Per-route engines. Acks default to Piper (fast); chat falls back to the
        # legacy STELLA_TTS_ENGINE so existing single-engine setups keep working.
        self._ack_engine = os.environ.get("STELLA_TTS_ACK_ENGINE") or "piper"
        self._chat_engine = (os.environ.get("STELLA_TTS_CHAT_ENGINE")
                             or os.environ.get("STELLA_TTS_ENGINE") or "piper")
        self._chatterbox_url = os.environ.get(
            "STELLA_CHATTERBOX_URL", "http://host.docker.internal:8123")
        self._ready_cache: dict[str, bool] = {}   # engine name -> last probe result

    # -- engine routing ---------------------------------------------------
    @property
    def ack_engine(self) -> str:
        return self._ack_engine

    @property
    def chat_engine(self) -> str:
        return self._chat_engine

    @property
    def engine(self) -> str:  # summary for the startup log
        return f"ack={self._ack_engine},chat={self._chat_engine}"

    def _engine_for(self, route: str) -> str:
        return self._ack_engine if route == "ack" else self._chat_engine

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

    # -- synthesis tuning (per-voice naturalness knobs) ------------------
    _TUNING_KEYS = ("length_scale", "noise_scale", "noise_w_scale", "sentence_silence")

    def _load_tuning(self) -> dict[str, dict]:
        try:
            data = json.loads(self._tuning_file.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            return {}

    def _save_tuning(self) -> None:
        try:
            self._tuning_file.write_text(json.dumps(self._tuning, indent=2), encoding="utf-8")
        except OSError:
            log.warning("could not persist voice tuning")

    def tuning_for(self, voice: Optional[str] = None) -> dict:
        """Effective tuning for a voice: its saved overrides on top of the config
        defaults. Always returns all four keys, so callers never miss one."""
        base = {
            "length_scale": self._cfg.length_scale,
            "noise_scale": self._cfg.noise_scale,
            "noise_w_scale": self._cfg.noise_w_scale,
            "sentence_silence": self._cfg.sentence_silence,
        }
        override = self._tuning.get(voice or self._voice, {})
        for k in self._TUNING_KEYS:
            if isinstance(override.get(k), (int, float)):
                base[k] = float(override[k])
        return base

    def set_tuning(self, voice: str, values: dict) -> dict:
        """Persist per-voice tuning overrides (only the recognized keys). Passing an
        empty dict clears the voice back to the config defaults. Returns the new
        effective tuning. Clears the cache so the change is audible immediately."""
        _validate_voice_name(voice)
        clean = {k: float(values[k]) for k in self._TUNING_KEYS
                 if isinstance(values.get(k), (int, float))}
        if clean:
            self._tuning[voice] = clean
        else:
            self._tuning.pop(voice, None)
        self._save_tuning()
        self._cache.clear()  # cached audio used the old tuning
        log.info("tuning set for %s: %s", voice, clean or "(reset to defaults)")
        return self.tuning_for(voice)

    # -- readiness --------------------------------------------------------
    def _engine_ready(self, engine: str) -> bool:
        if engine == "chatterbox":
            return bool(self._ready_cache.get("chatterbox"))
        return self._model_path().exists()  # piper: the ack voice file

    @property
    def ready(self) -> bool:
        """Command-path readiness = the ACK engine can synthesize (the critical path).
        A downed chat engine does NOT make the system 'not ready'."""
        return self._cfg.enabled and self._engine_ready(self._ack_engine)

    def chat_ready(self) -> bool:
        return self._cfg.enabled and self._engine_ready(self._chat_engine)

    async def check_ready(self) -> bool:
        """Probe BOTH engines and cache results. Returns ACK-engine readiness (what
        /health treats as the critical path); chat readiness is surfaced separately."""
        if not self._cfg.enabled:
            return False
        for engine in {self._ack_engine, self._chat_engine}:
            if engine == "chatterbox":
                try:
                    async with httpx.AsyncClient(timeout=3.0) as client:
                        await client.get(self._chatterbox_url)  # any response = reachable
                    ok = True
                except httpx.HTTPError:
                    ok = False
                if ok != self._ready_cache.get("chatterbox"):
                    log.warning("chatterbox TTS at %s: %s", self._chatterbox_url,
                                "reachable" if ok else "UNREACHABLE - chat replies will be silent")
                self._ready_cache["chatterbox"] = ok
            else:  # piper
                self._ready_cache["piper"] = self._model_path().exists()
        return self.ready

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
        log.info("active (Piper ack) voice set to %s", voice)

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

    # -- share: export / import a voice bundle ---------------------------
    def export_bundle(self, name: str) -> bytes:
        """Zip a voice (<name>.onnx + <name>.onnx.json) into a single shareable
        bundle. The user hands this file to a friend, who imports it. Keeps custom
        voices OUT of the repo/distribution - they travel as user files, not code."""
        _validate_voice_name(name)
        onnx = self._dir / f"{name}.onnx"
        cfg = self._dir / f"{name}.onnx.json"
        if not onnx.exists():
            raise FileNotFoundError(f"voice not installed: {name}")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(onnx, f"{name}.onnx")
            if cfg.exists():
                z.write(cfg, f"{name}.onnx.json")
        return buf.getvalue()

    def import_bundle(self, data: bytes, make_active: bool = False) -> str:
        """Install a voice from a .zip bundle (one .onnx plus its .onnx.json) and
        return the installed name. Only the member BASENAMES are used and the install
        name is validated, so a crafted zip cannot write outside the voices dir. Written
        atomically (both files or neither)."""
        onnx_bytes = json_bytes = None
        name = None
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    base = Path(info.filename).name
                    if base.endswith(".onnx.json"):
                        json_bytes = z.read(info)
                    elif base.endswith(".onnx"):
                        onnx_bytes = z.read(info)
                        name = base[: -len(".onnx")]
        except zipfile.BadZipFile as e:
            raise ValueError("not a valid voice bundle (.zip)") from e
        if not onnx_bytes or not name:
            raise ValueError("bundle has no .onnx voice file")
        if not json_bytes:
            raise ValueError("bundle is missing the .onnx.json config")
        _validate_voice_name(name)
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp_o = self._dir / f"{name}.onnx.part"
        tmp_j = self._dir / f"{name}.onnx.json.part"
        try:
            tmp_o.write_bytes(onnx_bytes)
            tmp_j.write_bytes(json_bytes)
        except Exception:
            tmp_o.unlink(missing_ok=True)
            tmp_j.unlink(missing_ok=True)
            raise
        tmp_o.replace(self._dir / f"{name}.onnx")
        tmp_j.replace(self._dir / f"{name}.onnx.json")
        if make_active:
            self.set_voice(name)
        log.info("imported voice bundle %s (active=%s)", name, make_active)
        return name

    # -- synthesis --------------------------------------------------------
    def _voice_rate(self) -> int:
        """Native sample rate of the active Piper voice (from its .onnx.json) so the
        WAV header matches the PCM. Falls back to the configured rate."""
        try:
            j = json.loads((self._dir / f"{self._voice}.onnx.json").read_text(encoding="utf-8"))
            return int((j.get("audio") or {}).get("sample_rate") or self._cfg.sample_rate)
        except Exception:  # noqa: BLE001
            return self._cfg.sample_rate

    def _wrap_wav(self, pcm: bytes, rate: int) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm)
        return buf.getvalue()

    async def synthesize(self, text: str, route: str = "ack") -> Optional[bytes]:
        """Synthesize text via the engine configured for the route (both default to
        Piper). Fails SOFT (returns None) so a downed chat engine never breaks the
        ack/command path."""
        if not self._cfg.enabled or not text.strip():
            return None
        engine = self._engine_for(route)
        # Tuning is part of the identity of the audio: a re-tune must not return a
        # stale cached clip. (set_tuning also clears the cache; this guards repeats
        # that span an env/default change too.)
        tuning_sig = tuple(sorted(self.tuning_for().items())) if engine == "piper" else ()
        key = (engine, self._voice, text.strip(), tuning_sig)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)  # LRU touch
            return cached
        try:
            wav = await self._synthesize(engine, text)
        except Exception:  # noqa: BLE001 - TTS is non-critical; never raise to caller
            log.exception("TTS synth failed (engine=%s route=%s)", engine, route)
            return None
        if wav:
            self._cache[key] = wav
            self._cache.move_to_end(key)
            while len(self._cache) > self._CACHE_MAX:
                self._cache.popitem(last=False)  # evict least-recently-used
        return wav

    async def _synthesize(self, engine: str, text: str) -> Optional[bytes]:
        if engine == "chatterbox":
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(f"{self._chatterbox_url}/synthesize", json={"text": text})
                r.raise_for_status()
                return r.content or None  # host service returns a full WAV at its own rate
        t = self.tuning_for()
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(self._cfg.piper_bin),
            "--model", str(self._model_path()),
            "--output-raw",
            "--length-scale", str(t["length_scale"]),
            "--noise-scale", str(t["noise_scale"]),
            "--noise-w-scale", str(t["noise_w_scale"]),
            "--sentence-silence", str(t["sentence_silence"]),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pcm, err = await proc.communicate(input=text.encode("utf-8"))
        if proc.returncode != 0 or not pcm:
            raise RuntimeError(
                f"piper failed (rc={proc.returncode}): {err.decode('utf-8', 'ignore')[:300]}"
            )
        return self._wrap_wav(pcm, self._voice_rate())
