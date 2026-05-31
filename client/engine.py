"""StellaEngine - the GUI-agnostic voice loop shared by the console harness and
the overlay app.

It owns the pieces (STT, server client, audio, keybind executor, chat injector,
PTT recorder) and runs the per-utterance flow. It reports progress by calling an
`on_event(name, data)` callback so any front-end (console printer or Qt overlay)
can render state without the engine knowing about it.

Events: status, mode, listening, transcript, chat_sent, response, await_confirm,
confirmed, cancelled, executed, error.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import threading
import time

import numpy as np

from .audio_capture import PTTRecorder
from .audio_player import AudioPlayer
from .chat_injector import ChatInjector
from .command_sender import CommandSender
from .config import ClientConfig
from .keybind_executor import KeybindExecutor
from .stt_handler import STTHandler

log = logging.getLogger("stella.engine")

_AFFIRMATIVE = ["yes", "yeah", "yep", "confirm", "confirmed", "affirmative",
                "do it", "go", "execute", "proceed", "engage"]
# Word-boundary match so "go" matches only the standalone word, NOT "good", "ago",
# or "let it go" - critical because this gates eject / self destruct.
_AFFIRMATIVE_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in _AFFIRMATIVE) + r")\b")


def is_affirmative(text: str) -> bool:
    return bool(_AFFIRMATIVE_RE.search((text or "").lower()))


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


class StellaEngine:
    def __init__(self, cfg: ClientConfig, on_event=None, execute_keys: bool | None = None):
        self.cfg = cfg
        self._on_event = on_event or (lambda name, data: None)
        self.mode = cfg.default_mode.upper()
        do_exec = cfg.execute_keys if execute_keys is None else execute_keys

        self.stt = STTHandler(cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute_type)
        self.sender = CommandSender(cfg.server_url)
        self.player = AudioPlayer(cfg.output_device)
        self.executor = KeybindExecutor(hold_duration=cfg.hold_duration, enabled=do_exec)
        self.chat = ChatInjector(cfg.chat_open_key, cfg.chat_send_key,
                                 cfg.chat_open_delay, enabled=do_exec)
        self.recorder = PTTRecorder(cfg.ptt_key, cfg.samplerate, cfg.input_device)

        # Wake/sleep: when asleep, PTT utterances are ignored until woken.
        self.active = not cfg.start_asleep
        self._auto_sleep = cfg.auto_sleep_seconds
        self._auto_sleep_enabled = cfg.auto_sleep_enabled
        self._wake_key = cfg.wake_key
        self._last_activity = time.time()

        # Optional spoken wake word: a tiny model that taps the mic stream and
        # wakes STELLA. Only built if enabled and a model file is present.
        self._wake_listener = None
        if cfg.wake_word_enabled and cfg.wake_word_model and os.path.exists(cfg.wake_word_model):
            try:
                from .wake_word import WakeWordListener
                self._wake_listener = WakeWordListener(
                    cfg.wake_word_model, cfg.wake_word_threshold, cfg.samplerate,
                    on_wake=self._on_wake_word)
                self._wake_listener.set_enabled(not self.active)  # listen only while asleep
                self.recorder.set_monitor(self._wake_listener.feed)
            except Exception:  # noqa: BLE001
                log.exception("wake-word listener init failed; continuing without it")

    # -- events -----------------------------------------------------------
    def _emit(self, name: str, **data):
        try:
            self._on_event(name, data)
        except Exception:  # noqa: BLE001 - never let a UI callback break the loop
            log.exception("on_event handler raised")

    # -- mode -------------------------------------------------------------
    def toggle_mode(self):
        self.mode = "CHAT" if self.mode == "COMMAND" else "COMMAND"
        self._emit("mode", mode=self.mode)

    # -- wake / sleep -----------------------------------------------------
    def wake(self):
        self._last_activity = time.time()
        if not self.active:
            self.active = True
            if self._wake_listener:
                self._wake_listener.set_enabled(False)  # no need to listen for the word while awake
            self._emit("wake_state", active=True)
            self._emit("status", text="awake")
            log.info("STELLA awake")

    def sleep(self):
        if self.active:
            self.active = False
            if self._wake_listener:
                self._wake_listener.set_enabled(True)  # resume listening for the wake word
            self._emit("wake_state", active=False)
            self._emit("status", text=f"asleep - {self._wake_key} to wake")
            log.info("STELLA asleep")

    def toggle_wake(self):
        self.sleep() if self.active else self.wake()

    def set_auto_sleep(self, on: bool):
        """Enable/disable the idle auto-sleep watchdog at runtime (tray toggle)."""
        self._auto_sleep_enabled = bool(on)
        self._last_activity = time.time()  # don't let re-enabling sleep us instantly
        self._emit("status", text=f"auto-sleep {'on' if on else 'off'}")
        log.info("auto-sleep %s", "on" if on else "off")

    def _on_wake_word(self):
        if not self.active:
            log.info("wake word -> waking")
            self.wake()

    def _auto_sleep_loop(self, should_stop):
        """Background watchdog: return to sleep after a stretch of inactivity."""
        while not should_stop():
            time.sleep(5)
            if (self.active and self._auto_sleep_enabled and self._auto_sleep > 0
                    and time.time() - self._last_activity > self._auto_sleep):
                log.info("auto-sleep after %ds idle", self._auto_sleep)
                self.sleep()

    def warm(self):
        self._emit("status", text="warming speech model...")
        self.stt.warm()
        try:
            self._emit("status", text=f"server: {self.sender.health().get('status')}")
            # Warm the LLM too so the first real command isn't a cold model load.
            self._emit("status", text="warming language model...")
            self.sender.send("ready", speak=False)
        except Exception as e:  # noqa: BLE001
            self._emit("status", text=f"server unreachable: {e}")
        # Warn if we'll try to send keys but aren't elevated (EAC/SC run elevated,
        # so a non-admin process can't inject input into them).
        if self.executor.enabled and not is_admin():
            self._emit("status", text="WARNING: not admin - keys may not reach Star Citizen")
        self._emit("mode", mode=self.mode)
        self._emit("wake_state", active=self.active)
        self._emit("status", text="ready" if self.active else f"asleep - {self._wake_key} to wake")

    # -- one utterance ----------------------------------------------------
    # Gate out accidental PTT taps and (near-)silence before STT, so Whisper
    # never gets a chance to hallucinate a phantom command from noise.
    _MIN_DUR_S = 0.35
    _MIN_RMS = 0.006

    def process(self, audio):
        # Asleep: ignore PTT entirely (wake via hotkey/tray/wake-word first).
        if not self.active:
            return
        self._last_activity = time.time()
        t0 = time.time()
        dur = len(audio) / self.stt.samplerate
        rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
        if dur < self._MIN_DUR_S or rms < self._MIN_RMS:
            log.info("skip utterance: %.2fs rms=%.4f (too short/quiet)", dur, rms)
            return
        text = self.stt.transcribe(audio)
        t_stt = time.time() - t0
        self._emit("transcript", text=text)
        log.info("audio %.1fs | STT %.2fs (%s) -> %r", dur, t_stt, self.stt.device, text)
        if not text:
            return

        if self.mode == "CHAT":
            self.chat.inject(text)
            self._emit("chat_sent", text=text)
            return

        # COMMAND mode. Request intent ONLY (speak=False) so TTS synthesis does
        # not sit in the action path; we voice the reply separately, after acting.
        t1 = time.time()
        try:
            res = self.sender.send(text, speak=False)
        except Exception as e:  # noqa: BLE001
            self._emit("error", text=f"server error: {e}")
            return
        t_srv = time.time() - t1
        log.info("LLM %.2fs -> intent=%s key=%s", t_srv, res.intent, res.keybind)

        self._emit("response", intent=res.intent, keybind=res.keybind,
                   confirm=res.confirm_required, text=res.response_text)

        # No action (chat intent): just speak the reply in the background.
        if not res.keybind and not res.sequence:
            self._speak_async(res.response_text)
            return

        # Dangerous command: speak the prompt, then wait for a spoken yes/no.
        if res.confirm_required:
            self._speak_blocking(res.response_text)
            self._emit("await_confirm", intent=res.intent)
            conf_audio = self.recorder.record_once()
            conf_text = self.stt.transcribe(conf_audio) if len(conf_audio) else ""
            self._emit("transcript", text=conf_text)
            if not is_affirmative(conf_text):
                self._emit("cancelled", intent=res.intent)
                self._speak_async("Cancelled.")
                return
            self._emit("confirmed", intent=res.intent)
            self._execute(res)
            self._speak_async("Confirmed.")
            return

        # Normal command: ACT IMMEDIATELY, then voice the reply in the background.
        self._execute(res)
        self._emit("status", text=f"STT {t_stt:.1f}s | LLM {t_srv:.1f}s | {dur:.0f}s audio")
        self._speak_async(res.response_text)

    def _speak_async(self, text: str):
        """Fetch TTS and play it without blocking the loop (voice trails the action)."""
        if not text:
            return
        def run():
            audio = self.sender.speak(text)
            if audio:
                self.player.play_b64(audio, blocking=False)
        threading.Thread(target=run, daemon=True).start()

    def _speak_blocking(self, text: str):
        if not text:
            return
        audio = self.sender.speak(text)
        if audio:
            self.player.play_b64(audio, blocking=True)

    def _execute(self, res):
        try:
            if res.sequence:
                self.executor.execute_sequence(res.sequence)
                self._emit("executed", intent=res.intent, keybind=f"macro({len(res.sequence)} steps)")
            else:
                self.executor.execute(res.keybind, hold=res.hold)
                self._emit("executed", intent=res.intent, keybind=res.keybind)
        except Exception as e:  # noqa: BLE001
            self._emit("error", text=f"exec error: {e}")

    # -- main loop --------------------------------------------------------
    def run(self, should_stop):
        self.recorder.open()
        if self._auto_sleep > 0:
            threading.Thread(target=self._auto_sleep_loop, args=(should_stop,),
                             daemon=True).start()
        try:
            while not should_stop():
                audio = self.recorder.record_once(
                    on_start=lambda: self._emit("listening", on=True),
                    on_stop=lambda: self._emit("listening", on=False),
                )
                if should_stop():
                    break
                if len(audio) > 0:
                    self.process(audio)
        finally:
            self.recorder.close()
