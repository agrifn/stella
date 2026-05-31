"""Optional knowledge module: answer factual Star Citizen questions from real data.

Multi-domain and feature-flagged. Each domain (ships, locations, commodities, ...)
is fetched from the StarCitizenWiki API and cached as a trimmed index on startup.
All entities go into ONE searchable pool tagged with their kind.

A small local model can't reliably route among several knowledge sub-intents, so
the LLM only has to recognize a single 'info' intent (a factual SC question). The
handler then fuzzy-matches the entity out of the (messy STT) speech across every
domain and the LLM composes a short spoken answer grounded ONLY in that entity's
cached data.

"Where to buy / how much" (items, weapons, armor, ship components) is answered from
the UEX Corp API instead - the wiki and the game files no longer carry shop
inventories (removed in SC 3.20), so live community price data is the only source.
This sub-feature is gated on a UEX token (STELLA_UEX_TOKEN); without it the rest of
the knowledge feature works unchanged. UEX has no blueprint/crafting data, so
"where to find a blueprint" is intentionally not supported.

When disabled, none of this runs and the prompt has no 'info' intent - the command
path is completely unaffected.
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
    from rapidfuzz import fuzz, process, utils
except ImportError:  # matching disabled -> feature degrades gracefully
    fuzz = process = utils = None

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

_BUY_SYSTEM = (
    "You are STELLA, a Star Citizen ship AI. The pilot wants to know where to buy an item/weapon "
    "or what it costs. Using ONLY the JSON data (a list of shops, each with a location and a buy "
    "price in aUEC, cheapest first), tell the pilot the best 1-3 places to buy it: shop name, "
    "where it is, and the price. Be concise and spoken, 1-2 short sentences. Use grouped numbers "
    "like '4,700 aUEC'. Do not invent shops, locations, or prices."
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

# UEX item categories to cache for "where to buy" lookups. Curated to the things a
# pilot actually shops for (weapons, armour, ship systems, gear) and skips
# commodities (already a wiki domain), cosmetics, liveries, and vehicles.
_UEX_ITEM_CATEGORIES = [
    18, 17,                          # personal weapons, weapon attachments
    32, 33, 34, 35, 70, 79, 90,      # vehicle weapons: guns, missile racks, missiles, turrets, bombs, PDC, bomb racks
    1, 2, 3, 4, 5, 7, 24,            # armour: arms/backpacks/helmets/legs/torso/full-set + undersuits
    19, 21, 22, 23,                  # systems: coolers, power plants, quantum drives, shield generators
    82, 83, 86,                      # avionics: flight blade, radar; propulsion: jump modules
    28, 29, 30, 31, 67, 109, 110,    # utility: gadgets, mining heads/modules, scraper/tractor/salvage beams, fabricator
    25, 26,                          # docking collars, external fuel tanks
    16, 62, 63, 73,                  # consumables, drinks, foods, mobiglas
]

# Does the pilot want to BUY/price something (UEX) vs. ask for stats (wiki)?
_BUY_RE = re.compile(
    r"\b(buy|buying|purchase|purchased|sell|sells|selling|sold|price|priced|prices|"
    r"cost|costs|shop|shops|store|stores|vendor|afford|acquire|cheapest)\b")
_FIND_RE = re.compile(
    r"where\s+(can|do|to|could|should|would|might)\b.*?\b(buy|get|find|purchase|grab|pick)\b"
    r"|where\s+to\s+(buy|get|find)\b")
# "how much is/does X (cost)" is a price question - but "how much armor/shields/HP"
# is a stat question, so don't treat "how much <stat-word>" as a buy query.
_HOWMUCH_RE = re.compile(
    r"\bhow\s+much\b(?!\s+(armou?r|shield|shields|hp|health|cargo|speed|velocity|"
    r"damage|dps|fuel|crew|mass|cooling|power)\b)")

# Filler stripped from the speech before fuzzy-matching an item name.
_QUERY_STOP = {
    "where", "can", "i", "do", "to", "find", "buy", "get", "the", "a", "an", "is", "are",
    "much", "how", "cost", "costs", "price", "of", "does", "it", "sell", "sold", "purchase",
    "me", "you", "what", "which", "shop", "store", "for", "sale", "at", "in", "on", "stella",
    "cheapest", "best", "place", "places", "nearest", "closest", "any", "some", "and",
}


def _is_buy_query(text: str) -> bool:
    tl = text.lower()
    return bool(_BUY_RE.search(tl) or _FIND_RE.search(tl) or _HOWMUCH_RE.search(tl))


def _clean_query(text: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _QUERY_STOP)


def _place(p: dict) -> str:
    """A readable 'where' from a UEX price record: most-specific spot, planet, system."""
    spot = (p.get("space_station_name") or p.get("city_name")
            or p.get("outpost_name") or p.get("orbit_name"))
    parts = [x for x in (spot, p.get("planet_name"), p.get("star_system_name")) if x]
    return ", ".join(dict.fromkeys(parts))  # dedupe (e.g. orbit == planet)


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

    def __init__(self, cfg):
        # cfg is a KnowledgeConfig (duck-typed to avoid a config import here).
        self.enabled = bool(getattr(cfg, "enabled", False))
        self._uex_token = getattr(cfg, "uex_token", None)
        self._uex_base = (getattr(cfg, "uex_base", None)
                          or "https://api.uexcorp.space/2.0").rstrip("/")
        self._uex = bool(self.enabled and self._uex_token)
        self._all: list[dict] = []          # stats pool (ships/locations/commodities), each tagged _KIND
        self._items: list[dict] = []        # UEX items for buy lookups: {id, name, section, company}
        self._item_names: list[str] = []    # names aligned to _items (rapidfuzz choices)
        self._price_cache: dict[int, list] = {}
        self._ready = asyncio.Event()

    @property
    def intents(self) -> set[str]:
        return {self.INTENT} if self.enabled else set()

    @property
    def _uex_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._uex_token}", "Accept": "application/json"}

    # -- prompt contributions --------------------------------------------
    def intent_lines(self) -> list[str]:
        if not self.enabled:
            return []
        line = ("- info: the pilot asks a FACTUAL question about a Star Citizen SHIP/vehicle, "
                "LOCATION (planet, moon, space station, star system), or COMMODITY/trade good - "
                "its stats, where it is, or what it is. ANY 'what is X / where is X / what type "
                "is X / how fast/big/strong is X / tell me about X' where X is a name is info, "
                "even if you don't recognise the name. A ship/place/commodity NAME with a stat "
                "word (armor, shields, speed, cargo) is info, NOT a power command.")
        if self._uex:
            line += (" ALSO info: WHERE TO BUY or the PRICE/COST of an item, weapon, armor, or "
                     "ship component - 'where can I buy X', 'how much is X', 'where do I find X', "
                     "'cheapest X'.")
        return [line]

    def examples(self) -> list[str]:
        if not self.enabled:
            return []

        def ex(text):
            return (f'Input: "{text}"\n'
                    f'Output: {{"intent":"info","confirm_required":false,"response_text":""}}')
        out = [ex("what is the guardian MX armor"), ex("cutlass black shields"),
               ex("where is crusader"), ex("what type is hurston"),
               ex("is laranite mineable")]
        if self._uex:
            out += [ex("where can I buy a P4-AR"), ex("how much is a demeco")]
        return out

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
        if self._uex:
            try:
                await self._load_uex_items()
            except Exception:  # noqa: BLE001 - UEX is an optional add-on
                log.exception("knowledge: failed to load UEX items")
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

    async def _load_uex_items(self) -> None:
        """Cache a name->id index of buyable items across the curated UEX categories."""
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True,
                                     headers=self._uex_headers) as client:
            async def one(cid: int) -> list[dict]:
                try:
                    r = await client.get(f"{self._uex_base}/items", params={"id_category": cid})
                    r.raise_for_status()
                    return r.json().get("data", []) or []
                except Exception:  # noqa: BLE001
                    log.exception("knowledge: UEX category %s failed", cid)
                    return []
            results = await asyncio.gather(*(one(c) for c in _UEX_ITEM_CATEGORIES))
        for lst in results:
            for it in lst:
                name = it.get("name")
                if name and it.get("id"):
                    self._items.append({"id": it["id"], "name": name,
                                        "section": it.get("section"),
                                        "company": it.get("company_name")})
        self._item_names = [i["name"] for i in self._items]
        log.info("knowledge: cached %d UEX items (buy lookup)", len(self._items))

    async def _fetch_prices(self, item_id: int) -> list[dict]:
        if item_id in self._price_cache:
            return self._price_cache[item_id]
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True,
                                     headers=self._uex_headers) as client:
            r = await client.get(f"{self._uex_base}/items_prices", params={"id_item": item_id})
            r.raise_for_status()
            data = r.json().get("data", []) or []
        self._price_cache[item_id] = data
        return data

    # -- matching ---------------------------------------------------------
    def _match_item(self, text: str) -> dict | None:
        """Fuzzy-match a UEX item from speech. Item names carry model codes and
        variants ('P4-AR "Warhawk" Rifle'), so use token-set matching over the
        de-filtered query and prefer the shortest (base) name on near-ties."""
        if not self._items or process is None:
            return None
        q = _clean_query(text)
        if not q:
            return None
        res = process.extract(q, self._item_names, scorer=fuzz.token_set_ratio,
                              processor=utils.default_process, limit=10)
        res = [r for r in res if r[1] >= 72]
        if not res:
            return None
        top = max(r[1] for r in res)
        best = min((r for r in res if r[1] >= top - 3), key=lambda r: len(r[0]))
        return self._items[best[2]]

    # -- answer -----------------------------------------------------------
    async def answer(self, intent: str, text: str, llm) -> str | None:
        if not self.enabled or intent != self.INTENT:
            return None
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=12)
        except asyncio.TimeoutError:
            return "Database is still loading, try again in a moment."

        # Buy/price questions go to UEX; a miss falls through to the stats pool
        # (covers e.g. "where can I find Crusader", which isn't an item).
        if self._uex and _is_buy_query(text):
            item = self._match_item(text)
            if item:
                return await self._answer_buy(item, text, llm)

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

    async def _answer_buy(self, item: dict, text: str, llm) -> str | None:
        try:
            prices = await self._fetch_prices(item["id"])
        except Exception:  # noqa: BLE001 - network / API hiccup
            log.exception("knowledge: UEX prices fetch failed")
            return f"I couldn't reach the trade database for the {item['name']} right now."
        buy = [p for p in prices if (p.get("price_buy") or 0) > 0]
        if not buy:
            return f"I don't have a known buy location for the {item['name']}."
        # Keep the cheapest record per terminal, then the cheapest few overall.
        per_terminal: dict = {}
        for p in buy:
            t = p.get("id_terminal")
            if t not in per_terminal or p["price_buy"] < per_terminal[t]["price_buy"]:
                per_terminal[t] = p
        locs = sorted(per_terminal.values(), key=lambda p: p["price_buy"])[:6]
        data = {
            "item": item["name"],
            "locations": [{
                "shop": p.get("terminal_name"),
                "place": _place(p),
                "price_auec": p.get("price_buy"),
            } for p in locs],
        }
        user = f"Question: {text}\nData: {json.dumps(data, separators=(',', ':'))}"
        try:
            return (await llm.generate(_BUY_SYSTEM, user)).strip()
        except Exception:  # noqa: BLE001
            log.exception("knowledge: buy compose failed")
            return None
