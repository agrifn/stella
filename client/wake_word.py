"""Spoken wake-word detection (openWakeWord).

Runs a tiny always-on model over the live microphone audio and fires a callback
when the wake word ("Stella") is heard. It does NOT open its own audio stream -
it taps the frames the PTTRecorder is already capturing (set via
PTTRecorder.set_monitor), so there's only ever one input stream on the mic.

openWakeWord wants sequential 16 kHz, 16-bit PCM frames of 1280 samples (80 ms)
and keeps internal state across frames, so we buffer the variable-size capture
blocks and feed it exactly one frame at a time.

Feature-flagged: the engine only constructs this when wake_word_enabled is set
and a model file exists, so the openwakeword dependency is optional.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

import numpy as np

log = logging.getLogger("stella.wake")

_FRAME = 1280  # samples @ 16 kHz = 80 ms, the openWakeWord frame size


def _ensure_base_models() -> None:
    """openWakeWord needs two shared feature models (melspectrogram, embedding) that
    are NOT bundled with the pip package - they download on first setup. A fresh
    `pip install` therefore has the wake model's deps but not these, so Model() would
    fail. Fetch them once if missing (network only on the first run), mirroring how
    the server auto-downloads its default Piper voice."""
    import os

    import openwakeword
    res = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
    needed = ("melspectrogram.onnx", "embedding_model.onnx")
    if all(os.path.exists(os.path.join(res, m)) for m in needed):
        return
    log.info("downloading openWakeWord base feature models (one-time)...")
    from openwakeword.utils import download_models
    download_models(model_names=[])  # empty list = base feature models only


class WakeWordListener:
    def __init__(self, model_path: str, threshold: float, samplerate: int,
                 on_wake: Callable[[], None], refractory_s: float = 2.0):
        self.threshold = threshold
        self.samplerate = samplerate
        self.on_wake = on_wake
        self._refractory = refractory_s
        self._buf = np.zeros(0, dtype=np.int16)
        self._last_fire = 0.0
        self._enabled = True
        self._model = None
        self._name = None
        # Lazy import so openwakeword is only required when the feature is on.
        _ensure_base_models()
        from openwakeword.model import Model  # noqa: PLC0415

        self._model = Model(wakeword_models=[model_path], inference_framework="onnx")
        # The score key is the model's own name (file stem).
        self._name = list(self._model.models.keys())[0]
        log.info("wake-word listener ready: %s (threshold %.2f)", self._name, threshold)

    def set_enabled(self, on: bool) -> None:
        """Pause/resume detection (e.g. only listen while STELLA is asleep)."""
        self._enabled = on

    def feed(self, indata: np.ndarray) -> None:
        """Consume a capture block (float32 mono in [-1,1]); detect on full frames."""
        if not self._enabled or self._model is None:
            return
        try:
            pcm = (np.asarray(indata, dtype=np.float32).reshape(-1) * 32767.0).astype(np.int16)
            self._buf = np.concatenate((self._buf, pcm))
            while len(self._buf) >= _FRAME:
                frame, self._buf = self._buf[:_FRAME], self._buf[_FRAME:]
                scores = self._model.predict(frame)
                if scores.get(self._name, 0.0) >= self.threshold:
                    now = time.time()
                    if now - self._last_fire >= self._refractory:
                        self._last_fire = now
                        log.info("wake word detected (%.2f)", scores[self._name])
                        self.on_wake()
        except Exception:  # noqa: BLE001 - detection must never crash the audio path
            log.exception("wake-word detection error")
