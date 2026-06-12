"""Push-to-talk microphone capture.

Keeps a sounddevice InputStream open continuously and gates recording on the PTT
key state, so the start of speech is never clipped (no per-utterance stream
startup latency). Audio is captured as float32 mono at 16 kHz, ready for Whisper.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import keyboard
import numpy as np
import sounddevice as sd

from .endpointing import trailing_silence_s

log = logging.getLogger("stella.audio")


class PTTRecorder:
    def __init__(
        self,
        ptt_key: str = "scroll lock",
        samplerate: int = 16000,
        input_device: Optional[int] = None,
    ):
        self.ptt_key = ptt_key
        self.samplerate = samplerate
        self.input_device = input_device
        self._frames: list[np.ndarray] = []
        self._recording = threading.Event()
        self._stream: Optional[sd.InputStream] = None
        self._monitor: Optional[Callable] = None  # always-on tap (e.g. wake-word listener)

    def set_monitor(self, fn: Optional[Callable]) -> None:
        """Register a callback fed EVERY capture block (regardless of PTT state),
        so a wake-word listener can share this single mic stream."""
        self._monitor = fn

    # -- stream lifecycle -------------------------------------------------
    def _callback(self, indata, frames, time_info, status):  # noqa: ANN001
        if status:
            log.debug("audio status: %s", status)
        if self._monitor is not None:
            try:
                self._monitor(indata)
            except Exception:  # noqa: BLE001 - a monitor error must not break capture
                log.exception("audio monitor error")
        if self._recording.is_set():
            self._frames.append(indata.copy())

    def open(self) -> None:
        if self._stream is None:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="float32",
                device=self.input_device,
                callback=self._callback,
            )
            self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # -- capture ----------------------------------------------------------
    def record_once(self, on_start=None, on_stop=None,
                    timeout: Optional[float] = None) -> np.ndarray:
        """Block until PTT is pressed, record until released, return the audio.

        on_start fires when the key goes down (recording begins) and on_stop when
        it is released - used to drive a 'listening' indicator in the UI. If timeout
        is given and PTT is not pressed within that many seconds, return an empty
        array (used by the confirmation capture so an unanswered prompt auto-cancels).
        """
        self.open()
        if timeout is None:
            keyboard.wait(self.ptt_key)
        else:
            deadline = time.monotonic() + timeout
            while not keyboard.is_pressed(self.ptt_key):
                if time.monotonic() >= deadline:
                    return np.zeros(0, dtype=np.float32)
                sd.sleep(20)
        self._frames = []
        self._recording.set()
        if on_start:
            on_start()
        while keyboard.is_pressed(self.ptt_key):
            sd.sleep(20)
        self._recording.clear()
        if on_stop:
            on_stop()
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._frames, axis=0).reshape(-1)

    def ptt_pressed(self) -> bool:
        return keyboard.is_pressed(self.ptt_key)

    def capture_ptt(self, on_start=None, on_stop=None, on_silence=None,
                    spec_silence_s: float = 0.35, min_speech_s: float = 0.0,
                    rms_gate: float = 0.0) -> np.ndarray:
        """Record while PTT is held (assumes it's already down). Returns the audio.
        Same capture as record_once but without the initial wait-for-press, so the
        engine loop can poll for either PTT or the wake word and act on whichever.

        on_silence (optional) fires AT MOST ONCE per hold, with a copy of the
        buffer so far, when the pilot has stopped talking but still holds the key:
        the buffer ends in >= spec_silence_s of trailing silence and the spoken
        part is at least min_speech_s long. The engine uses it to start a
        speculative transcription early; whether that snapshot is still valid at
        release is the engine's call (speculation_valid), so speech resuming after
        the snapshot needs no extra state here. Needs rms_gate > 0 to detect
        silence (a disabled loudness gate also disables speculation)."""
        self.open()
        self._frames = []
        self._recording.set()
        if on_start:
            on_start()
        fired = False
        while keyboard.is_pressed(self.ptt_key):
            sd.sleep(20)
            if on_silence is None or fired or not self._frames:
                continue
            # Snapshot the frame list (the audio callback appends concurrently).
            snap = np.concatenate(list(self._frames), axis=0).reshape(-1)
            dur = len(snap) / self.samplerate
            trail = trailing_silence_s(snap, self.samplerate, rms_gate)
            if trail >= spec_silence_s and (dur - trail) >= max(min_speech_s, 1e-9):
                fired = True
                try:
                    on_silence(snap)
                except Exception:  # noqa: BLE001 - speculation must not break capture
                    log.exception("on_silence callback failed")
        self._recording.clear()
        if on_stop:
            on_stop()
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._frames, axis=0).reshape(-1)

    def record_hands_free(self, on_start=None, on_stop=None, max_s: float = 6.0,
                          silence_s: float = 0.8, start_grace_s: float = 2.5,
                          rms_gate: float = 0.008) -> np.ndarray:
        """Capture one utterance WITHOUT push-to-talk (used after a wake word).

        Starts recording immediately, then stops after `silence_s` of trailing
        silence once speech has been heard, or at `max_s`. If no speech arrives
        within `start_grace_s` (a bare wake word, or a false trigger in quiet),
        returns empty so nothing is processed.
        """
        self.open()
        self._frames = []
        self._recording.set()
        if on_start:
            on_start()
        t0 = time.monotonic()
        last_voice = t0
        speech = False
        try:
            while True:
                sd.sleep(50)
                now = time.monotonic()
                tail = self._frames[-8:]
                if tail:
                    a = np.concatenate(tail, axis=0).reshape(-1)
                    rms = float(np.sqrt(np.mean(np.square(a)))) if len(a) else 0.0
                else:
                    rms = 0.0
                if rms >= rms_gate:
                    speech = True
                    last_voice = now
                if now - t0 >= max_s:
                    break
                if not speech and now - t0 >= start_grace_s:
                    break
                if speech and now - last_voice >= silence_s:
                    break
        finally:
            self._recording.clear()
            if on_stop:
                on_stop()
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._frames, axis=0).reshape(-1)

    def loop(self, on_utterance: Callable[[np.ndarray], None]) -> None:
        """Continuously capture PTT utterances and hand each to on_utterance."""
        self.open()
        try:
            while True:
                audio = self.record_once()
                if len(audio) > 0:
                    on_utterance(audio)
        finally:
            self.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
