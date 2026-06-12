"""Server configuration.

Loads settings from config/settings.json with sane defaults and allows
environment-variable overrides. Kept as a single immutable dataclass so the
rest of the server depends on a typed object, not on raw dict access.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo layout: this file is server/config.py (repo root is the parent of server/).
SERVER_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVER_DIR.parent
CONFIG_DIR = REPO_ROOT / "config"


@dataclass(frozen=True)
class TTSConfig:
    enabled: bool = True
    voice: str = "en_GB-jenny_dioco-medium"  # shipped default (free voice; a custom/Cortana voice is set per-user)
    sample_rate: int = 22050
    # Directory holding <voice>.onnx and <voice>.onnx.json
    voices_dir: Path = field(default_factory=lambda: SERVER_DIR / "voices")
    # TTS engine command (piper1-gpl, the engine voices are trained/exported with).
    # shlex-split, so a multi-token command like "python3 -m piper" works.
    piper_bin: str = "python3 -m piper"
    # Piper synthesis tuning - the biggest lever on how natural (vs robotic) any
    # voice sounds, with NO retraining. Defaults below lean slightly calmer and
    # less metronomic than Piper's stock (1.0 / 0.667 / 0.8), which reads as more
    # human and "in control" for a ship-assistant delivery. Per-voice overrides
    # live in config/tts_tuning.json (managed by the GUI); these are the fallback.
    #   length_scale    : phoneme duration. >1 slower/calmer, <1 faster/clipped.
    #   noise_scale     : voice expressiveness/variation.
    #   noise_w_scale   : phoneme-timing variation (cadence; higher = less robotic).
    #   sentence_silence: seconds of pause between sentences.
    length_scale: float = 1.06
    noise_scale: float = 0.62
    noise_w_scale: float = 0.85
    sentence_silence: float = 0.25


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8420
    keybinds_path: Path = CONFIG_DIR / "keybinds.json"
    tts: TTSConfig = field(default_factory=TTSConfig)
    # Embedding intent classifier (the LLM-free intent engine).
    classifier_model: str = "minishlab/potion-base-32M"
    classifier_reject: float = 0.45  # below this cosine -> 'chat' (not a command)
    # Gray zone: a command scoring in [reject, clarify) is too borderline to fire
    # blind, so the response sets clarify=True and the client asks "Say again?".
    classifier_clarify: float = 0.55
    # Optional shared secret (STELLA_API_TOKEN). When set, every route except
    # /health requires 'Authorization: Bearer <token>'. Unset = open (local use).
    api_token: str | None = None


def _env(name: str, default: str | None) -> str | None:
    return os.environ.get(name, default)


def load_config(settings_path: Path | None = None) -> ServerConfig:
    """Build a ServerConfig from settings.json (if present) + env overrides."""
    settings_path = settings_path or (CONFIG_DIR / "settings.json")
    data: dict = {}
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))

    srv = data.get("server", {})
    tts = data.get("tts", {})

    tts_defaults = TTSConfig()
    tts_cfg = TTSConfig(
        enabled=bool(tts.get("enabled", tts_defaults.enabled)),
        voice=_env("STELLA_TTS_VOICE", tts.get("voice", tts_defaults.voice)),
        sample_rate=int(tts.get("sample_rate", tts_defaults.sample_rate)),
        voices_dir=Path(_env("STELLA_VOICES_DIR", str(tts_defaults.voices_dir))),
        piper_bin=_env("STELLA_PIPER_BIN", tts_defaults.piper_bin),
        length_scale=float(tts.get("length_scale", tts_defaults.length_scale)),
        noise_scale=float(tts.get("noise_scale", tts_defaults.noise_scale)),
        noise_w_scale=float(tts.get("noise_w_scale", tts_defaults.noise_w_scale)),
        sentence_silence=float(tts.get("sentence_silence", tts_defaults.sentence_silence)),
    )

    clf = data.get("classifier", {})
    return ServerConfig(
        host=_env("STELLA_HOST", srv.get("host", ServerConfig.host)),
        port=int(_env("STELLA_PORT", srv.get("port", ServerConfig.port))),
        tts=tts_cfg,
        classifier_model=_env("STELLA_CLASSIFIER_MODEL", clf.get("model", ServerConfig.classifier_model)),
        classifier_reject=float(clf.get("reject_threshold", ServerConfig.classifier_reject)),
        classifier_clarify=float(clf.get("clarify_threshold", ServerConfig.classifier_clarify)),
        api_token=_env("STELLA_API_TOKEN", srv.get("api_token")),
    )
