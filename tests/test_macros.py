"""Macro text <-> step-dict conversion used by the command-manager GUI."""
from client.macros import macro_to_text, parse_macro_line


def test_parse_hold():
    assert parse_macro_line("f7 hold") == {"key": "f7", "hold": True, "taps": 1, "delay": 0.1}


def test_parse_taps():
    assert parse_macro_line("h x3")["taps"] == 3


def test_parse_delay():
    assert parse_macro_line("tab /0.2")["delay"] == 0.2


def test_parse_combo_key():
    step = parse_macro_line("altleft+c")
    assert step["key"] == "altleft+c" and step["hold"] is False


def test_parse_empty_is_none():
    assert parse_macro_line("   ") is None


def test_parse_bad_delay_ignored():
    assert parse_macro_line("tab /oops")["delay"] == 0.1  # falls back to default


def test_roundtrip_hold():
    seq = [{"key": "f7", "hold": True, "taps": 1, "delay": 0.1}]
    assert macro_to_text(seq) == "f7 hold"


def test_roundtrip_taps_and_delay():
    assert macro_to_text([{"key": "h", "taps": 3, "delay": 0.1}]) == "h x3"
    assert macro_to_text([{"key": "tab", "taps": 1, "delay": 0.2}]) == "tab /0.2"


def test_roundtrip_multi_step():
    seq = [{"key": "f7", "hold": True, "taps": 1, "delay": 0.1},
           {"key": "f6", "hold": True, "taps": 1, "delay": 0.1}]
    assert macro_to_text(seq) == "f7 hold\nf6 hold"
    # and parsing each line back yields the same steps
    assert [parse_macro_line(ln) for ln in macro_to_text(seq).splitlines()] == seq
