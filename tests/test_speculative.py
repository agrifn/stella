"""Tests for client.endpointing: the pure decision logic behind speculative STT.

Synthetic buffers only - no audio hardware, no Whisper. "Speech" is a constant
0.1-amplitude signal (well above the 0.0012 default RMS gate), "silence" is zeros.
"""
from __future__ import annotations

import numpy as np

from client.endpointing import speculation_valid, trailing_silence_s

SR = 16000
GATE = 0.0012


def _speech(seconds: float) -> np.ndarray:
    return np.full(int(seconds * SR), 0.1, dtype=np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


# -- trailing_silence_s ---------------------------------------------------------
def test_trailing_silence_known_tail():
    audio = np.concatenate([_speech(1.0), _silence(0.5)])
    assert trailing_silence_s(audio, SR, GATE) == 0.5


def test_trailing_silence_all_silence():
    audio = _silence(2.0)
    assert trailing_silence_s(audio, SR, GATE) == 2.0


def test_trailing_silence_all_speech():
    audio = _speech(1.5)
    assert trailing_silence_s(audio, SR, GATE) == 0.0


def test_trailing_silence_empty_and_disabled_gate():
    assert trailing_silence_s(np.zeros(0, dtype=np.float32), SR, GATE) == 0.0
    assert trailing_silence_s(None, SR, GATE) == 0.0
    # gate <= 0 means the loudness gate is disabled: no silence detection.
    assert trailing_silence_s(_silence(1.0), SR, 0.0) == 0.0


def test_trailing_silence_ignores_earlier_pauses():
    # A mid-utterance pause must not count, only the tail.
    audio = np.concatenate([_speech(0.5), _silence(0.4), _speech(0.5), _silence(0.2)])
    assert trailing_silence_s(audio, SR, GATE) == 0.2


# -- speculation_valid ----------------------------------------------------------
def test_valid_when_equal_or_shrank():
    final = np.concatenate([_speech(1.0), _silence(0.4)])
    assert speculation_valid(len(final), final, SR, GATE) is True
    assert speculation_valid(len(final) + 100, final, SR, GATE) is True


def test_valid_when_growth_within_tolerance():
    # Grew by 0.1s (under the 0.15s tolerance): valid regardless of content.
    final = np.concatenate([_speech(1.0), _speech(0.1)])
    spec_len = len(_speech(1.0))
    assert speculation_valid(spec_len, final, SR, GATE) is True


def test_valid_when_growth_is_silence():
    # THE typical PTT case: snapshot at end of speech, pilot keeps holding the key
    # for 0.4s of silence. The buffer grew well past the tolerance, but only with
    # silence, so the speculation must stand (a raw length check would discard it).
    spec_len = len(np.concatenate([_speech(1.0), _silence(0.35)]))
    final = np.concatenate([_speech(1.0), _silence(0.35), _silence(0.4)])
    assert speculation_valid(spec_len, final, SR, GATE) is True


def test_invalid_when_speech_resumed_after_snapshot():
    # Pilot spoke again after the snapshot: the speculative transcript is stale.
    spec_len = len(np.concatenate([_speech(1.0), _silence(0.35)]))
    final = np.concatenate([_speech(1.0), _silence(0.35), _speech(0.5)])
    assert speculation_valid(spec_len, final, SR, GATE) is False


def test_invalid_when_speech_resumed_then_paused_again():
    # Speech after the snapshot followed by a fresh silent tail: still stale (the
    # silent tail is shorter than the total growth).
    spec_len = len(np.concatenate([_speech(1.0), _silence(0.35)]))
    final = np.concatenate([_speech(1.0), _silence(0.35), _speech(0.5), _silence(0.3)])
    assert speculation_valid(spec_len, final, SR, GATE) is False


def test_valid_with_none_final():
    assert speculation_valid(100, None, SR, GATE) is True
