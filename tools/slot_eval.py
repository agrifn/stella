"""Validate the design: power commands handled by a deterministic slot rule,
everything else by the potion-32M embedding classifier. Run from repo root.

This imports the PRODUCTION rule (server.intent_classifier._power_rule) so the eval
can never drift from what actually runs. It also checks a set of collision cases
(phrasings that previously mis-resolved) so a regression shows up here."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from model2vec import StaticModel

from server.intent_classifier import _power_rule

KB = json.loads(Path("config/keybinds.json").read_text(encoding="utf-8"))["keybinds"]

POWER_INTENTS = {
    "weapons_power_max", "weapons_power_inc", "weapons_power_dec", "lower_weapons_min",
    "weapons_power_toggle", "engines_power_max", "engines_power_inc", "engines_power_dec",
    "lower_engine_min", "thrusters_power_toggle", "shields_power_max", "shields_power_inc",
    "shields_power_dec", "lower_shields_min", "shields_power_toggle",
}

# Phrasings that previously mis-resolved (greedy toggle, missing lower/down). Each
# maps to the intent the production rule MUST return now. None = must fall through to
# the embedder (no deterministic hit).
COLLISIONS = {
    "lower shields": "shields_power_dec",
    "shields down": "shields_power_dec",
    "power down weapons": "weapons_power_dec",
    "turn off shields": "shields_power_toggle",
    "more power to shields": "shields_power_inc",
    "all power to shields": "shields_power_max",
    "weapons power": None,         # bare noun, no direction -> embedder
    "eject": None,                 # dangerous: never a power-rule hit
    "self destruct": None,
}


def main() -> int:
    # 1) power slot-rule coverage on the real power example phrases
    pc = pw = 0
    pmiss = []
    for intent in POWER_INTENTS:
        for ph in (KB.get(intent, {}).get("examples") or []):
            pw += 1
            got = _power_rule(ph.lower())
            if got == intent:
                pc += 1
            else:
                pmiss.append((ph, intent, got))
    print(f"POWER SLOT-RULE: {pc}/{pw} = {pc/pw:.1%} on power example phrases")
    for m in pmiss:
        print(f"   miss {m[0]!r:34s} want={m[1]} got={m[2]}")

    # 1b) collision cases (regressions the review found)
    cc = cw = 0
    for phrase, want in COLLISIONS.items():
        cw += 1
        got = _power_rule(phrase.lower())
        ok = got == want
        cc += ok
        print(f"   {'ok  ' if ok else 'FAIL'} {phrase!r:24s} want={want} got={got}")
    print(f"COLLISION CASES: {cc}/{cw}")

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
