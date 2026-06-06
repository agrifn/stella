"""Static-embedding intent classifier - the LLM-free replacement for intent parsing.

Three layers, cheapest first:
  1. Power slot-rule: a closed-vocab regex maps pool (weapons/engines/shields) x
     direction (max/min/inc/dec/toggle) to the matching power intent. Deterministic,
     removes the near-synonym confusion the embedding model has on the 12 power intents.
  2. Disambiguation keywords: a few hand rules for pairs the embedder confuses
     (reset-power vs all-power, mfd forward vs back).
  3. Embedding nearest-example: potion-32M (model2vec) over the registry's example
     phrases, cosine match. Below `reject_threshold` -> 'chat' (not a command).

CPU, numpy-only, microseconds per call. No torch, no Ollama. Build the index from the
command registry; rebuild on registry change.
"""
from __future__ import annotations

import re

import numpy as np

CHAT = "chat"

# (pool, direction) -> intent name in the registry. Direction order matters: the rule
# checks min/max before inc/dec so "to max"/"cut" win over a stray "more"/"less".
_POWER_MAP = {
    ("weapons", "max"): "weapons_power_max", ("weapons", "inc"): "weapons_power_inc",
    ("weapons", "dec"): "weapons_power_dec", ("weapons", "min"): "lower_weapons_min",
    ("weapons", "toggle"): "weapons_power_toggle",
    ("engines", "max"): "engines_power_max", ("engines", "inc"): "engines_power_inc",
    ("engines", "dec"): "engines_power_dec", ("engines", "min"): "lower_engine_min",
    ("engines", "toggle"): "thrusters_power_toggle",
    ("shields", "max"): "shields_power_max", ("shields", "inc"): "shields_power_inc",
    ("shields", "dec"): "shields_power_dec", ("shields", "min"): "lower_shields_min",
    ("shields", "toggle"): "shields_power_toggle",
}
_POOL = [(re.compile(r"\b(weapon|weapons|gun|guns)\b"), "weapons"),
         (re.compile(r"\b(engine|engines|thruster|thrusters)\b"), "engines"),
         (re.compile(r"\b(shield|shields)\b"), "shields")]
# Order matters: hard-stop words (max/min) are checked before the gradual ones
# (inc/dec), and toggle last. Word choice encodes magnitude on purpose:
#   - "drop/cut/kill/zero" = slam to MIN; "lower/down" = ease one pip = DEC. An
#     ambiguous reduce-word resolves to the smaller, reversible action (a wrong rule
#     hit has no recovery layer behind it - score is 1.0 and never rescored).
#   - "power" is a NOUN here ("more power to shields", "weapons power"), never a
#     direction, so it is NOT a toggle trigger; toggle needs an explicit token.
_DIR = [
    (re.compile(r"\b(max|maximum|full|hot|to max|all power)\b"), "max"),
    (re.compile(r"\b(min|minimum|cut|kill|zero|cold|drop|to zero|take everything)\b"), "min"),
    (re.compile(r"\b(more|raise|add|increase|boost)\b"), "inc"),
    (re.compile(r"\b(less|fewer|reduce|ease off|back off|decrease|lower|down)\b"), "dec"),
    (re.compile(r"\b(toggle|arm|on|off)\b"), "toggle"),
]
# Pool-less power phrasings.
_POWER_ALIAS = {"brace": ("shields", "max"), "punch it": ("engines", "max")}


def _power_rule(t: str):
    for phrase, slot in _POWER_ALIAS.items():
        if phrase in t:
            return _POWER_MAP.get(slot)
    pool = next((v for rx, v in _POOL if rx.search(t)), None)
    if not pool:
        return None
    direction = next((v for rx, v in _DIR if rx.search(t)), None)
    if not direction:
        return None
    return _POWER_MAP.get((pool, direction))


def select_best(results: list[tuple[str, float, str]], chat_label: str = CHAT):
    """Pick the winner from classified candidates for n-best rescoring.

    `results` is a list of (intent, score, text). Returns the highest-scoring COMMAND
    (intent != chat); if no candidate is a command, returns the highest-scoring chat
    result. Pure (no model), so it is unit-testable. Ties keep the first seen, so the
    original transcript (passed first) wins over a later alternate of equal score.
    """
    cmd_best = None
    chat_best = None
    for intent, score, text in results:
        if intent != chat_label:
            if cmd_best is None or score > cmd_best[1]:
                cmd_best = (intent, score, text)
        elif chat_best is None or score > chat_best[1]:
            chat_best = (chat_label, score, text)
    if cmd_best is not None:
        return cmd_best
    if chat_best is not None:
        return chat_best
    return (chat_label, 0.0, "")


def _disambig(t: str):
    if re.search(r"\b(reset|balance|equalize|equalise|default|even)\b", t) and "power" in t:
        return "reset_power"
    if re.search(r"\b(next|forward)\b", t) and re.search(r"\b(mfd|display|page|screen)\b", t):
        return "mfd_cycle_forward"
    if re.search(r"\b(previous|prev|back|last)\b", t) and re.search(r"\b(mfd|display|page|screen)\b", t):
        return "mfd_cycle_back"
    return None


class IntentClassifier:
    def __init__(self, commands: dict, model_name: str = "minishlab/potion-base-32M",
                 reject_threshold: float = 0.45):
        from model2vec import StaticModel  # CPU, numpy-only

        self.reject_threshold = reject_threshold
        self._model = StaticModel.from_pretrained(model_name)
        phrases, labels = [], []
        for intent, spec in commands.items():
            for ex in (spec.get("examples") or []):
                phrases.append(ex)
                labels.append(intent)
        self._labels = np.array(labels)
        emb = np.asarray(self._model.encode(phrases), dtype=np.float32)
        self._emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
        self._valid = set(commands)

    def classify(self, text: str) -> tuple[str, float]:
        """Return (intent, confidence). intent is 'chat' if nothing matched well."""
        t = (text or "").lower().strip()
        if not t:
            return CHAT, 0.0
        for rule in (_power_rule, _disambig):
            hit = rule(t)
            if hit and hit in self._valid:
                return hit, 1.0
        v = np.asarray(self._model.encode([t])[0], dtype=np.float32)
        v /= (np.linalg.norm(v) + 1e-9)
        sims = self._emb @ v
        j = int(np.argmax(sims))
        score = float(sims[j])
        if score < self.reject_threshold:
            return CHAT, score
        return str(self._labels[j]), score

    def classify_best(self, texts: list[str]) -> tuple[str, float, str]:
        """N-best rescoring: classify several ASR candidates and return
        (intent, confidence, chosen_text) for the most confident command among them.
        Falls back to chat if none is a command. The first text is the primary
        transcript and wins ties."""
        results, seen = [], set()
        for t in texts:
            key = (t or "").lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            intent, score = self.classify(t)
            results.append((intent, score, t))
        if not results:
            return CHAT, 0.0, ""
        return select_best(results)
