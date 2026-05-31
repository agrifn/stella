"""Optional knowledge module: answer factual Star Citizen questions from real data.

Multi-domain and feature-flagged. On startup it caches several trimmed indexes and
keeps the LLM's job to a single 'info' intent (a small local model can't reliably
route among many knowledge sub-intents). The handler then figures out, from the
speech, which kind of question it is and answers from the right source:

  - stats        ships / locations / commodities (StarCitizenWiki API v2) AND
                 weapons / armor / ship components (StarCitizenWiki API, /api)
  - where to buy items / weapons        UEX Corp API (token-gated, live prices)
  - crafting / blueprints               StarCitizenWiki API /api/blueprints

For each, the LLM composes a short spoken answer grounded ONLY in the matched
record's cached JSON. Routing inside the single intent is by trigger words
(buy/price -> UEX, craft/blueprint -> blueprints) with a fall-through to stats.

Notes / limits:
  - Shop inventories left the game files in SC 3.20, so "where to buy" needs UEX
    (no token => that sub-path is silently off). UEX has no blueprint data.
  - Item stats come from the newer /api surface (the /api/v2 one lacks them).
When disabled, none of this runs and the prompt has no 'info' intent.
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

_API = "https://api.star-citizen.wiki/api/v2"   # ships/locations/commodities
_WIKI = "https://api.star-citizen.wiki/api"     # newer surface: items (stats) + blueprints
_KIND = "_kind"  # internal tag on each cached entity (its domain label)

# Item categories cached for stat lookups (the /api/items list scopes by type;
# multiple types can be comma-joined in one filter). Weapons + armor + the main
# ship components - the gameplay gear a pilot asks stats about.
_STAT_TYPES = ",".join([
    "WeaponPersonal", "WeaponGun", "WeaponAttachment", "WeaponDefensive", "WeaponMining",
    "Char_Armor_Helmet", "Char_Armor_Torso", "Char_Armor_Arms", "Char_Armor_Legs",
    "Char_Armor_Backpack", "Char_Armor_Undersuit",
    "Cooler", "PowerPlant", "QuantumDrive", "Shield", "Radar", "JumpDrive",
    "MainThruster", "ManneuverThruster", "TractorBeam",
])

_KNOWLEDGE_SYSTEM = (
    "You are STELLA, a Star Citizen ship AI assisting a pilot. Answer the pilot's question "
    "using ONLY the JSON {label} data provided - do not invent numbers or facts. Be concise and "
    "spoken, 1-2 short sentences. Use plain numbers (e.g. '2500 HP', '25 percent'). For ships, "
    "armor damage_multiplier values are damage taken: 0.75 means 25 percent resistance. For "
    "weapons, damage_per_shot is per-shot damage and rof/rpm is the rate of fire; resistance maps "
    "are damage taken by type. If the data does not contain the answer, say so briefly."
)

_BUY_SYSTEM = (
    "You are STELLA, a Star Citizen ship AI. The pilot wants to know where to buy an item/weapon "
    "or what it costs. Using ONLY the JSON data (a list of shops, each with a location and a buy "
    "price in aUEC, cheapest first), tell the pilot the best 1-3 places to buy it: shop name, "
    "where it is, and the price. Be concise and spoken, 1-2 short sentences. Use grouped numbers "
    "like '4,700 aUEC'. Do not invent shops, locations, or prices."
)

_BLUEPRINT_SYSTEM = (
    "You are STELLA, a Star Citizen ship AI. The pilot is asking about CRAFTING an item. Using "
    "ONLY the JSON blueprint data (what it makes, craft time, ingredients with quantities, and "
    "whether it's unlocked by default or via missions), answer concisely and spoken, 1-2 short "
    "sentences: name the key ingredients and the craft time. If available_by_default is false and "
    "unlocking_missions is above zero, note the recipe must be unlocked via a mission. Do not "
    "invent ingredients or numbers."
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


# Fields on a wiki item record that are noise for a spoken stat answer.
_ITEM_NOISE = {
    "description", "description_data", "manufacturer_description", "images", "blueprint",
    "variants", "shops", "uex_prices", "interactions", "ports", "entity_tag_map", "entity_tags",
    "tags", "required_tags", "dimension", "web_url", "link", "type_web_url", "slug", "uuid",
    "class_name", "classification", "updated_at", "version", "clothing", "type", "sub_type",
}


def _stat_block(d: dict) -> dict:
    """Keep the scalar stat fields of a nested dict (damage, rof, range, ...) plus
    any 'resistance' map; drop verbose sub-arrays like modes/damages."""
    out: dict = {}
    for k, v in d.items():
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)) and v not in (0,):
            out[k] = v
        elif isinstance(v, str) and v:
            out[k] = v
        elif k == "resistance" and isinstance(v, dict):
            r = {kk: vv for kk, vv in v.items() if isinstance(vv, (int, float))}
            if r:
                out[k] = r
    return out


def _trim_item(it: dict) -> dict:
    """Generic trim for a wiki item (weapon/armor/component): scalar attributes +
    its nested stat blocks. Works across types without per-type code."""
    out: dict = {}
    for k, v in it.items():
        if k in _ITEM_NOISE:
            continue
        if k == "manufacturer":
            out["manufacturer"] = v.get("name") if isinstance(v, dict) else v
        elif isinstance(v, (int, float, str, bool)) and v not in ("", None):
            out[k] = v
        elif isinstance(v, dict):
            block = _stat_block(v)
            if block:
                out[k] = block
    return out


def _trim_blueprint(bp: dict) -> dict:
    ings = []
    for i in (bp.get("ingredients") or []):
        if i.get("quantity") is not None:
            qty = i["quantity"]
        elif i.get("quantity_scu") is not None:
            qty = f"{i['quantity_scu']} SCU"
        else:
            qty = None
        ings.append({"name": i.get("name"), "qty": qty, "kind": i.get("kind")})
    return {
        "name": bp.get("output_name"), "makes_class": bp.get("output_class"),
        "craft_time": bp.get("craft_time_label"),
        "available_by_default": bp.get("is_available_by_default"),
        "unlocking_missions": bp.get("unlocking_missions_count"),
        "ingredients": ings,
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
# Crafting / blueprint questions -> the blueprints index.
_CRAFT_RE = re.compile(
    r"\b(craft|crafted|crafting|craftable|blueprint|blueprints|recipe|recipes|ingredient|"
    r"ingredients|fabricate|how\s+(do\s+i\s+|to\s+)?make|how\s+(do\s+i\s+|to\s+)?build)\b")

# Filler stripped from the speech before fuzzy-matching a name (buy/craft/stat words too).
_QUERY_STOP = {
    "where", "can", "i", "do", "to", "find", "buy", "get", "the", "a", "an", "is", "are",
    "much", "how", "cost", "costs", "price", "of", "does", "it", "sell", "sold", "purchase",
    "me", "you", "what", "whats", "hows", "wheres", "which", "shop", "store", "for", "sale",
    "at", "in", "on", "stella",
    "cheapest", "best", "place", "places", "nearest", "closest", "any", "some", "and",
    # stat words
    "fire", "rate", "damage", "dps", "range", "magazine", "capacity", "speed", "armor", "armour",
    "protect", "protection", "resistance", "stats", "stat", "mass", "size", "fast", "strong",
    "shields", "shield", "hp", "health", "much",
    # craft words
    "craft", "crafting", "blueprint", "recipe", "ingredients", "ingredient", "make", "build",
    "need", "unlock", "fabricate", "tell", "about", "rating",
}


# Words too common to be a distinctive entity-name token in the stats matcher.
_COMMON = {"the", "of", "and", "a", "an", "to", "in", "on", "for", "mk", "type"}


def _is_buy_query(text: str) -> bool:
    tl = text.lower()
    return bool(_BUY_RE.search(tl) or _FIND_RE.search(tl) or _HOWMUCH_RE.search(tl))


def _is_craft_query(text: str) -> bool:
    return bool(_CRAFT_RE.search(text.lower()))


def _clean_query(text: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _QUERY_STOP)


def _place(p: dict) -> str:
    """A readable 'where' from a UEX price record: most-specific spot, planet, system."""
    spot = (p.get("space_station_name") or p.get("city_name")
            or p.get("outpost_name") or p.get("orbit_name"))
    parts = [x for x in (spot, p.get("planet_name"), p.get("star_system_name")) if x]
    return ", ".join(dict.fromkeys(parts))  # dedupe (e.g. orbit == planet)


def _fuzzy_pick(query: str, names: list[str], items: list[dict], cutoff: int = 72):
    """Pick the best item whose name matches the (de-filtered) query.

    Two stages: token_set_ratio for RECALL (find candidates that contain the query
    tokens, tolerant of word order/extra words), then re-rank by token_sort_ratio
    for PRECISION (it penalises a candidate's extra tokens, so 'Crusader' beats
    'ADP Arms Crusader Edition' and 'Guardian MX' beats 'Guardian'); shortest name
    breaks ties (prefers the base variant, e.g. 'P4-AR Rifle')."""
    if not items or process is None or not query:
        return None
    cands = process.extract(query, names, scorer=fuzz.token_set_ratio,
                            processor=utils.default_process, limit=15)
    cands = [c for c in cands if c[1] >= cutoff]
    if not cands:
        return None
    qn = utils.default_process(query)
    best = max(cands, key=lambda c: (fuzz.token_sort_ratio(qn, utils.default_process(c[0])),
                                     -len(c[0])))
    return items[best[2]]


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
        ntoks = [t for t in re.findall(r"[a-z0-9]+", (it.get("name") or "").lower())
                 if len(t) >= 3 and t not in _COMMON]
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
        self._all: list[dict] = []          # v2 stats (ships/locations/commodities), each tagged _KIND
        self._equipment: list[dict] = []     # weapons/armor/components (stats), tagged _KIND
        self._stats_items: list[dict] = []   # unified search pool = _all + _equipment
        self._stats_names: list[str] = []    # names aligned to _stats_items
        self._blueprints: list[dict] = []    # crafting recipes
        self._bp_names: list[str] = []
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
                "LOCATION (planet, moon, space station, star system), COMMODITY/trade good, or a "
                "piece of EQUIPMENT (weapon, armor, ship component) - its stats, where it is, or "
                "what it is. ANY 'what is X / where is X / what type is X / how fast/big/strong is "
                "X / tell me about X' where X is a name is info, even if you don't recognise the "
                "name. A ship/place/item NAME with a stat word (armor, shields, speed, cargo, "
                "damage, fire rate) is info, NOT a power command.")
        line += (" ALSO info: CRAFTING questions - what's needed to craft/make an item, its "
                 "ingredients, craft time, or how a blueprint is unlocked.")
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
               ex("whats the A03 sniper fire rate"),
               ex("what do I need to craft an omnisky three")]
        if self._uex:
            out += [ex("where can I buy a P4-AR"), ex("how much is a demeco")]
        return out

    # -- index loading ----------------------------------------------------
    async def load(self) -> None:
        if not self.enabled:
            self._ready.set()
            return
        # Core: stats pool (v2 ships/locations/commodities) + UEX buy index. These
        # gate _ready so the common paths are available quickly after startup.
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
        self._rebuild_stats_index()
        if self._uex:
            try:
                await self._load_uex_items()
            except Exception:  # noqa: BLE001 - UEX is an optional add-on
                log.exception("knowledge: failed to load UEX items")
        self._ready.set()

        # Heavier add-ons (equipment stats ~34 pages, blueprints ~16) load in the
        # background; until each finishes, those queries just miss gracefully.
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            try:
                eq = await self._fetch_wiki(client, "items", {"filter[type]": _STAT_TYPES},
                                            _trim_item, tag="equipment")
                self._equipment = eq
                self._rebuild_stats_index()
                log.info("knowledge: cached %d equipment items (stats)", len(eq))
            except Exception:  # noqa: BLE001
                log.exception("knowledge: failed to load equipment stats")
            try:
                bp = await self._fetch_wiki(client, "blueprints", {}, _trim_blueprint)
                self._blueprints = bp
                self._bp_names = [b["name"] for b in bp]
                log.info("knowledge: cached %d blueprints", len(bp))
            except Exception:  # noqa: BLE001
                log.exception("knowledge: failed to load blueprints")

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

    async def _fetch_wiki(self, client: httpx.AsyncClient, path: str, params: dict,
                          trim: Callable[[dict], dict], tag: str | None = None) -> list[dict]:
        """Paginate the newer /api surface and trim each record."""
        out: list[dict] = []
        page = 1
        while True:
            p = dict(params, **{"page[size]": 100, "page[number]": page})
            j = None
            for attempt in range(3):  # large item pages occasionally drop mid-stream
                try:
                    r = await client.get(f"{_WIKI}/{path}", params=p)
                    r.raise_for_status()
                    j = r.json()
                    break
                except (httpx.TransportError, httpx.HTTPStatusError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5 * (attempt + 1))
            for v in j.get("data", []) or []:
                t = trim(v)
                if t and t.get("name"):
                    if tag:
                        t[_KIND] = tag
                    out.append(t)
            last = int((j.get("meta") or {}).get("last_page", page))
            if page >= last or page >= 200:  # 200-page hard stop (safety)
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

    def _rebuild_stats_index(self) -> None:
        """Unify the v2 pool and equipment into one searchable stats index."""
        self._stats_items = self._all + self._equipment
        self._stats_names = [it.get("name") or "" for it in self._stats_items]

    # -- matching ---------------------------------------------------------
    def _match_item(self, text: str) -> dict | None:
        """Fuzzy-match a UEX item (for buy lookups) from speech."""
        return _fuzzy_pick(_clean_query(text), self._item_names, self._items)

    # -- answer -----------------------------------------------------------
    async def answer(self, intent: str, text: str, llm) -> str | None:
        if not self.enabled or intent != self.INTENT:
            return None
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=12)
        except asyncio.TimeoutError:
            return "Database is still loading, try again in a moment."

        # Crafting question -> blueprints (miss falls through to stats).
        if _is_craft_query(text) and self._blueprints:
            bp = _fuzzy_pick(_clean_query(text), self._bp_names, self._blueprints)
            if bp:
                return await self._compose(_BLUEPRINT_SYSTEM, "blueprint", bp, text, llm)

        # Buy/price question -> UEX (miss falls through to stats, e.g. "where can I
        # find Crusader", which is a location not an item).
        if self._uex and _is_buy_query(text):
            item = self._match_item(text)
            if item:
                return await self._answer_buy(item, text, llm)

        # Stats: one unified fuzzy match over ships/locations/commodities + equipment
        # (the two-stage scorer picks the best entity across the namespaces). Fall
        # back to the two-tier substring matcher over the v2 pool on a miss.
        entity = _fuzzy_pick(_clean_query(text), self._stats_names, self._stats_items)
        if not entity:
            entity = _match(self._all, text)
        if not entity:
            if not self._stats_items:
                return "The knowledge database is unavailable right now."
            return "I couldn't identify what you're asking about."
        label = entity.get(_KIND) or entity.get("type_label") or "item"
        return await self._compose(_KNOWLEDGE_SYSTEM.format(label=label), label, entity, text, llm)

    async def _compose(self, system: str, label: str, entity: dict, text: str, llm) -> str | None:
        clean = {k: v for k, v in entity.items() if k != _KIND}
        user = f"Question: {text}\nData: {json.dumps(clean, separators=(',', ':'))}"
        try:
            return (await llm.generate(system, user)).strip()
        except Exception:  # noqa: BLE001
            log.exception("knowledge: compose failed (%s)", label)
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
