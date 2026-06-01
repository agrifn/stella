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
PROMPTS_DIR = SERVER_DIR / "prompts"


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "ollama"  # ollama | openai | anthropic
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2:3b"
    temperature: float = 0.0
    # 4096 (not 2048): the system prompt (37 commands + knowledge intents/examples)
    # is ~2050 tokens, which overflowed a 2048 context and made Ollama TRUNCATE the
    # prompt -> the model lost command definitions and misclassified. Must be the SAME
    # value on every Ollama call (the provider pins it) or the model reloads.
    num_ctx: int = 4096
    # config/settings.json sets this to "24h" on purpose: keeping the model resident
    # avoids multi-second cold reloads. This 30m fallback only applies with no settings.
    keep_alive: str = "30m"
    # For external providers (openai-compatible / anthropic):
    base_url: str | None = None       # override endpoint (OpenRouter, Groq, LM Studio...)
    api_key: str | None = None        # prefer the STELLA_LLM_API_KEY env var


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
class KnowledgeConfig:
    # Optional factual lookups (SC ship/equipment stats, where-to-buy, crafting).
    # DISABLED by default: STELLA is a pure voice-command assistant. When off, the
    # 'info' intent is never added to the prompt and no data is fetched (zero
    # overhead), and the LLM only classifies commands vs chat. Set enabled=true (or
    # STELLA_KNOWLEDGE_ENABLED=1) to bring it back.
    enabled: bool = False
    # UEX Corp API token (free, from uexcorp.space/api/apps). When set AND knowledge
    # is enabled, STELLA can answer "where to buy / how much" for items, weapons,
    # armor and ship components. Prefer the STELLA_UEX_TOKEN env var - never commit it.
    uex_token: str | None = None
    uex_base: str = "https://api.uexcorp.space/2.0"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8420
    keybinds_path: Path = CONFIG_DIR / "keybinds.json"
    system_prompt_path: Path = PROMPTS_DIR / "stella_system.txt"
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
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
    llm = data.get("llm", {})
    tts = data.get("tts", {})

    llm_cfg = LLMConfig(
        provider=_env("STELLA_LLM_PROVIDER", llm.get("provider", LLMConfig.provider)),
        ollama_url=_env("STELLA_OLLAMA_URL", llm.get("ollama_url", LLMConfig.ollama_url)),
        model=_env("STELLA_LLM_MODEL", llm.get("model", LLMConfig.model)),
        temperature=float(llm.get("temperature", LLMConfig.temperature)),
        num_ctx=int(llm.get("num_ctx", LLMConfig.num_ctx)),
        keep_alive=str(llm.get("keep_alive", LLMConfig.keep_alive)),
        base_url=_env("STELLA_LLM_BASE_URL", llm.get("base_url", LLMConfig.base_url)),
        api_key=_env("STELLA_LLM_API_KEY", llm.get("api_key", LLMConfig.api_key)),
    )

    tts_defaults = TTSConfig()
    tts_cfg = TTSConfig(
        enabled=bool(tts.get("enabled", tts_defaults.enabled)),
        voice=_env("STELLA_TTS_VOICE", tts.get("voice", tts_defaults.voice)),
        sample_rate=int(tts.get("sample_rate", tts_defaults.sample_rate)),
        voices_dir=Path(_env("STELLA_VOICES_DIR", str(tts_defaults.voices_dir))),
        piper_bin=_env("STELLA_PIPER_BIN", tts_defaults.piper_bin),
    )

    know = data.get("knowledge", {})
    know_enabled = _env("STELLA_KNOWLEDGE_ENABLED", None)
    knowledge_cfg = KnowledgeConfig(
        enabled=(know_enabled.lower() in ("1", "true", "yes")) if know_enabled is not None
        else bool(know.get("enabled", KnowledgeConfig.enabled)),
        uex_token=_env("STELLA_UEX_TOKEN", know.get("uex_token")),
        uex_base=_env("STELLA_UEX_BASE", know.get("uex_base", KnowledgeConfig.uex_base)),
    )

    return ServerConfig(
        host=_env("STELLA_HOST", srv.get("host", ServerConfig.host)),
        port=int(_env("STELLA_PORT", srv.get("port", ServerConfig.port))),
        llm=llm_cfg,
        tts=tts_cfg,
        knowledge=knowledge_cfg,
        api_token=_env("STELLA_API_TOKEN", srv.get("api_token")),
    )
