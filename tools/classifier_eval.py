"""Offline eval of a potion-32M (model2vec) nearest-example intent classifier against
the live command registry. Decides whether the static embedding classifier is accurate
enough to replace the LLM, and exposes the power-family confusion. Run from repo root:

    client/.venv/Scripts/python.exe tools/classifier_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from model2vec import StaticModel

KB = Path("config/keybinds.json")


def main() -> int:
    d = json.loads(KB.read_text(encoding="utf-8"))
    kb = d["keybinds"]
    phrases, labels = [], []
    for intent, spec in kb.items():
        for ex in (spec.get("examples") or []):
            phrases.append(ex)
            labels.append(intent)
    print(f"{len(kb)} intents, {len(phrases)} example phrases")

    model = StaticModel.from_pretrained("minishlab/potion-base-32M")
    emb = np.asarray(model.encode(phrases), dtype=np.float32)
    emb /= (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
    labels = np.array(labels)

    def classify(text: str, exclude: int | None = None):
        v = np.asarray(model.encode([text])[0], dtype=np.float32)
        v /= (np.linalg.norm(v) + 1e-9)
        sims = emb @ v
        if exclude is not None:
            sims[exclude] = -1.0
        order = np.argsort(sims)[::-1]
        top, second = order[0], order[1]
        return labels[top], float(sims[top]), labels[second], float(sims[second])

    # leave-one-out over the example phrases (generalization to a held-out phrasing)
    correct = 0
    mistakes = []
    margins = []
    for i, (p, lab) in enumerate(zip(phrases, labels)):
        pred, s1, pred2, s2 = classify(p, exclude=i)
        margins.append(s1 - s2)
        if pred == lab:
            correct += 1
        else:
            mistakes.append((p, lab, pred, round(s1, 3)))
    print(f"\nLEAVE-ONE-OUT: {correct}/{len(phrases)} = {correct/len(phrases):.1%}")
    print(f"mean top-2 margin: {np.mean(margins):.3f}")
    print(f"mistakes ({len(mistakes)}):")
    for m in mistakes:
        print(f"   {m[0]!r:40s} true={m[1]:22s} pred={m[2]:22s} sim={m[3]}")

    # fresh probes (phrasings NOT necessarily in examples), power family stressed
    probes = [
        ("max guns", "weapons_power_max"), ("weapons hot", "weapons_power_max"),
        ("more guns", "weapons_power_inc"), ("less guns", "weapons_power_dec"),
        ("cut guns", "lower_weapons_min"),
        ("punch it", "engines_power_max"), ("more engines", "engines_power_inc"),
        ("raise engine power", "engines_power_inc"), ("less engines", "engines_power_dec"),
        ("cut engines", "lower_engine_min"),
        ("brace", "shields_power_max"), ("more shields", "shields_power_inc"),
        ("less shields", "shields_power_dec"), ("drop shields", "lower_shields_min"),
        ("shields off", "shields_power_toggle"), ("shields to max", "shields_power_max"),
        ("balance power", "reset_power"), ("eject", "eject"),
        ("landing gear", "landing_gear"), ("fire flares", "decoy_burst"),
        ("scan mode", "scan_mode"), ("look behind", "look_behind"),
    ]
    ok = 0
    print("\nPROBES (fresh phrasings):")
    for t, want in probes:
        pred, s1, pred2, s2 = classify(t)
        mark = "OK " if pred == want else "XX "
        ok += pred == want
        extra = "" if pred == want else f"  (2nd: {pred2} {s2:.2f})"
        print(f"  {mark}{t!r:24s} -> {pred:22s} sim={s1:.2f}{extra}")
    print(f"PROBE SCORE: {ok}/{len(probes)} = {ok/len(probes):.1%}")

    # non-commands: should land LOW so a reject threshold sends them to chat
    print("\nNON-COMMANDS (want LOW sim -> reject):")
    for t in ["how is the weather today", "i think we should land soon",
              "hello there how are you", "what time is it"]:
        pred, s1, _, _ = classify(t)
        print(f"  {t!r:34s} -> {pred:22s} sim={s1:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
