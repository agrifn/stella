"""parse_keybind is the pure, security-relevant translation from a keybind string
to modifiers/key/taps. These cases lock in the tricky behaviours (aliases,
double-tap, lone-modifier-as-key, combos) so a refactor can't silently break them."""
import pytest

from client.keybind_executor import parse_keybind


def test_single_key():
    p = parse_keybind("0")
    assert p.modifiers == () and p.key == "0" and p.taps == 1


def test_modifier_combo():
    p = parse_keybind("alt+y")
    assert p.modifiers == ("alt",) and p.key == "y" and p.taps == 1


def test_multi_modifier():
    p = parse_keybind("ctrl+alt+delete")
    assert p.modifiers == ("ctrl", "alt") and p.key == "delete"


def test_alias_period():
    assert parse_keybind("period").key == "."


def test_double_tap():
    p = parse_keybind("f10+f10")
    assert p.modifiers == () and p.key == "f10" and p.taps == 2


def test_lone_modifier_as_key():
    # 'left_shift' (boost held) is a modifier used AS the key
    p = parse_keybind("left_shift")
    assert p.modifiers == () and p.key == "shiftleft"


def test_left_right_modifier_alias():
    p = parse_keybind("altright+y")
    assert p.modifiers == ("altright",) and p.key == "y"


def test_empty_raises():
    with pytest.raises(ValueError):
        parse_keybind("  ")


def test_two_distinct_keys_raises():
    with pytest.raises(ValueError):
        parse_keybind("a+b")
