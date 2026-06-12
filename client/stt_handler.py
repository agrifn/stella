"""faster-whisper speech-to-text.

Loads a Whisper model on CUDA (with CPU fallback) and transcribes float32 mono
16 kHz audio. The shipped default (config/settings.json) is 'large-v3-turbo' at
float16, roughly 1.5 GB VRAM, which is comfortable on the target 5090. On an 8 GB
card prefer 'small' / int8 (about 0.5 GB) by editing settings.json.

The primary decode is GREEDY by default (stt_beam_size=1): the vocabulary is a
small closed command set, so beam search buys almost nothing while costing 2 to 3x
the decode time. Recognition errors are backstopped by the n-best rescoring pass
plus the classifier's reject/clarify thresholds, so a rare greedy slip is recovered
instead of fired. Raise stt_beam_size in settings.json to get the old robust
beam-search behavior back.
"""
from __future__ import annotations

import logging
import threading

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
    def __init__(self, model_size: str, device: str = "cuda", compute_type: str = "int8",
                 no_speech_prob: float = 0.6, avg_logprob: float = -1.3,
                 beam_size: int = 1):
        ensure_cuda_dlls()
        from faster_whisper import WhisperModel  # imported after DLL paths are set

        self._max_no_speech = no_speech_prob   # drop segments noisier than this
        self._min_logprob = avg_logprob        # drop segments less confident than this
        self._beam_size = max(1, int(beam_size))  # 1 = greedy (see module docstring)
        # Serialize ALL decode entry points: speculative STT transcribes from a
        # background thread while PTT is still held, and WhisperModel inference must
        # not run concurrently with itself. The lock covers segment consumption too,
        # because faster-whisper decodes lazily while the generator is iterated.
        self._decode_lock = threading.Lock()
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

    def _filter(self, segments) -> str:
        """Join confident speech segments into a transcript. Whisper marks
        noise/silence with a high no_speech_prob and/or very low avg_logprob, then
        hallucinates text; drop those and the common silence hallucinations."""
        kept = []
        for seg in segments:
            if getattr(seg, "no_speech_prob", 0.0) > self._max_no_speech:
                continue
            # Lenient logprob floor: only drop very-low-confidence segments, so a
            # quiet/accented real command is kept (the stoplist still catches the
            # common silence hallucinations below).
            if getattr(seg, "avg_logprob", 0.0) < self._min_logprob:
                continue
            kept.append(seg.text)
        text = " ".join(kept).strip()
        if text.lower().strip(" .!?,") in _HALLUCINATIONS:
            return ""
        return text

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono array sampled at 16 kHz. Returns text. Greedy
        by default (beam_size=1) for speed on the closed command vocabulary;
        condition_on_previous_text / timestamps are off because every utterance is
        an independent 1 to 3 second command, not continuous dictation."""
        if audio is None or len(audio) == 0:
            return ""
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        with self._decode_lock:
            segments, _info = self._model.transcribe(
                audio, vad_filter=True, language="en",
                beam_size=self._beam_size,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            return self._filter(segments)

    def transcribe_nbest(self, audio: np.ndarray, n: int = 3) -> list[str]:
        """Return up to `n` DISTINCT candidate transcripts, best first, for n-best
        intent rescoring. The first is the primary decode (same as transcribe());
        the rest come from sampled passes (temperature > 0), which diverge only when
        the audio is ambiguous - on clear speech they collapse to one candidate, so
        this stays cheap. faster-whisper's high-level API returns a single hypothesis
        per call, so alternates are drawn from extra sampled decodes."""
        if audio is None or len(audio) == 0:
            return []
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        cands: list[str] = []

        def add(text: str) -> None:
            t = (text or "").strip()
            if t and t.lower() not in {c.lower() for c in cands}:
                cands.append(t)

        add(self.transcribe(audio))
        for temp in (0.4, 0.8):
            if len(cands) >= n:
                break
            try:
                with self._decode_lock:
                    segments, _info = self._model.transcribe(
                        audio, vad_filter=True, language="en",
                        temperature=temp, beam_size=1, best_of=max(2, n),
                        condition_on_previous_text=False,
                        without_timestamps=True)
                    add(self._filter(segments))
            except Exception:  # noqa: BLE001 - an alternate failing must not kill the path
                log.exception("n-best sampled decode failed (temp=%.1f)", temp)
        return cands[:n]
