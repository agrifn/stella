"""Tests for server.intent_classifier.select_best: the pure n-best rescoring picker.

select_best is model-free (it takes already-classified (intent, score, text) tuples),
so these stay in the pure-logic suite - no model2vec download required.
"""
from server.intent_classifier import CHAT, select_best


def test_picks_highest_scoring_command():
    results = [
        (CHAT, 0.40, "shoes to max"),
        ("shields_power_max", 0.82, "shields to max"),
    ]
    assert select_best(results) == ("shields_power_max", 0.82, "shields to max")


def test_command_beats_higher_scoring_chat():
    # A confident chat must NOT outrank a real command - we prefer acting on the
    # command among the candidates (the chat score is on a different scale).
    results = [
        (CHAT, 0.95, "what is the best ship"),
        ("landing_gear", 0.60, "landing gear"),
    ]
    assert select_best(results)[0] == "landing_gear"


def test_all_chat_returns_best_chat():
    results = [
        (CHAT, 0.30, "hello there"),
        (CHAT, 0.41, "how are you"),
    ]
    assert select_best(results) == (CHAT, 0.41, "how are you")


def test_ties_keep_first_seen():
    # The original transcript is passed first; on an equal score it should win over a
    # later alternate so behaviour is stable.
    results = [
        ("shields_power_max", 0.70, "shields to max"),
        ("weapons_power_max", 0.70, "weapons to max"),
    ]
    assert select_best(results) == ("shields_power_max", 0.70, "shields to max")


def test_empty():
    assert select_best([]) == (CHAT, 0.0, "")
