"""Non-blocking playback of TTS WAV audio returned by the server.

Decodes WAV bytes with the stdlib `wave` module and plays via sounddevice. An
explicit output device can be set (some machines default to a silent/HDMI sink),
which is the usual fix for "STELLA responds but I hear nothing".
"""
from __future__ import annotations

import base64
import io
import logging
import wave
from typing import Optional

import numpy as np
import sounddevice as sd

log = logging.getLogger("stella.player")


class AudioPlayer:
    def __init__(self, output_device: Optional[int] = None):
        self.output_device = output_device

    def play_wav_bytes(self, wav_bytes: bytes, blocking: bool = False) -> None:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            rate = w.getframerate()
            sampwidth = w.getsampwidth()
            frames = w.readframes(w.getnframes())
        if sampwidth != 2:
            log.warning("unexpected sample width %d; expected 16-bit", sampwidth)
        audio = np.frombuffer(frames, dtype=np.int16)
        sd.play(audio, rate, device=self.output_device)
        if blocking:
            sd.wait()

    def play_b64(self, audio_b64: str, blocking: bool = False) -> None:
        if not audio_b64:
            return
        self.play_wav_bytes(base64.b64decode(audio_b64), blocking=blocking)

    def wait(self) -> None:
        sd.wait()
