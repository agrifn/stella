"""Optional knowledge module: answer factual Star Citizen questions from real data.

Multi-domain and feature-flagged. Each domain (ships, locations, commodities, ...)
is fetched from the StarCitizenWiki API and cached as a trimmed index on startup.
All entities go into ONE searchable pool tagged with their kind.

A small local model can't reliably route among several knowledge sub-intents, so
the LLM only has to recognize a single 'info' intent (a factual SC question). The
handler then fuzzy-matches the entity out of the (messy STT) speech across every
domain and the LLM composes a short spoken answer grounded ONLY in that entity's
cached data.

When disabled, none of this runs and the prompt has no 'info' intent - the command
path is completely unaffected.

Deferred (noted): items/weapons (12.7k entries -> on-demand, not a startup cache)
and galactapedia lore (slow + many stub articles). Adding a domain = one Domain
entry below.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Callable

import httpx

try:
    from rapidfuzz import fuzz
except ImportError:  # matching disabled -> feature degrades gracefully
    fuzz = None

log = logging.getLogger("stella.knowledge")

_API = "https://api.star-citizen.wiki/api/v2"
_KIND = "_kind"  # internal tag on each cached entity (its domain label)

_KNOWLEDGE_SYSTEM = (
    "You are STELLA, a Star Citizen ship AI assisting a pilot. Answer the pilot's question "
    "using ONLY the JSON {label} data provided - do not invent numbers or facts. Be concise and "
    "spoken, 1-2 short sentences. Use plain numbers (e.g. '2500 HP', '25 percent'). For ships, "
    "armor damage_multiplier values are damage taken: 0.75 means 25 percent resistance. If the "
    "data does not contain the answer, say so briefly."
)


def _short(v, n: int = 500):
    """Truncated string, or None if the field isn't a plain string (some API
    'description' fields are nested objects/null)."""
    return v[:n] if isinstance(v, str) and v else None


# -- per-domain trims (keep only fields worth answering on) ------------------
def _trim_vehicle(v: dict) -> dict:
    shield = v.get("shield") or {}
    return {
        "name": v.get("name"), "manufacturer": (v.get("manufacturer") or {}).get("name"),
        "class": v.get("class_name"), "hull_health": v.get("health"),
        "armor": v.get("armor"), "shield_hp": v.get("shield_hp"),
        "shield": {k: shield.get(k) for k in ("hp", "regeneration", "resistance") if k in shield},
        "speed": v.get("speed"), "cargo_capacity": v.get("cargo_capacity"),
        "crew": v.get("crew"), "mass": v.get("mass"),
    }


def _trim_location(o: dict) -> dict:
    return {
        "name": o.get("name"), "type": o.get("type"), "designation": o.get("designation"),
        "habitable": o.get("habitable"), "distance": o.get("distance"),
        "status": o.get("status"), "description": _short(o.get("description")),
    }


def _trim_commodity(c: dict) -> dict:
    return {
        "name": c.get("name"), "tier": c.get("tier"), "is_mineable": c.get("is_mineable"),
        "density_g_per_cc": c.get("density_g_per_cc"), "description": _short(c.get("description")),
    }


@dataclass
class Domain:
    label: str                  # tag + compose-prompt label
    endpoints: list[str]        # API paths to paginate (concatenated)
    trim: Callable[[dict], dict]


_DOMAINS: list[Domain] = [
    Domain("ship/vehicle", ["vehicles"], _trim_vehicle),
    Domain("location", ["celestial-objects", "starsystems"], _trim_location),
    Domain("commodity", ["commodities"], _trim_commodity),
]


def _match(items: list[dict], text: str) -> dict | None:
    """Two-tier fuzzy match of an entity name out of the text. Tier 1: whole name
    present (prefers the most specific). Tier 2: distinctive tokens covered."""
    if not items or fuzz is None:
        return None
    tl = text.lower()
    words = set(re.findall(r"[a-z0-9]+", tl))

    best, best_adj = None, 0.0
    for it in items:
        name = (it.get("name") or "").lower()
        if not name:
            continue
        score = fuzz.partial_ratio(name, tl)
        if score >= 90:
            adj = score + min(len(name), 30) * 0.4
            if adj > best_adj:
                best, best_adj = it, adj
    if best:
        return best

    best, best_score = None, 0.0
    for it in items:
        ntoks = [t for t in re.findall(r"[a-z0-9]+", (it.get("name") or "").lower()) if len(t) >= 3]
        if not ntoks:
            continue
        matched = sum(1 for t in ntoks if any(fuzz.ratio(t, w) >= 88 for w in words))
        coverage = matched / len(ntoks)
        if matched >= 1 and coverage >= 0.5:
            score = coverage * 100 - len(ntoks)
            if score > best_score:
                best, best_score = it, score
    return best


class KnowledgeHandler:
    INTENT = "info"

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._all: list[dict] = []     # every entity, each tagged with _KIND
        self._ready = asyncio.Event()

    @property
    def intents(self) -> set[str]:
        return {self.INTENT} if self.enabled else set()

    # -- prompt contributions --------------------------------------------
    def intent_lines(self) -> list[str]:
        if not self.enabled:
            return []
        return ["- info: the pilot asks a FACTUAL question about a Star Citizen SHIP/vehicle, "
                "LOCATION (planet, moon, space station, star system), or COMMODITY/trade good - "
                "its stats, where it is, or what it is. ANY 'what is X / where is X / what type "
                "is X / how fast/big/strong is X / tell me about X' where X is a name is info, "
                "even if you don't recognise the name. A ship/place/commodity NAME with a stat "
                'word (armor, shields, speed, cargo) is info, NOT a power command.']

    def examples(self) -> list[str]:
        if not self.enabled:
            return []
        def ex(text):
            return (f'Input: "{text}"\n'
                    f'Output: {{"intent":"info","confirm_required":false,"response_text":""}}')
        return [ex("what is the guardian MX armor"), ex("cutlass black shields"),
                ex("where is crusader"), ex("what type is hurston"),
                ex("is laranite mineable")]

    # -- index loading ----------------------------------------------------
    async def load(self) -> None:
        if not self.enabled:
            self._ready.set()
            return
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for d in _DOMAINS:
                try:
                    items = await self._fetch(client, d)
                    for it in items:
                        it[_KIND] = d.label
                    self._all.extend(items)
                    log.info("knowledge: cached %d %s", len(items), d.label)
                except Exception:  # noqa: BLE001 - one domain failing shouldn't sink the rest
                    log.exception("knowledge: failed to load %s", d.label)
        self._ready.set()

    async def _fetch(self, client: httpx.AsyncClient, d: Domain) -> list[dict]:
        out: list[dict] = []
        for ep in d.endpoints:
            page = 1
            while True:
                r = await client.get(f"{_API}/{ep}",
                                     params={"page[size]": 100, "page[number]": page})
                r.raise_for_status()
                j = r.json()
                out.extend(d.trim(v) for v in j.get("data", []) if v.get("name"))
                last = int((j.get("meta") or {}).get("last_page", page))
                if page >= last:
                    break
                page += 1
        return out

    # -- answer -----------------------------------------------------------
    async def answer(self, intent: str, text: str, llm) -> str | None:
        if not self.enabled or intent != self.INTENT:
            return None
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=12)
        except asyncio.TimeoutError:
            return "Database is still loading, try again in a moment."
        if not self._all:
            return "The knowledge database is unavailable right now."
        entity = _match(self._all, text)
        if not entity:
            return "I couldn't identify what you're asking about."
        label = entity.get(_KIND, "thing")
        clean = {k: v for k, v in entity.items() if k != _KIND}
        user = f"Question: {text}\nData: {json.dumps(clean, separators=(',', ':'))}"
        try:
            return (await llm.generate(_KNOWLEDGE_SYSTEM.format(label=label), user)).strip()
        except Exception:  # noqa: BLE001
            log.exception("knowledge: compose failed")
            return None
