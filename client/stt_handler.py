"""faster-whisper speech-to-text.

Loads a Whisper model on CUDA (with CPU fallback) and transcribes float32 mono
16 kHz audio. VRAM footprint for 'small' int8 is ~0.5 GB, transient, so it does
not meaningfully compete with Star Citizen on an 8 GB card.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from .cuda_paths import ensure_cuda_dlls

log = logging.getLogger("stella.stt")


class STTHandler:
    def __init__(self, model_size: str, device: str = "cuda", compute_type: str = "int8"):
        ensure_cuda_dlls()
        from faster_whisper import WhisperModel  # imported after DLL paths are set

        self.samplerate = 16000  # Whisper operates at 16 kHz
        try:
            self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
            self.device = device
        except Exception as e:  # noqa: BLE001 - fall back to CPU so STT still works
            log.warning("CUDA Whisper init failed (%s); falling back to CPU int8", e)
            self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
            self.device = "cpu"
        log.info("Whisper '%s' ready on %s", model_size, self.device)

    def warm(self) -> None:
        """Run one inference so the first real transcription isn't slow."""
        self.transcribe(np.zeros(self.samplerate, dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono array sampled at 16 kHz. Returns text."""
        if audio is None or len(audio) == 0:
            return ""
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        segments, _info = self._model.transcribe(audio, vad_filter=True, language="en")
        return " ".join(seg.text for seg in segments).strip()
