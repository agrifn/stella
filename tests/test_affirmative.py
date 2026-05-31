"""The confirmation gate is the only thing between a misheard reply and a
destructive keypress (eject / self destruct). These cases lock in that negations
veto and that the known false positives never fire."""
import pytest

from client.confirm import is_affirmative

# Must NOT confirm: negations (even with an affirmative word present), neutral, empty.
NEGATIVE_OR_NEUTRAL = [
    "no, don't do it", "no go back", "let it go", "abort", "cancel", "negative",
    "stop", "belay that", "wait", "hold on", "nope", "do not", "", "   ",
    "maybe later", "what was that",
]

# Must confirm.
AFFIRMATIVE = [
    "yes", "yeah do it", "yep", "yup", "confirm", "confirmed", "affirmative",
    "execute", "engage", "do it", "yes, do it now",
]


@pytest.mark.parametrize("text", NEGATIVE_OR_NEUTRAL)
def test_not_affirmative(text):
    assert is_affirmative(text) is False


@pytest.mark.parametrize("text", AFFIRMATIVE)
def test_affirmative(text):
    assert is_affirmative(text) is True
