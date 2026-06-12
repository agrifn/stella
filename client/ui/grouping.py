"""Pure helper: assign each command to a display group for the manager GUI.

keybinds.json has no category field (and the server's CommandModel would drop
one), so grouping is derived client-side from the intent name and description.
The groups and their order come from the approved design: Power, Flight,
Combat & Utility, Apps, Other, with Dangerous (confirm-required) always last so
the scary stuff is visually separated. Kept free of Qt imports so the unit
tests can cover it.
"""
from __future__ import annotations

GROUP_ORDER = ["Power", "Flight", "Combat & Utility", "Apps", "Other", "Dangerous"]

_POWER = ("power", "shield", "weapon", "engine", "thruster")
_FLIGHT = ("gear", "landing", "vtol", "cruise", "decouple", "couple", "nav",
           "quantum", "ready", "preflight", "seat", "exit", "brake", "speed",
           "autoland", "atc", "doors", "lock")
_COMBAT = ("flare", "noise", "countermeasure", "gimbal", "target", "scan",
           "ping", "light", "missile", "fire", "mining", "salvage", "tractor")
_APPS = ("mobiglas", "map", "starmap", "comms", "chat", "contract", "wallet",
         "journal", "app")


def categorize(intent: str, description: str = "", confirm_required: bool = False) -> str:
    """One of GROUP_ORDER. confirm_required always wins (Dangerous), then the
    first keyword family that matches the intent or description."""
    if confirm_required:
        return "Dangerous"
    hay = f"{intent} {description}".lower()
    for group, words in (("Power", _POWER), ("Flight", _FLIGHT),
                         ("Combat & Utility", _COMBAT), ("Apps", _APPS)):
        if any(w in hay for w in words):
            return group
    return "Other"


def group_commands(commands: list[dict]) -> list[tuple[str, list[dict]]]:
    """[(group_label, [command, ...]), ...] in GROUP_ORDER, skipping empty groups.
    Commands keep their server order inside each group."""
    buckets: dict[str, list[dict]] = {g: [] for g in GROUP_ORDER}
    for c in commands:
        buckets[categorize(c.get("intent", ""), c.get("description", ""),
                           bool(c.get("confirm_required")))].append(c)
    return [(g, buckets[g]) for g in GROUP_ORDER if buckets[g]]
