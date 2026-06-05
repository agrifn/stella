"""End-to-end test of server.intent_classifier on HELD-OUT paraphrases (not verbatim
in the registry examples). Run from repo root."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from server.intent_classifier import IntentClassifier  # noqa: E402

KB = json.loads(Path("config/keybinds.json").read_text(encoding="utf-8"))["keybinds"]
clf = IntentClassifier(KB)

# (phrase, expected) - paraphrases, NOT copied from the example sets
TESTS = [
    # power (rule layer)
    ("give me more weapon power", "weapons_power_inc"),
    ("weapons to maximum", "weapons_power_max"),
    ("drop the shields all the way", "lower_shields_min"),
    ("ease off the engines", "engines_power_dec"),
    ("kill the weapons", "lower_weapons_min"),
    ("shields to full", "shields_power_max"),
    ("turn the engines on", "thrusters_power_toggle"),
    ("punch it", "engines_power_max"),
    ("brace", "shields_power_max"),
    ("even out the power", "reset_power"),
    # non-power (embedding)
    ("put the gear down", "landing_gear"),
    ("drop the landing gear", "landing_gear"),
    ("open the star map", "starmap"),
    ("let me see behind me", "look_behind"),
    ("deploy countermeasures", "decoy_burst"),
    ("switch to third person view", "camera_cycle"),
    ("open my mobiglas", "mobiglas"),
    ("punch out now", "eject"),
    ("get out of the seat", "exit_seat"),
    ("enter scanning mode", "scan_mode"),
    ("next mfd page", "mfd_cycle_forward"),
    ("go to vtol", "vtol_toggle"),
    # non-commands -> chat (reject)
    ("i wonder what is for dinner", "chat"),
    ("the weather is really nice today", "chat"),
    ("did you see that ship explode", "chat"),
]

ok = 0
print(f"reject_threshold={clf.reject_threshold}\n")
for text, want in TESTS:
    pred, score = clf.classify(text)
    mark = "OK " if pred == want else "XX "
    ok += pred == want
    print(f"  {mark}{text!r:34s} -> {pred:22s} ({score:.2f})  want {want}")
print(f"\nCOMBINED HELD-OUT: {ok}/{len(TESTS)} = {ok/len(TESTS):.1%}")
