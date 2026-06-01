"""faster-whisper speech-to-text.

Loads a Whisper model on CUDA (with CPU fallback) and transcribes float32 mono
16 kHz audio. The shipped default (config/settings.json) is 'large-v3-turbo' at
float16, roughly 1.5 GB VRAM, which is comfortable on the target 5090. On an 8 GB
card prefer 'small' / int8 (about 0.5 GB) by editing settings.json.
"""
from __future__ import annotations

import logging

import numpy as np

from .cuda_paths import ensure_cuda_dlls

log = logging.getLogger("stella.stt")

# Phrases Whisper commonly hallucinates from silence/noise. If a transcript is
# nothing but one of these, treat it as empty so it never becomes a command.
_HALLUCINATIONS = {
    "", ".", "..", "...", "you", "thank you", "thanks", "thanks for watching",
    "thank you for watching", "please subscribe", "subscribe", "bye", "okay",
    "ok", "uh", "um", "i'm sorry", "you're welcome", "so", "yeah", ".you",
}


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
        # Keep only confident speech segments; Whisper marks noise/silence with a
        # high no_speech_prob and/or very low avg_logprob, then hallucinates text.
        kept = []
        for seg in segments:
            if getattr(seg, "no_speech_prob", 0.0) > 0.6:
                continue
            # Lenient logprob floor: only drop very-low-confidence segments, so a
            # quiet/accented real command is kept (the stoplist still catches the
            # common silence hallucinations below).
            if getattr(seg, "avg_logprob", 0.0) < -1.3:
                continue
            kept.append(seg.text)
        text = " ".join(kept).strip()
        if text.lower().strip(" .!?,") in _HALLUCINATIONS:
            return ""
        return text
