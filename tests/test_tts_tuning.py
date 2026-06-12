"""Tests for the Piper synthesis-tuning logic in TTSHandler.

Pure-logic only: tuning_for / set_tuning persist and merge per-voice overrides on
top of the config defaults and never touch the Piper subprocess, so they run in
CI without any model or audio. The handler is built against a temp voices dir.
"""
from __future__ import annotations

import json

import pytest

from server.config import TTSConfig
from server.tts_handler import TTSHandler


def _handler(tmp_path):
    # A voice file must exist for set_voice / active load, but tuning never runs it.
    (tmp_path / "en_US-amy-medium.onnx").write_bytes(b"stub")
    (tmp_path / "en_US-amy-medium.onnx.json").write_text(
        json.dumps({"audio": {"sample_rate": 22050}}), encoding="utf-8")
    cfg = TTSConfig(voices_dir=tmp_path, voice="en_US-amy-medium")
    return TTSHandler(cfg), cfg


def test_defaults_when_no_override(tmp_path):
    h, cfg = _handler(tmp_path)
    t = h.tuning_for()
    assert t["length_scale"] == cfg.length_scale
    assert t["noise_scale"] == cfg.noise_scale
    assert t["noise_w_scale"] == cfg.noise_w_scale
    assert t["sentence_silence"] == cfg.sentence_silence
    assert set(t) == {"length_scale", "noise_scale", "noise_w_scale", "sentence_silence"}


def test_set_partial_override_merges_over_defaults(tmp_path):
    h, cfg = _handler(tmp_path)
    h.set_tuning("en_US-amy-medium", {"length_scale": 1.2})
    t = h.tuning_for("en_US-amy-medium")
    assert t["length_scale"] == 1.2
    assert t["noise_scale"] == cfg.noise_scale  # untouched key keeps the default


def test_override_persists_to_disk_and_reloads(tmp_path):
    h, _ = _handler(tmp_path)
    h.set_tuning("en_US-amy-medium", {"length_scale": 1.15, "sentence_silence": 0.4})
    # A fresh handler over the same dir must see the saved tuning.
    cfg = TTSConfig(voices_dir=tmp_path, voice="en_US-amy-medium")
    h2 = TTSHandler(cfg)
    t = h2.tuning_for("en_US-amy-medium")
    assert t["length_scale"] == 1.15
    assert t["sentence_silence"] == 0.4


def test_empty_dict_resets_to_defaults(tmp_path):
    h, cfg = _handler(tmp_path)
    h.set_tuning("en_US-amy-medium", {"length_scale": 1.3})
    h.set_tuning("en_US-amy-medium", {})  # reset
    assert h.tuning_for("en_US-amy-medium")["length_scale"] == cfg.length_scale
    assert "en_US-amy-medium" not in h._tuning


def test_unknown_keys_ignored(tmp_path):
    h, _ = _handler(tmp_path)
    h.set_tuning("en_US-amy-medium", {"length_scale": 1.1, "bogus": 9, "speed": 2})
    assert set(h._tuning["en_US-amy-medium"]) == {"length_scale"}


def test_set_tuning_clears_cache(tmp_path):
    h, _ = _handler(tmp_path)
    h._cache[("piper", "en_US-amy-medium", "hi", ())] = b"stale"
    h.set_tuning("en_US-amy-medium", {"length_scale": 1.1})
    assert len(h._cache) == 0


def test_per_voice_isolation(tmp_path):
    h, cfg = _handler(tmp_path)
    h.set_tuning("en_US-amy-medium", {"length_scale": 1.25})
    # A different voice keeps the defaults.
    assert h.tuning_for("en_GB-jenny_dioco-medium")["length_scale"] == cfg.length_scale


def test_invalid_voice_name_rejected(tmp_path):
    h, _ = _handler(tmp_path)
    with pytest.raises(ValueError):
        h.set_tuning("../etc/passwd", {"length_scale": 1.1})
