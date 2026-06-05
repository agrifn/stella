"""Tests for client.mode_switch.detect_mode_switch."""
from client.mode_switch import detect_mode_switch


def test_switch_to_chat():
    assert detect_mode_switch("switch to chat") == "CHAT"
    assert detect_mode_switch("Switch to chat.") == "CHAT"


def test_switch_to_command():
    assert detect_mode_switch("switch to command") == "COMMAND"


def test_mode_phrasings():
    assert detect_mode_switch("chat mode") == "CHAT"
    assert detect_mode_switch("command mode") == "COMMAND"
    assert detect_mode_switch("go to chat") == "CHAT"
    assert detect_mode_switch("change to command") == "COMMAND"
    assert detect_mode_switch("put me in chat mode") == "CHAT"


def test_not_a_mode_switch():
    # normal commands / chat must not flip the mode
    assert detect_mode_switch("raise shields") is None
    assert detect_mode_switch("max weapons") is None
    assert detect_mode_switch("fire three flares") is None
    assert detect_mode_switch("command the ship to land") is None  # "command" but no switch word
    assert detect_mode_switch("") is None
    assert detect_mode_switch("hello there") is None


def test_ambiguous_returns_none():
    # mentions both -> ambiguous -> no switch
    assert detect_mode_switch("switch chat and command") is None
