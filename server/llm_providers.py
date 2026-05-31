"""Pluggable LLM providers for intent parsing.

All providers take a system prompt + the pilot's text and return an IntentResult
(intent, confirm_required, response_text). The server resolves the keybind itself,
so providers only classify + speak.

Providers:
  - OllamaProvider:    local Ollama, uses structured-output `format` schema.
  - OpenAIProvider:    any OpenAI-compatible /chat/completions endpoint (OpenAI,
                       OpenRouter, Groq, LM Studio, vLLM, ...) via base_url + key.
  - AnthropicProvider: Anthropic Messages API, forces a tool call for clean JSON.

Implemented with httpx directly (no heavy SDKs) to keep the backend container small.
"""
from __future__ import annotations

import abc
import json
import logging

import httpx

from .config import LLMConfig
from .models import IntentResult

log = logging.getLogger("stella.llm")

# The structure every provider must produce.
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "confirm_required": {"type": "boolean"},
        "response_text": {"type": "string"},
    },
    "required": ["intent", "confirm_required", "response_text"],
}


def _result_from_obj(data: dict) -> IntentResult:
    return IntentResult(
        intent=str(data.get("intent", "chat")),
        confirm_required=bool(data.get("confirm_required", False)),
        response_text=str(data.get("response_text", "")),
    )


def _extract_json(text: str) -> dict:
    """Parse JSON, tolerating a stray code fence or surrounding prose."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


class IntentProvider(abc.ABC):
    @abc.abstractmethod
    async def parse_intent(self, system_prompt: str, text: str) -> IntentResult: ...

    async def warm(self) -> None:  # optional; default no-op
        return None

    async def generate(self, system_prompt: str, user: str) -> str:
        """Free-form completion (used by the knowledge module). Default unsupported."""
        raise NotImplementedError

    @abc.abstractmethod
    async def health(self) -> bool: ...

    async def aclose(self) -> None:
        return None


class OllamaProvider(IntentProvider):
    def __init__(self, cfg: LLMConfig):
        self._cfg = cfg
        self._client = httpx.AsyncClient(base_url=cfg.ollama_url, timeout=60.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _options(self, **extra) -> dict:
        # num_ctx MUST be identical on every call, otherwise Ollama reloads the
        # model (a multi-second stall) whenever the requested context differs.
        return {"temperature": self._cfg.temperature, "num_ctx": self._cfg.num_ctx, **extra}

    async def warm(self) -> None:
        try:
            await self._client.post("/api/chat", json={
                "model": self._cfg.model,
                "messages": [{"role": "user", "content": "ok"}],
                "stream": False, "keep_alive": self._cfg.keep_alive,
                "options": self._options(num_predict=1),
            })
        except httpx.HTTPError:
            pass

    async def parse_intent(self, system_prompt: str, text: str) -> IntentResult:
        r = await self._client.post("/api/chat", json={
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "format": INTENT_SCHEMA,
            "keep_alive": self._cfg.keep_alive,
            "options": self._options(),
        })
        r.raise_for_status()
        return _result_from_obj(_extract_json(r.json()["message"]["content"]))

    async def generate(self, system_prompt: str, user: str) -> str:
        r = await self._client.post("/api/chat", json={
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": self._cfg.keep_alive,
            "options": self._options(num_predict=160),
        })
        r.raise_for_status()
        return r.json()["message"]["content"]

    async def health(self) -> bool:
        try:
            return (await self._client.get("/api/tags")).status_code == 200
        except httpx.HTTPError:
            return False


class OpenAIProvider(IntentProvider):
    """OpenAI-compatible chat completions (OpenAI, OpenRouter, Groq, LM Studio...)."""
    def __init__(self, cfg: LLMConfig):
        self._cfg = cfg
        base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        self._client = httpx.AsyncClient(base_url=base, headers=headers, timeout=60.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def parse_intent(self, system_prompt: str, text: str) -> IntentResult:
        r = await self._client.post("/chat/completions", json={
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": self._cfg.temperature,
            # json_object is the most widely supported structured mode across
            # OpenAI-compatible servers; the system prompt already pins the shape.
            "response_format": {"type": "json_object"},
        })
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return _result_from_obj(_extract_json(content))

    async def generate(self, system_prompt: str, user: str) -> str:
        r = await self._client.post("/chat/completions", json={
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user},
            ],
            "temperature": self._cfg.temperature,
            "max_tokens": 160,
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def health(self) -> bool:
        try:
            r = await self._client.get("/models")
            return r.status_code < 500
        except httpx.HTTPError:
            return False


class AnthropicProvider(IntentProvider):
    """Anthropic Messages API; forces a tool call to get reliable structured JSON."""
    _TOOL = {
        "name": "set_intent",
        "description": "Record the parsed ship command intent.",
        "input_schema": INTENT_SCHEMA,
    }

    def __init__(self, cfg: LLMConfig):
        self._cfg = cfg
        base = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=base,
            headers={
                "x-api-key": cfg.api_key or "",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=60.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def parse_intent(self, system_prompt: str, text: str) -> IntentResult:
        r = await self._client.post("/v1/messages", json={
            "model": self._cfg.model,
            "max_tokens": 256,
            "temperature": self._cfg.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": text}],
            "tools": [self._TOOL],
            "tool_choice": {"type": "tool", "name": "set_intent"},
        })
        r.raise_for_status()
        for block in r.json().get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "set_intent":
                return _result_from_obj(block["input"])
        raise RuntimeError("Anthropic did not return the expected tool call")

    async def generate(self, system_prompt: str, user: str) -> str:
        r = await self._client.post("/v1/messages", json={
            "model": self._cfg.model,
            "max_tokens": 160,
            "temperature": self._cfg.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user}],
        })
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", [])
                       if b.get("type") == "text")

    async def health(self) -> bool:
        # No unauthenticated health route; treat a configured key as healthy.
        return bool(self._cfg.api_key)


def make_provider(cfg: LLMConfig) -> IntentProvider:
    provider = (cfg.provider or "ollama").lower()
    if provider == "ollama":
        return OllamaProvider(cfg)
    if provider in ("openai", "openai-compatible", "openrouter", "groq"):
        return OpenAIProvider(cfg)
    if provider == "anthropic":
        return AnthropicProvider(cfg)
    raise ValueError(f"unknown LLM provider: {cfg.provider!r}")
