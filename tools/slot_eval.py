"""Validate the proposed design: power commands handled by a deterministic slot rule,
everything else by the potion-32M embedding classifier. Run from repo root."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from model2vec import StaticModel

KB = json.loads(Path("config/keybinds.json").read_text(encoding="utf-8"))["keybinds"]

# Map the 12 power intents to (pool, direction) so we can score the rule.
POWER_INTENTS = {
    "weapons_power_max": ("weapons", "max"), "weapons_power_inc": ("weapons", "inc"),
    "weapons_power_dec": ("weapons", "dec"), "lower_weapons_min": ("weapons", "min"),
    "weapons_power_toggle": ("weapons", "toggle"),
    "engines_power_max": ("engines", "max"), "engines_power_inc": ("engines", "inc"),
    "engines_power_dec": ("engines", "dec"), "lower_engine_min": ("engines", "min"),
    "thrusters_power_toggle": ("engines", "toggle"),
    "shields_power_max": ("shields", "max"), "shields_power_inc": ("shields", "inc"),
    "shields_power_dec": ("shields", "dec"), "lower_shields_min": ("shields", "min"),
    "shields_power_toggle": ("shields", "toggle"),
}

_POOL = [(r"\b(weapon|weapons|gun|guns)\b", "weapons"),
         (r"\b(engine|engines|thruster|thrusters)\b", "engines"),
         (r"\b(shield|shields)\b", "shields")]
# order matters: check min/max before inc/dec, toggle last
_DIR = [
    (r"\b(max|maximum|full|hot|brace|punch it|all power|to max)\b", "max"),
    (r"\b(min|minimum|cut|kill|zero|cold|drop|take everything|to zero)\b", "min"),
    (r"\b(more|raise|add|increase|boost|up)\b", "inc"),
    (r"\b(less|fewer|reduce|ease off|back off|decrease|lower)\b", "dec"),
    (r"\b(on|off|toggle|power)\b", "toggle"),
]


def power_slot(text: str):
    t = text.lower()
    pool = next((v for rx, v in _POOL if re.search(rx, t)), None)
    if not pool:
        return None
    direction = next((v for rx, v in _DIR if re.search(rx, t)), None)
    if not direction:
        return None
    return (pool, direction)


def main() -> int:
    # 1) power slot-rule coverage on the real power example phrases
    pc = pw = 0
    pmiss = []
    for intent, want in POWER_INTENTS.items():
        for ph in (KB.get(intent, {}).get("examples") or []):
            pw += 1
            got = power_slot(ph)
            if got == want:
                pc += 1
            else:
                pmiss.append((ph, want, got))
    print(f"POWER SLOT-RULE: {pc}/{pw} = {pc/pw:.1%} on power example phrases")
    for m in pmiss:
        print(f"   miss {m[0]!r:34s} want={m[1]} got={m[2]}")

    # 2) embedding classifier leave-one-out on NON-power intents only
    phrases, labels = [], []
    for intent, spec in KB.items():
        if intent in POWER_INTENTS:
            continue
        for ph in (spec.get("examples") or []):
            phrases.append(ph)
            labels.append(intent)
    model = StaticModel.from_pretrained("minishlab/potion-base-32M")
    emb = np.asarray(model.encode(phrases), dtype=np.float32)
    emb /= (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
    labels = np.array(labels)
    correct = 0
    miss = []
    for i, (p, lab) in enumerate(zip(phrases, labels)):
        sims = emb @ emb[i]
        sims[i] = -1
        j = int(np.argmax(sims))
        if labels[j] == lab:
            correct += 1
        else:
            miss.append((p, lab, labels[j]))
    print(f"\nNON-POWER classifier LEAVE-ONE-OUT: {correct}/{len(phrases)} = {correct/len(phrases):.1%}")
    print(f"({len(KB)-len(POWER_INTENTS)} non-power intents, {len(phrases)} phrases)")
    for m in miss:
        print(f"   {m[0]!r:30s} true={m[1]:20s} pred={m[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
