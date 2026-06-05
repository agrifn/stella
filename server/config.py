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
    voice: str = "en_US-lessac-medium"
    sample_rate: int = 22050
    # Directory holding <voice>.onnx and <voice>.onnx.json
    voices_dir: Path = field(default_factory=lambda: SERVER_DIR / "voices")
    # TTS engine command (piper1-gpl, the engine voices are trained/exported with).
    # shlex-split, so a multi-token command like "python3 -m piper" works.
    piper_bin: str = "python3 -m piper"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8420
    keybinds_path: Path = CONFIG_DIR / "keybinds.json"
    tts: TTSConfig = field(default_factory=TTSConfig)
    # Embedding intent classifier (the LLM-free intent engine).
    classifier_model: str = "minishlab/potion-base-32M"
    classifier_reject: float = 0.45  # below this cosine -> 'chat' (not a command)
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
    )

    clf = data.get("classifier", {})
    return ServerConfig(
        host=_env("STELLA_HOST", srv.get("host", ServerConfig.host)),
        port=int(_env("STELLA_PORT", srv.get("port", ServerConfig.port))),
        tts=tts_cfg,
        classifier_model=_env("STELLA_CLASSIFIER_MODEL", clf.get("model", ServerConfig.classifier_model)),
        classifier_reject=float(clf.get("reject_threshold", ServerConfig.classifier_reject)),
        api_token=_env("STELLA_API_TOKEN", srv.get("api_token")),
    )
