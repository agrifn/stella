"""Tests for the GUI's pure command-grouping helper (client/ui/grouping.py)."""
from client.ui.grouping import GROUP_ORDER, categorize, group_commands


def test_dangerous_wins_over_keywords():
    assert categorize("eject", "Eject from the ship", confirm_required=True) == "Dangerous"
    assert categorize("shields_max", "", confirm_required=True) == "Dangerous"


def test_power_family():
    for intent in ("shields_max", "weapons_power_toggle", "engines_min",
                   "reset_power", "thrusters_power_toggle", "power_toggle_all"):
        assert categorize(intent) == "Power", intent


def test_flight_family():
    for intent in ("landing_gear", "vtol_toggle", "cruise_control_toggle",
                   "decouple_toggle", "nav_mode", "systems_ready", "request_landing"):
        assert categorize(intent) == "Flight", intent


def test_combat_and_apps_and_other():
    assert categorize("fire_flares") == "Combat & Utility"
    assert categorize("cycle_gimbals", "Cycle gimbal mode") == "Combat & Utility"
    assert categorize("open_mobiglas") == "Apps"
    assert categorize("starmap") == "Apps"
    assert categorize("mystery_intent", "does something odd") == "Other"


def test_description_keywords_count_too():
    assert categorize("f7_thing", "Set shield power to maximum") == "Power"


def test_group_commands_order_and_skips_empty():
    cmds = [
        {"intent": "self_destruct", "confirm_required": True},
        {"intent": "shields_max"},
        {"intent": "landing_gear"},
        {"intent": "weird_one"},
    ]
    groups = group_commands(cmds)
    labels = [g for g, _ in groups]
    assert labels == [g for g in GROUP_ORDER if g in ("Power", "Flight", "Other", "Dangerous")]
    assert labels[-1] == "Dangerous"
    by_label = dict(groups)
    assert [c["intent"] for c in by_label["Power"]] == ["shields_max"]
    assert [c["intent"] for c in by_label["Dangerous"]] == ["self_destruct"]
