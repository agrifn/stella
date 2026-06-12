"""Pure endpointing helpers for speculative STT (numpy-only, no audio deps).

Pilots keep holding PTT for 200 to 400ms after their last word. If transcription
starts the moment the live buffer goes quiet (instead of at key release), most of
that hold time is recovered. These two functions are the decision logic for that:
when to fire a speculative decode, and whether its result is still valid at
release. They live in their own module (not audio_capture) so the unit tests can
import them without keyboard/sounddevice installed.
"""
from __future__ import annotations

import numpy as np


def trailing_silence_s(audio: np.ndarray, samplerate: int, rms_gate: float,
                       block_s: float = 0.05) -> float:
    """Seconds at the END of the buffer whose RMS is below the gate.

    Scans backwards in block_s chunks and stops at the first loud block, so the
    cost is proportional to the silent tail, not the whole buffer. With
    rms_gate <= 0 every block counts as speech and the result is 0.0 (callers
    that disable the loudness gate also disable silence detection).
    """
    if audio is None or len(audio) == 0 or samplerate <= 0 or rms_gate <= 0:
        return 0.0
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    n = max(1, int(block_s * samplerate))
    silent = 0
    i = len(audio)
    while i > 0:
        j = max(0, i - n)
        block = audio[j:i]
        if float(np.sqrt(np.mean(np.square(block)))) >= rms_gate:
            break
        silent += i - j
        i = j
    return silent / samplerate


def speculation_valid(spec_len: int, final_audio: np.ndarray, samplerate: int,
                      rms_gate: float, tolerance_s: float = 0.15) -> bool:
    """Is a speculative transcript (taken at spec_len samples) still valid for the
    final buffer at PTT release?

    The buffer keeps growing with SILENCE for as long as the key stays down, so
    growth alone says nothing - judging by raw length would discard nearly every
    speculation (the post-snapshot hold is the whole point). Valid means nothing
    was SPOKEN after the snapshot: either the buffer grew less than the tolerance,
    or everything past the snapshot sits inside the buffer's silent tail.
    """
    if final_audio is None:
        return True
    final_len = len(final_audio)
    if final_len <= spec_len:
        return True  # shrank or equal: nothing new arrived
    growth_s = (final_len - spec_len) / samplerate
    if growth_s <= tolerance_s:
        return True
    return trailing_silence_s(final_audio, samplerate, rms_gate) + tolerance_s >= growth_s
