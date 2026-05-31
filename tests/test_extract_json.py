"""_extract_json tolerates the ways small models wrap JSON (clean, code fence,
prose). If it fails the server falls back to a 'chat' intent, so keep it robust."""
from server.llm_providers import _extract_json


def test_clean_json():
    out = _extract_json('{"intent":"chat","confirm_required":false,"response_text":"hi"}')
    assert out["intent"] == "chat"


def test_code_fence():
    s = '```json\n{"intent":"eject","confirm_required":true,"response_text":"x"}\n```'
    assert _extract_json(s)["intent"] == "eject"


def test_prose_wrapped():
    s = 'Sure: {"intent":"shields_max","confirm_required":false,"response_text":""} hope this helps'
    assert _extract_json(s)["intent"] == "shields_max"


def test_whitespace_padding():
    assert _extract_json('   {"intent":"chat"}\n')["intent"] == "chat"
