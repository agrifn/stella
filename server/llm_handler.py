"""Orchestrates intent parsing over a pluggable LLM provider.

Holds the chosen provider (Ollama / OpenAI-compatible / Anthropic) and a callable
that yields the current system prompt, so the prompt always reflects the live
command set. The provider does the actual model call; this class is the seam the
rest of the server depends on.
"""
from __future__ import annotations

from typing import Callable

from .config import LLMConfig
from .llm_providers import IntentProvider, make_provider
from .models import IntentResult


class LLMHandler:
    def __init__(self, cfg: LLMConfig, system_prompt_provider: Callable[[], str]):
        self._cfg = cfg
        self._system_prompt_provider = system_prompt_provider
        self._provider: IntentProvider = make_provider(cfg)

    @property
    def provider_name(self) -> str:
        return self._cfg.provider

    async def aclose(self) -> None:
        await self._provider.aclose()

    async def warm(self) -> None:
        await self._provider.warm()

    async def parse_intent(self, text: str) -> IntentResult:
        return await self._provider.parse_intent(self._system_prompt_provider(), text)

    async def health(self) -> bool:
        return await self._provider.health()
