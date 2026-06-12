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
import threading
import time

import numpy as np

from .audio_capture import PTTRecorder
from .audio_player import AudioPlayer
from .chat_injector import ChatInjector
from .command_sender import CommandSender
from .config import ClientConfig
from .confirm import is_affirmative
from .keybind_executor import KeybindExecutor
from .mode_switch import detect_mode_switch
from .multicmd import split_commands
from .stt_handler import STTHandler

log = logging.getLogger("stella.engine")


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

        self.stt = STTHandler(cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute_type,
                              no_speech_prob=cfg.stt_no_speech_prob, avg_logprob=cfg.stt_avg_logprob,
                              beam_size=cfg.stt_beam_size)
        self.sender = CommandSender(cfg.server_url, cfg.api_token)
        self.player = AudioPlayer(cfg.output_device)
        self.executor = KeybindExecutor(hold_duration=cfg.hold_duration, enabled=do_exec)
        self.chat = ChatInjector(cfg.chat_open_key, cfg.chat_send_key,
                                 cfg.chat_open_delay, enabled=do_exec)
        self.recorder = PTTRecorder(cfg.ptt_key, cfg.samplerate, cfg.input_device)

        # Pre-STT gate thresholds (tunable so a quiet mic isn't silently dropped).
        self._min_dur = cfg.min_speech_seconds
        self._min_rms = cfg.min_speech_rms

        # ASR n-best rescoring + "say again?" recovery + follow-up chaining.
        self._nbest = cfg.nbest_enabled
        self._nbest_n = cfg.nbest_count
        self._follow_enabled = cfg.follow_up_enabled
        self._follow_window = cfg.follow_up_window
        self._follow_armed = False  # set after a command fires; the run loop captures next
        # Track TTS playback so follow-up capture never starts while an ack is playing
        # (else STELLA would hear its own voice). Incremented synchronously in the speak
        # helpers BEFORE the playback thread starts, to avoid a start-latency race.
        self._play_lock = threading.Lock()
        self._playing = 0

        # Wake/sleep: when asleep, PTT utterances are ignored until woken.
        self.active = not cfg.start_asleep
        self._auto_sleep = cfg.auto_sleep_seconds
        self._auto_sleep_enabled = cfg.auto_sleep_enabled
        self._wake_key = cfg.wake_key
        self._last_activity = time.time()

        # Optional spoken wake word: a tiny always-on model that taps the mic stream.
        # Saying "Stella" acts like a push-to-talk press - it fires this event and the
        # run loop captures the following command hands-free (no key). Set from the
        # audio-callback thread, so it only flips a flag (never blocks the audio path).
        self._wake_event = threading.Event()
        self._wake_lock = threading.Lock()
        self._wake_mute = 0  # reference count of active muters (capture + ack playback)
        self._wake_listener = None
        if cfg.wake_word_enabled and cfg.wake_word_model and os.path.exists(cfg.wake_word_model):
            try:
                from .wake_word import WakeWordListener
                self._wake_listener = WakeWordListener(
                    cfg.wake_word_model, cfg.wake_word_threshold, cfg.samplerate,
                    on_wake=self._on_wake_word)
                self._wake_listener.set_enabled(True)  # always listening (hands-free trigger)
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

    def set_mode(self, mode: str):
        if mode in ("CHAT", "COMMAND") and mode != self.mode:
            self.mode = mode
            self._emit("mode", mode=self.mode)

    # -- wake / sleep -----------------------------------------------------
    def wake(self):
        self._last_activity = time.time()
        if not self.active:
            self.active = True
            self._emit("wake_state", active=True)
            self._emit("status", text="awake")
            log.info("STELLA awake")

    def sleep(self):
        if self.active:
            self.active = False
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
        # "Stella" heard: behave like a PTT press. Wake if asleep, and signal the run
        # loop to capture the following command hands-free. Runs on the audio thread,
        # so it must only flip the flag - never block.
        self._last_activity = time.time()
        if not self.active:
            self.wake()
        self._wake_event.set()

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
            # Warm the server path (classifier + first request) so the first real
            # command isn't a cold round-trip.
            self._emit("status", text="warming classifier...")
            self.sender.send("ready", speak=False)
        except Exception as e:  # noqa: BLE001
            self._emit("status", text=f"server unreachable: {e}")
        # Warn if we'll try to send keys but aren't elevated (EAC/SC run elevated,
        # so a non-admin process can't inject input into them).
        if self.executor.enabled and not is_admin():
            self._emit("warn", text="NOT ELEVATED - keys may not reach the game (run as admin)")
        self._emit("mode", mode=self.mode)
        self._emit("wake_state", active=self.active)
        self._emit("status", text="ready" if self.active else f"asleep - {self._wake_key} to wake")

    # -- one utterance ----------------------------------------------------
    def process(self, audio):
        # Asleep: ignore PTT entirely (wake via hotkey/tray/wake-word first).
        if not self.active:
            return
        self._last_activity = time.time()
        t0 = time.time()
        dur = len(audio) / self.stt.samplerate
        rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
        # Gate accidental PTT taps and (near-)silence before STT. Thresholds are
        # configurable; min_speech_rms=0 disables the loudness gate for quiet mics.
        if dur < self._min_dur or (self._min_rms > 0 and rms < self._min_rms):
            log.info("skip utterance: %.2fs rms=%.4f (too short/quiet; "
                     "min %.2fs/%.4f - lower min_speech_rms if this eats real speech)",
                     dur, rms, self._min_dur, self._min_rms)
            return
        text = self.stt.transcribe(audio)
        t_stt = time.time() - t0
        self._emit("transcript", text=text)
        log.info("audio %.1fs | STT %.2fs (%s) -> %r", dur, t_stt, self.stt.device, text)
        if not text:
            return

        # Voice mode switch ("switch to chat" / "command mode") - handled before the
        # mode branch so it works from EITHER mode.
        target = detect_mode_switch(text)
        if target:
            if target != self.mode:
                self.set_mode(target)
                self._speak_async(f"{target.capitalize()} mode.", route="ack")
            else:
                self._speak_async(f"Already in {target.lower()} mode.", route="ack")
            return

        if self.mode == "CHAT":
            self.chat.inject(text)
            self._emit("chat_sent", text=text)
            return

        # COMMAND mode: one utterance may carry several commands ("lower shields and
        # raise engine power") and/or a repeat ("fire three flares"). Split it locally
        # and run each part through the (single-intent) server in turn - the classifier
        # and server stay simple; the orchestration lives here.
        segments = split_commands(text)
        if len(segments) > 1 or (segments and segments[0][1] > 1):
            log.info("multi-command: %r -> %s", text, segments)
        # n-best rescoring and "say again?" recovery use the raw audio, and only make
        # sense for a single, whole-utterance command - a clean multi-command split is
        # already unambiguous, so those parts go straight through.
        single = len(segments) == 1
        acted = False
        for seg_text, count in segments:
            if self._handle_segment(seg_text, count, t_stt, dur,
                                    audio=audio if single else None):
                acted = True

        # Follow-up mode: after a real command, arm a short hands-free listen so the
        # next command needs no PTT/wake. The run loop performs the capture (gated on
        # ack playback) and re-arms via process() if another command fires.
        if acted and self._follow_enabled and self.active and self.mode == "COMMAND":
            self._follow_armed = True

    @staticmethod
    def _is_command(res) -> bool:
        """True if the server resolved an actionable keybind/macro (not a chat reply)."""
        return bool(res and (res.keybind or res.sequence))

    def _handle_segment(self, text: str, count: int, t_stt: float, dur: float,
                        audio=None) -> bool:
        """Classify and act on one sub-command, repeating a normal command `count`
        times. Request intent ONLY (speak=False) so TTS synthesis does not sit in the
        action path; we voice the reply separately, after acting. Returns True if a
        real command actually executed (used to arm follow-up mode).

        When `audio` is provided (single, whole-utterance command), two recovery layers
        kick in if the first transcript is not already a confident command:
          - ASR n-best rescoring: transcribe sampled alternates and let the server pick
            the most confident command among them.
          - "Say again?": a still-borderline match asks the pilot to repeat once,
            rather than firing a guess."""
        t1 = time.time()
        try:
            res = self.sender.send(text, speak=False)
        except Exception as e:  # noqa: BLE001
            self._emit("error", text=f"server error: {e}")
            return False
        t_srv = time.time() - t1
        log.info("classify %.2fs -> intent=%s (%.2f) key=%s clarify=%s x%d",
                 t_srv, res.intent, res.confidence, res.keybind, res.clarify, count)

        # n-best rescoring: only on the uncertain path (top transcript was chat or a
        # borderline command), and only when we kept the audio. NOTE this re-runs STT
        # (sampled decodes) plus a second server call, so it adds latency exactly on the
        # uncertain utterances - timed and logged so that cost stays visible.
        if audio is not None and self._nbest and (not self._is_command(res) or res.clarify):
            t_nb = time.time()
            alts = [c for c in self.stt.transcribe_nbest(audio, self._nbest_n)
                    if c and c.lower() != text.lower()]
            if alts:
                try:
                    res2 = self.sender.send(text, speak=False, candidates=alts)
                except Exception as e:  # noqa: BLE001
                    res2 = None
                    log.warning("n-best rescoring failed: %s", e)
                # Adopt the rescored result if it is a command and an improvement
                # (the first pass was not a command, or the new one is confident).
                adopted = self._is_command(res2) and (not self._is_command(res) or not res2.clarify)
                log.info("n-best %.2fs: alternates=%r adopted=%s%s", time.time() - t_nb,
                         alts, adopted,
                         f" -> {res2.intent}({res2.confidence:.2f})" if adopted else "")
                if adopted:
                    res = res2
                    if res.chosen_text and res.chosen_text.lower() != text.lower():
                        self._emit("transcript", text=res.chosen_text)
            else:
                log.info("n-best %.2fs: no distinct alternates", time.time() - t_nb)

        # Still a borderline command: ask once instead of guessing.
        if audio is not None and self._is_command(res) and res.clarify:
            res = self._say_again()
            if res is None:
                return False

        return self._act_on(res, count, t_stt, dur)

    def _act_on(self, res, count: int, t_stt: float, dur: float) -> bool:
        """Carry out a resolved result: chat reply, confirm-gated command, or a normal
        command repeated `count` times. Returns True only if a command executed."""
        self._emit("response", intent=res.intent, keybind=res.keybind,
                   confirm=res.confirm_required, text=res.response_text)

        # No action (chat intent, or a mis-split part that matched no command): speak
        # via the chat voice. An over-split fails safe here - no keys are sent.
        if not self._is_command(res):
            self._speak_async(res.response_text, route="chat")
            return False

        # Dangerous command: speak the prompt, then wait for a spoken yes/no. A
        # dangerous command is never auto-repeated - it fires once after confirmation.
        if res.confirm_required:
            self._speak_blocking(res.response_text, route="ack")
            self._emit("await_confirm", intent=res.intent)
            # Bounded wait: if the pilot never presses PTT to answer, auto-cancel
            # instead of blocking the engine loop forever.
            conf_audio = self.recorder.record_once(timeout=6.0)
            conf_text = self.stt.transcribe(conf_audio) if len(conf_audio) else ""
            self._emit("transcript", text=conf_text)
            if not is_affirmative(conf_text):
                self._emit("cancelled", intent=res.intent)
                self._speak_async("Cancelled.", route="ack")
                return False
            self._emit("confirmed", intent=res.intent)
            self._execute(res)
            self._speak_async("Confirmed.", route="ack")
            return True

        # Normal command: ACT IMMEDIATELY (count times), then voice the ack via Piper.
        self._execute(res, count=count)
        self._emit("status", text=f"STT {t_stt:.1f}s | {dur:.0f}s audio")
        self._speak_async(res.response_text, route="ack")
        return True

    def _say_again(self):
        """Borderline command recovery: ask the pilot to repeat once, capture the
        answer hands-free, and rescore it. Returns a confident CommandResult to act on,
        or None to abort (nothing caught / still unclear)."""
        self._speak_blocking("Say again?", route="ack")
        self._emit("status", text="say again?")
        self._mute_wake(True)
        try:
            audio = self.recorder.record_hands_free(
                silence_s=self.cfg.wake_capture_silence,
                start_grace_s=self.cfg.wake_capture_grace,
                max_s=self.cfg.wake_capture_max,
                rms_gate=self.cfg.min_speech_rms,
            )
        finally:
            self._mute_wake(False)
        text = self.stt.transcribe(audio) if audio is not None and len(audio) else ""
        self._emit("transcript", text=text)
        if not text:
            self._speak_async("Didn't catch that.", route="ack")
            return None
        # The retry is already a fresh, deliberate utterance, so do NOT run n-best
        # again here - that bounds one stubborn command to a single recovery round
        # (n-best -> say-again -> single re-classify) instead of stacking STT passes.
        try:
            res = self.sender.send(text, speak=False)
        except Exception as e:  # noqa: BLE001
            self._emit("error", text=f"server error: {e}")
            return None
        if self._is_command(res) and not res.clarify:
            return res
        self._speak_async("Didn't catch that.", route="ack")
        return None

    def _mute_wake(self, on: bool) -> None:
        """Pause/resume the wake-word listener so STELLA does not hear its own ack (or
        the command it is capturing) and re-trigger. Reference-COUNTED + locked: there
        are multiple concurrent muters (the capture step and one or more ack-playback
        threads), so the listener is only re-enabled once every muter has released, and
        a self-trigger is discarded at that point. A plain boolean here races - a
        finishing ack could re-enable the listener while another is still playing."""
        if not self._wake_listener:
            return
        with self._wake_lock:
            if on:
                self._wake_mute += 1
                self._wake_listener.set_enabled(False)
            else:
                self._wake_mute = max(0, self._wake_mute - 1)
                if self._wake_mute == 0:
                    self._wake_event.clear()  # drop any wake fired while we were muted
                    self._wake_listener.set_enabled(True)

    def _set_playing(self, delta: int) -> None:
        """Track in-flight TTS playback so follow-up capture waits for it to finish."""
        with self._play_lock:
            self._playing = max(0, self._playing + delta)

    def _speak_async(self, text: str, route: str = "chat"):
        """Fetch TTS and play it without blocking the loop (voice trails the action).
        route selects the server's TTS engine for acks vs chat (both default to Piper)."""
        if not text:
            return
        self._set_playing(1)  # mark BEFORE the thread starts (avoid follow-up race)
        def run():
            try:
                audio = self.sender.speak(text, route)
                if not audio:
                    return
                self._mute_wake(True)  # don't let our own voice trip the wake word
                try:
                    self.player.play_b64(audio, blocking=True)  # block in THIS thread only
                finally:
                    self._mute_wake(False)
            finally:
                self._set_playing(-1)
        threading.Thread(target=run, daemon=True).start()

    def _speak_blocking(self, text: str, route: str = "chat"):
        if not text:
            return
        self._set_playing(1)
        try:
            audio = self.sender.speak(text, route)
            if not audio:
                return
            self._mute_wake(True)
            try:
                self.player.play_b64(audio, blocking=True)
            finally:
                self._mute_wake(False)
        finally:
            self._set_playing(-1)

    def _execute(self, res, count: int = 1):
        count = max(1, count)
        try:
            for i in range(count):
                if res.sequence:
                    self.executor.execute_sequence(res.sequence)
                else:
                    self.executor.execute(res.keybind, hold=res.hold,
                                          duration=res.hold_duration)
                if i + 1 < count:
                    time.sleep(0.12)  # brief gap so rapid repeats register as separate presses
            label = f"macro({len(res.sequence)} steps)" if res.sequence else res.keybind
            if count > 1:
                label = f"{label} x{count}"
            self._emit("executed", intent=res.intent, keybind=label)
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
                audio = None
                if self.recorder.ptt_pressed():
                    # Push-to-talk: record while the key is held.
                    audio = self.recorder.capture_ptt(
                        on_start=lambda: self._emit("listening", on=True),
                        on_stop=lambda: self._emit("listening", on=False),
                    )
                elif self._wake_event.is_set():
                    # Wake word fired: capture the command hands-free (no key). Mute the
                    # listener during capture so the command speech can't re-trigger it
                    # (ref-counted, shared with ack playback - see _mute_wake).
                    self._wake_event.clear()
                    self._mute_wake(True)
                    self._emit("listening", on=True)
                    try:
                        audio = self.recorder.record_hands_free(
                            silence_s=self.cfg.wake_capture_silence,
                            start_grace_s=self.cfg.wake_capture_grace,
                            max_s=self.cfg.wake_capture_max,
                            rms_gate=self.cfg.min_speech_rms,
                        )
                    finally:
                        self._emit("listening", on=False)
                        self._mute_wake(False)
                elif self._follow_armed and self.active:
                    # Follow-up window: listen for the next command with no PTT/wake.
                    # Wait for any ack still playing so we don't capture our own voice;
                    # re-arm and re-check next tick rather than blocking here.
                    if self._playing > 0:
                        time.sleep(0.03)
                        continue
                    self._follow_armed = False  # consumed; process() re-arms on a command
                    self._mute_wake(True)
                    self._emit("listening", on=True)
                    self._emit("status", text="listening (follow-up)")
                    try:
                        audio = self.recorder.record_hands_free(
                            silence_s=self.cfg.wake_capture_silence,
                            start_grace_s=self._follow_window,
                            max_s=self.cfg.wake_capture_max,
                            rms_gate=self.cfg.min_speech_rms,
                        )
                    finally:
                        self._emit("listening", on=False)
                        self._mute_wake(False)
                else:
                    time.sleep(0.03)  # idle poll for PTT / wake word
                    continue
                if should_stop():
                    break
                if audio is not None and len(audio) > 0:
                    self.process(audio)
        finally:
            self.recorder.close()
