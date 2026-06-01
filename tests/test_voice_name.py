"""Voice-name validation must allow real voices (Piper names AND custom ones like
'cortana') while blocking path traversal in the unauthenticated download endpoint."""
import pytest

from server.tts_handler import _validate_voice_name


@pytest.mark.parametrize("name", [
    "cortana",                  # custom installed voice (regression: was rejected)
    "en_US-lessac-medium",
    "en_GB-alba-medium",
    "en_US-amy-medium",
    "voice_2",
])
def test_valid_names_pass(name):
    _validate_voice_name(name)  # should not raise


@pytest.mark.parametrize("name", [
    "../../etc/passwd", "en_US/../x", "a/b", "..", ".", "", "has space",
    "name.with.dot", "back\\slash",
])
def test_traversal_and_junk_rejected(name):
    with pytest.raises(ValueError):
        _validate_voice_name(name)
