"""Regression tests for the deterministic power slot-rule (server.intent_classifier).

The rule is authoritative for power commands (score 1.0, never rescored), so a wrong
hit is a wrong key press with no recovery. These lock in the registry example phrases
and the collision cases an external review found. Pure regex, no model needed."""
import json
from pathlib import Path

from server.intent_classifier import _disambig, _power_rule

KB = json.loads((Path(__file__).resolve().parents[1] / "config" / "keybinds.json")
                .read_text(encoding="utf-8"))["keybinds"]

POWER_INTENTS = {
    "weapons_power_max", "weapons_power_inc", "weapons_power_dec", "lower_weapons_min",
    "weapons_power_toggle", "engines_power_max", "engines_power_inc", "engines_power_dec",
    "lower_engine_min", "thrusters_power_toggle", "shields_power_max", "shields_power_inc",
    "shields_power_dec", "lower_shields_min", "shields_power_toggle",
}


def test_all_power_example_phrases_resolve_to_their_intent():
    # Every seeded example of a power intent must hit the rule and resolve to itself.
    for intent in POWER_INTENTS:
        for phrase in KB[intent]["examples"]:
            assert _power_rule(phrase.lower()) == intent, (phrase, intent)


def test_lower_and_down_are_one_pip_decrease():
    # "lower"/"down" are gradual reduce-words -> dec (one pip), not min.
    assert _power_rule("lower shields") == "shields_power_dec"
    assert _power_rule("shields down") == "shields_power_dec"
    assert _power_rule("power down weapons") == "weapons_power_dec"


def test_hard_stop_words_are_min():
    assert _power_rule("drop shields") == "lower_shields_min"
    assert _power_rule("cut weapons") == "lower_weapons_min"
    assert _power_rule("kill engines") == "lower_engine_min"


def test_power_is_a_noun_not_a_toggle_trigger():
    # The bare word "power" must not pull a phrase toward toggle.
    assert _power_rule("more power to shields") == "shields_power_inc"
    assert _power_rule("all power to weapons") == "weapons_power_max"
    # "weapons power" alone has no direction -> falls through to the embedder.
    assert _power_rule("weapons power") is None


def test_explicit_toggle_words_still_toggle():
    assert _power_rule("turn off shields") == "shields_power_toggle"
    assert _power_rule("weapons on") == "weapons_power_toggle"
    assert _power_rule("arm weapons") == "weapons_power_toggle"


def test_no_pool_no_rule():
    assert _power_rule("landing gear") is None
    assert _power_rule("drop chaff") is None  # "drop" but no power pool


def test_dangerous_commands_never_hit_power_or_disambig_rules():
    # eject / self destruct must only come from the embedder + confirm gate.
    for phrase in ("eject", "eject eject eject", "self destruct", "blow it up"):
        assert _power_rule(phrase.lower()) is None
        assert _disambig(phrase.lower()) is None
