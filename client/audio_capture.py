"""Push-to-talk microphone capture.

Keeps a sounddevice InputStream open continuously and gates recording on the PTT
key state, so the start of speech is never clipped (no per-utterance stream
startup latency). Audio is captured as float32 mono at 16 kHz, ready for Whisper.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import keyboard
import numpy as np
import sounddevice as sd

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
    def record_once(self, on_start=None, on_stop=None) -> np.ndarray:
        """Block until PTT is pressed, record until released, return the audio.

        on_start fires when the key goes down (recording begins) and on_stop when
        it is released - used to drive a 'listening' indicator in the UI.
        """
        self.open()
        keyboard.wait(self.ptt_key)
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
