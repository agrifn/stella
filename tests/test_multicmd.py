"""Tests for client.multicmd: splitting one utterance into command steps + repeats."""
from client.multicmd import MAX_REPEAT, parse_repeat, split_commands


# -- single command is unchanged (no regression for the common case) ----------
def test_single_command_unchanged():
    assert split_commands("shields to max") == [("shields to max", 1)]


def test_single_command_with_number_at_end_is_not_a_repeat():
    # Trailing numbers are command parameters (e.g. "weapon group 3"), not counts.
    assert split_commands("weapon group 3") == [("weapon group 3", 1)]


def test_empty():
    assert split_commands("") == []
    assert split_commands("   ") == []


# -- multiple commands (#3) ----------------------------------------------------
def test_split_on_and():
    assert split_commands("lower shields and raise engine power") == [
        ("lower shields", 1), ("raise engine power", 1)]


def test_split_on_then():
    assert split_commands("boost then full stop") == [("boost", 1), ("full stop", 1)]


def test_split_on_and_then():
    assert split_commands("decouple and then full stop") == [
        ("decouple", 1), ("full stop", 1)]


def test_split_does_not_cut_inside_words():
    # "command" contains "and" but must not be split.
    assert split_commands("command mode") == [("command mode", 1)]


# -- repeats (#2) --------------------------------------------------------------
def test_repeat_number_word():
    assert split_commands("fire three flares") == [("fire flares", 3)]


def test_repeat_digit():
    assert split_commands("fire 3 flares") == [("fire flares", 3)]


def test_repeat_with_trailing_times():
    assert parse_repeat("fire flares three times") == ("fire flares", 3)


def test_repeat_a_means_one_no_repeat():
    # "a"/"one" -> count 1 -> no repeat, text preserved.
    assert parse_repeat("fire a flare") == ("fire a flare", 1)


def test_large_number_is_not_a_repeat():
    # > MAX_REPEAT stays in the text, count 1 (e.g. "set power to 50").
    assert parse_repeat("set power to 50") == ("set power to 50", 1)


def test_repeat_capped():
    assert MAX_REPEAT == 10
    assert parse_repeat("fire ten flares") == ("fire flares", 10)


# -- combined: a repeat AND a second command -----------------------------------
def test_repeat_and_second_command():
    assert split_commands("fire three flares and raise shields") == [
        ("fire flares", 3), ("raise shields", 1)]
