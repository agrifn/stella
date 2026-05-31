"""Optional knowledge module: answer factual Star Citizen questions from real data.

V1 covers ship/vehicle stats via the StarCitizenWiki API. On startup it caches a
trimmed vehicle index in memory; a question fuzzy-matches a ship name out of the
(often messy STT) speech, and the LLM composes a short spoken answer grounded
ONLY in that ship's cached data.

Feature-flagged: when disabled, nothing here runs and the prompt never gets the
'ship_info' intent, so the command path is completely unaffected.

The design is domain-agnostic - the same fetch/cache/match/compose pipeline will
extend to items, weapons, locations, etc. by adding more index loaders.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

try:
    from rapidfuzz import fuzz
except ImportError:  # rapidfuzz missing -> matching disabled, feature degrades gracefully
    fuzz = None

log = logging.getLogger("stella.knowledge")

_API = "https://api.star-citizen.wiki/api/v2"

# Which damage type each armor multiplier key represents (for nicer answers).
_KNOWLEDGE_SYSTEM = (
    "You are STELLA, a Star Citizen ship AI assisting a pilot. Answer the pilot's question "
    "using ONLY the JSON ship data provided - do not invent numbers. Be concise and spoken, "
    "1-2 short sentences. Use plain numbers (e.g. '2500 HP', '25 percent'). Armor "
    "damage_multiplier values are damage taken: 0.75 means 25 percent resistance. If the data "
    "does not contain the answer, say so briefly."
)


def _trim_vehicle(v: dict) -> dict:
    """Keep only the stat fields worth answering on (drop huge hardpoint/part arrays)."""
    shield = v.get("shield") or {}
    return {
        "name": v.get("name"),
        "manufacturer": (v.get("manufacturer") or {}).get("name"),
        "class": v.get("class_name"),
        "hull_health": v.get("health"),
        "armor": v.get("armor"),
        "shield_hp": v.get("shield_hp"),
        "shield": {k: shield.get(k) for k in ("hp", "regeneration", "resistance", "absorption")
                   if k in shield},
        "speed": v.get("speed"),
        "cargo_capacity": v.get("cargo_capacity"),
        "crew": v.get("crew"),
        "mass": v.get("mass"),
    }


class KnowledgeHandler:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._ships: list[dict] = []
        self._ready = asyncio.Event()

    # -- index loading ----------------------------------------------------
    async def load(self) -> None:
        """Background-fetch + cache the vehicle index. Safe to call when disabled."""
        if not self.enabled:
            self._ready.set()
            return
        try:
            self._ships = await self._fetch_all_vehicles()
            log.info("knowledge: cached %d vehicles", len(self._ships))
        except Exception:  # noqa: BLE001 - keep the server up even if the API is down
            log.exception("knowledge: vehicle index load failed (lookups will say unavailable)")
        finally:
            self._ready.set()

    async def _fetch_all_vehicles(self) -> list[dict]:
        out: list[dict] = []
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            page = 1
            while True:
                r = await client.get(f"{_API}/vehicles",
                                     params={"page[size]": 100, "page[number]": page})
                r.raise_for_status()
                j = r.json()
                out.extend(_trim_vehicle(v) for v in j.get("data", []) if v.get("name"))
                last = int((j.get("meta") or {}).get("last_page", page))
                if page >= last:
                    break
                page += 1
        return out

    # -- matching ---------------------------------------------------------
    def match(self, text: str) -> dict | None:
        """Resolve a ship from the (messy STT) text. Two tiers:
        1) the full ship name appears in the text ('guardian mx' -> 'Guardian MX'),
           preferring the most specific (longest) full match;
        2) token coverage - the ship's distinctive name words appear in the text
           ('hornet' -> 'F7C Hornet'), preferring the most-covered / most-base name.
        """
        if not self._ships or fuzz is None:
            return None
        tl = text.lower()
        words = set(re.findall(r"[a-z0-9]+", tl))

        # Tier 1: whole name present in the speech.
        best, best_adj = None, 0.0
        for s in self._ships:
            name = (s.get("name") or "").lower()
            if not name:
                continue
            score = fuzz.partial_ratio(name, tl)
            if score >= 88:
                adj = score + min(len(name), 30) * 0.4  # specificity bonus
                if adj > best_adj:
                    best, best_adj = s, adj
        if best:
            return best

        # Tier 2: distinctive name tokens (len>=3) covered by the spoken words.
        best, best_score = None, 0.0
        for s in self._ships:
            ntoks = [t for t in re.findall(r"[a-z0-9]+", (s.get("name") or "").lower())
                     if len(t) >= 3]
            if not ntoks:
                continue
            matched = sum(1 for t in ntoks if any(fuzz.ratio(t, w) >= 86 for w in words))
            coverage = matched / len(ntoks)
            if matched >= 1 and coverage >= 0.5:
                # prefer higher coverage; tie-break toward fewer-token (more base) names
                score = coverage * 100 - len(ntoks)
                if score > best_score:
                    best, best_score = s, score
        return best

    # -- answer -----------------------------------------------------------
    async def answer(self, text: str, llm) -> str | None:
        """Return a spoken answer for a ship-info question, or None on failure."""
        if not self.enabled:
            return None
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=12)
        except asyncio.TimeoutError:
            return "Ship database is still loading, try again in a moment."
        if not self._ships:
            return "Ship database is unavailable right now."
        ship = self.match(text)
        if not ship:
            return "I couldn't identify that ship."
        user = f"Question: {text}\nShip data: {json.dumps(ship, separators=(',', ':'))}"
        try:
            return (await llm.generate(_KNOWLEDGE_SYSTEM, user)).strip()
        except Exception:  # noqa: BLE001
            log.exception("knowledge: compose failed")
            return None
