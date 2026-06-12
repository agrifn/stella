"""Tests for client.local_intent.LocalIntentResolver: in-process classification.

model2vec is stubbed with a deterministic fake (crc32-seeded random unit vectors),
so no model download and no network. An exact example phrase encodes to the exact
same vector (cosine 1.0); unrelated text encodes to an independent random vector
(cosine ~0 in 256 dims, far below the reject threshold); the "kinda " prefix
produces a vector with cosine exactly 0.5 to its base phrase, landing inside the
[reject, clarify) gray zone to exercise the clarify flag.

Commands here deliberately avoid weapons/engines/shields words so the classifier's
deterministic power slot-rule (score 1.0) never short-circuits the embedding path.
"""
from __future__ import annotations

import json
import os
import sys
import types
import zlib

import numpy as np
import pytest

_DIM = 256


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def _vec(text: str) -> np.ndarray:
    t = (text or "").lower().strip()
    if t.startswith("kinda "):
        # Exactly cosine 0.5 to the base phrase: 0.5*base + sqrt(0.75)*orthogonal.
        base = _vec(t[len("kinda "):])
        rng = np.random.default_rng(zlib.crc32(("noise:" + t).encode()))
        noise = rng.standard_normal(_DIM).astype(np.float32)
        noise -= np.dot(noise, base) * base  # make orthogonal to base
        return (0.5 * base + np.sqrt(0.75) * _unit(noise)).astype(np.float32)
    rng = np.random.default_rng(zlib.crc32(t.encode()))
    return _unit(rng.standard_normal(_DIM).astype(np.float32))


class _FakeStaticModel:
    @classmethod
    def from_pretrained(cls, name):  # noqa: ANN001 - mirrors model2vec's API
        return cls()

    def encode(self, texts):
        return np.stack([_vec(t) for t in texts])


@pytest.fixture(autouse=True)
def fake_model2vec(monkeypatch):
    mod = types.ModuleType("model2vec")
    mod.StaticModel = _FakeStaticModel
    monkeypatch.setitem(sys.modules, "model2vec", mod)


KEYBINDS = {
    "_comment": "test",
    "keybinds": {
        "landing_gear": {
            "key": "n",
            "description": "Toggle landing gear",
            "examples": ["landing gear", "gear down"],
            "ack": "Landing gear.",
        },
        "decouple_toggle": {
            "key": "c",
            "description": "Toggle decoupled flight",
            "examples": ["decouple"],
            "ack": "Decoupling.",
        },
        "self_destruct": {
            "key": "backspace",
            "hold": True,
            "hold_duration": 1.5,
            "confirm_required": True,
            "description": "Self destruct",
            "examples": ["self destruct"],
            "ack": "Self destruct armed.",
        },
    },
}


def _make_resolver(tmp_path, clarify: float = 0.55):
    from client.local_intent import LocalIntentResolver

    p = tmp_path / "keybinds.json"
    p.write_text(json.dumps(KEYBINDS), encoding="utf-8")
    return LocalIntentResolver(p, model_name="fake", reject_threshold=0.45,
                               clarify_threshold=clarify), p


def _touch(path, offset_s: float) -> None:
    """Force a visibly different mtime (some filesystems are coarse)."""
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + offset_s))


def test_resolve_exact_phrase_returns_full_result(tmp_path):
    resolver, _ = _make_resolver(tmp_path)
    res = resolver.resolve("landing gear")
    assert res.intent == "landing_gear"
    assert res.keybind == "n"
    assert res.response_text == "Landing gear."
    assert res.confidence == pytest.approx(1.0)
    assert res.clarify is False
    assert res.audio_b64 is None  # TTS is fetched separately via /speak
    assert res.chosen_text == "landing gear"


def test_resolve_carries_safety_fields(tmp_path):
    resolver, _ = _make_resolver(tmp_path)
    res = resolver.resolve("self destruct")
    assert res.intent == "self_destruct"
    assert res.confirm_required is True
    assert res.hold is True
    assert res.hold_duration == 1.5


def test_unrelated_text_is_chat(tmp_path):
    resolver, _ = _make_resolver(tmp_path)
    res = resolver.resolve("what is the best ship for mining")
    assert res.intent == "chat"
    assert res.keybind is None
    assert res.clarify is False  # chat never clarifies
    assert res.confidence < 0.45


def test_gray_zone_sets_clarify(tmp_path):
    # "kinda landing gear" scores cosine 0.5 against "landing gear": above the 0.45
    # reject threshold (it IS the command) but below the 0.55 clarify threshold.
    resolver, _ = _make_resolver(tmp_path)
    res = resolver.resolve("kinda landing gear")
    assert res.intent == "landing_gear"
    assert res.confidence == pytest.approx(0.5, abs=1e-5)
    assert res.clarify is True


def test_nbest_candidates_rescored(tmp_path):
    # The primary transcript is junk; an ASR alternate is an exact command. The
    # resolver must pick the alternate and report it as chosen_text.
    resolver, _ = _make_resolver(tmp_path)
    res = resolver.resolve("lend in gere", candidates=["landing gear"])
    assert res.intent == "landing_gear"
    assert res.chosen_text == "landing gear"


def test_hot_reload_picks_up_added_command(tmp_path):
    resolver, p = _make_resolver(tmp_path)
    assert resolver.resolve("scanner ping").intent == "chat"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["keybinds"]["scanner_ping"] = {
        "key": "tab", "description": "Ping", "examples": ["scanner ping"],
        "ack": "Pinging.",
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    _touch(p, 10)
    res = resolver.resolve("scanner ping")
    assert res.intent == "scanner_ping"
    assert res.keybind == "tab"


def test_malformed_reload_keeps_previous_classifier(tmp_path):
    resolver, p = _make_resolver(tmp_path)
    p.write_text("{ this is not json", encoding="utf-8")
    _touch(p, 10)
    # Must not raise, and the previous command set keeps working.
    res = resolver.resolve("landing gear")
    assert res.intent == "landing_gear"
    assert res.keybind == "n"


def test_fixed_malformed_file_reloads_on_next_change(tmp_path):
    resolver, p = _make_resolver(tmp_path)
    p.write_text("{ broken", encoding="utf-8")
    _touch(p, 10)
    resolver.resolve("landing gear")  # serves the old classifier
    data = dict(KEYBINDS)
    data["keybinds"] = dict(KEYBINDS["keybinds"])
    data["keybinds"]["scanner_ping"] = {
        "key": "tab", "description": "Ping", "examples": ["scanner ping"],
        "ack": "Pinging.",
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    _touch(p, 20)
    assert resolver.resolve("scanner ping").intent == "scanner_ping"
