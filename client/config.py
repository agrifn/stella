"""Client configuration, loaded from config/settings.json.

Mirrors the server's typed-config approach: the rest of the client depends on a
small immutable object, not on raw dict access.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CLIENT_DIR.parent
CONFIG_DIR = REPO_ROOT / "config"


@dataclass(frozen=True)
class ClientConfig:
    # Use 127.0.0.1 (not 'localhost') for the local WSL2 backend: 'localhost'
    # resolves to IPv6 ::1 first, which WSL2's port-forward ignores, causing a
    # ~21s connection timeout per new connection.
    server_url: str = "http://127.0.0.1:8420"
    ptt_key: str = "right ctrl"
    mode_toggle_key: str = "ctrl+alt+m"   # avoid F-keys: many are SC bindings (F8=reset_power)
    default_mode: str = "COMMAND"
    whisper_model: str = "small"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "int8"
    samplerate: int = 16000
    input_device: int | None = None   # None = system default mic
    output_device: int | None = None  # None = system default speakers/headset
    # Pre-STT gate: drop utterances shorter/quieter than these (accidental PTT taps
    # and silence) before Whisper. If a quiet mic is being gated out, lower
    # min_speech_rms; set it to 0 to disable the loudness gate entirely.
    min_speech_seconds: float = 0.3
    min_speech_rms: float = 0.0012
    # Hands-free (wake-word) capture endpointing: once speech is heard, stop after this
    # much trailing silence; give up if no speech within the grace window; hard cap at
    # max. Tighter silence = snappier voice commands (less dead air), but too low clips
    # slow or deliberate speakers mid-command - if commands get cut off, go back to the
    # conservative 0.45. The capture's speech-detect gate reuses min_speech_rms (one
    # "what counts as speech" value).
    wake_capture_silence: float = 0.32
    wake_capture_grace: float = 2.5
    wake_capture_max: float = 6.0
    # STT confidence gates (faster-whisper): drop segments noisier / less confident
    # than these so silence/noise never becomes a command.
    stt_no_speech_prob: float = 0.6
    stt_avg_logprob: float = -1.3
    # Primary decode beam width. 1 = greedy: 2 to 3x faster and accurate enough for
    # the small closed command vocabulary (n-best + the classifier reject threshold
    # backstop the rare slip). Raise to 5 for Whisper's robust beam-search default.
    stt_beam_size: int = 1
    # Speculative STT during the PTT hold: pilots hold the key 200 to 400ms past
    # their last word. Once spec_silence_s of trailing silence accumulates in the
    # live buffer, transcription starts in the background so the text is ready at
    # release; anything spoken after that snapshot discards it (see
    # client/endpointing.py). False = byte-identical to the classic flow. Needs
    # min_speech_rms > 0 (silence detection reuses the loudness gate).
    speculative_stt: bool = True
    spec_silence_s: float = 0.35
    # In-process intent classification: classify in the client (no HTTP hop on the
    # action path). False = classic behavior, POST /command to the server. The
    # classifier model/thresholds below mirror the server defaults and are read from
    # the shared top-level "classifier" section of settings.json, so tuning one place
    # changes both paths identically.
    local_intent: bool = True
    classifier_model: str = "minishlab/potion-base-32M"
    classifier_reject: float = 0.45    # below this cosine -> chat (not a command)
    classifier_clarify: float = 0.55   # command below this -> ask "Say again?"
    # ASR n-best rescoring: when the top transcript is not already a confident command,
    # transcribe a couple of sampled alternates and let the server pick the most
    # confident command among them. Only runs on the uncertain path (no added latency
    # when the first transcript is already a clean command).
    nbest_enabled: bool = True
    nbest_count: int = 3                 # total candidates (top-1 + alternates)
    # Follow-up mode: after a command fires, listen briefly for the next one with no
    # PTT press / wake word. Lets you chain commands. The window is how long you have
    # to START speaking; it re-arms after each command and closes on silence.
    follow_up_enabled: bool = True
    follow_up_window: float = 1.2      # seconds to wait for a follow-up command before the window closes
    execute_keys: bool = True        # actually press keys (False = dry-run / log only)
    hold_duration: float = 1.5       # seconds to hold a 'hold' key (e.g. self destruct)
    # CHAT mode: by default just type at the cursor and press Enter (the user
    # opens/focuses chat themselves). Set chat_open_key (e.g. "f12") to auto-open.
    chat_open_key: str = ""          # empty = don't open a chat box, just type
    chat_send_key: str = "enter"     # key that sends the message ("" = don't send)
    chat_open_delay: float = 0.25    # settle pause before typing (avoids dropped 1st char)
    # Wake / sleep: when asleep STELLA ignores PTT until woken (so it's not "fully
    # running"). Wake via the hotkey (toggles sleep), the tray menu, or - once
    # configured - a spoken wake word. It auto-sleeps after inactivity.
    # Wake/sleep is OPT-IN: by default STELLA boots awake and stays awake (works
    # out of the box). Enable start_asleep / auto_sleep to get the dormant behavior.
    start_asleep: bool = False           # True = launch dormant (must wake with wake_key)
    wake_key: str = "ctrl+alt+s"         # global hotkey that toggles wake/sleep
    auto_sleep_enabled: bool = False     # True = sleep after auto_sleep_seconds idle
    auto_sleep_seconds: int = 300        # when enabled, sleep after this many idle seconds
    wake_word_enabled: bool = False      # spoken wake word (needs a model; see wake_word_model)
    wake_word_model: str = ""            # path to the openWakeWord 'stella' model (.onnx/.tflite)
    wake_word_threshold: float = 0.5     # detection confidence 0-1
    # Optional API token sent as 'Authorization: Bearer <token>' to the server. Must
    # match the server's STELLA_API_TOKEN. Empty = no auth header (the default).
    api_token: str = ""
    # Overlay HUD
    overlay_corner: str = "bottom-right"  # top-left | top-right | bottom-left | bottom-right
    overlay_opacity: float = 0.85
    overlay_margin: int = 24
    overlay_scale: float = 0.8         # HUD size multiplier (smaller < 1.0 < larger)
    overlay_width: int = 300           # base HUD width in px (before scale)
    # Steam-style auto-hide: fade the HUD in on activity, fade it out after this many
    # idle seconds (it stays up while listening or while a warning is shown).
    overlay_auto_hide: bool = True
    overlay_hide_seconds: float = 4.0


def load_client_config(settings_path: Path | None = None) -> ClientConfig:
    settings_path = settings_path or (CONFIG_DIR / "settings.json")
    data: dict = {}
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))

    client = data.get("client", {})
    # The "classifier" section is shared with the server (same keys), so the local
    # and HTTP intent paths always run the same model and thresholds.
    clf = data.get("classifier", {})
    return ClientConfig(
        server_url=data.get("server_url", ClientConfig.server_url),
        ptt_key=client.get("ptt_key", ClientConfig.ptt_key),
        mode_toggle_key=client.get("mode_toggle_key", ClientConfig.mode_toggle_key),
        default_mode=client.get("default_mode", ClientConfig.default_mode),
        whisper_model=client.get("whisper_model", ClientConfig.whisper_model),
        whisper_device=client.get("whisper_device", ClientConfig.whisper_device),
        whisper_compute_type=client.get("whisper_compute_type", ClientConfig.whisper_compute_type),
        samplerate=int(client.get("samplerate", ClientConfig.samplerate)),
        input_device=client.get("input_device", ClientConfig.input_device),
        output_device=client.get("output_device", ClientConfig.output_device),
        min_speech_seconds=float(client.get("min_speech_seconds", ClientConfig.min_speech_seconds)),
        min_speech_rms=float(client.get("min_speech_rms", ClientConfig.min_speech_rms)),
        wake_capture_silence=float(client.get("wake_capture_silence", ClientConfig.wake_capture_silence)),
        wake_capture_grace=float(client.get("wake_capture_grace", ClientConfig.wake_capture_grace)),
        wake_capture_max=float(client.get("wake_capture_max", ClientConfig.wake_capture_max)),
        stt_no_speech_prob=float(client.get("stt_no_speech_prob", ClientConfig.stt_no_speech_prob)),
        stt_avg_logprob=float(client.get("stt_avg_logprob", ClientConfig.stt_avg_logprob)),
        stt_beam_size=int(client.get("stt_beam_size", ClientConfig.stt_beam_size)),
        speculative_stt=bool(client.get("speculative_stt", ClientConfig.speculative_stt)),
        spec_silence_s=float(client.get("spec_silence_s", ClientConfig.spec_silence_s)),
        local_intent=bool(client.get("local_intent", ClientConfig.local_intent)),
        classifier_model=clf.get("model", ClientConfig.classifier_model),
        classifier_reject=float(clf.get("reject_threshold", ClientConfig.classifier_reject)),
        classifier_clarify=float(clf.get("clarify_threshold", ClientConfig.classifier_clarify)),
        nbest_enabled=bool(client.get("nbest_enabled", ClientConfig.nbest_enabled)),
        nbest_count=int(client.get("nbest_count", ClientConfig.nbest_count)),
        follow_up_enabled=bool(client.get("follow_up_enabled", ClientConfig.follow_up_enabled)),
        follow_up_window=float(client.get("follow_up_window", ClientConfig.follow_up_window)),
        execute_keys=bool(client.get("execute_keys", ClientConfig.execute_keys)),
        hold_duration=float(client.get("hold_duration", ClientConfig.hold_duration)),
        chat_open_key=client.get("chat_open_key", ClientConfig.chat_open_key),
        chat_send_key=client.get("chat_send_key", ClientConfig.chat_send_key),
        chat_open_delay=float(client.get("chat_open_delay", ClientConfig.chat_open_delay)),
        start_asleep=bool(client.get("start_asleep", ClientConfig.start_asleep)),
        wake_key=client.get("wake_key", ClientConfig.wake_key),
        auto_sleep_enabled=bool(client.get("auto_sleep_enabled", ClientConfig.auto_sleep_enabled)),
        auto_sleep_seconds=int(client.get("auto_sleep_seconds", ClientConfig.auto_sleep_seconds)),
        wake_word_enabled=bool(client.get("wake_word_enabled", ClientConfig.wake_word_enabled)),
        wake_word_model=client.get("wake_word_model", ClientConfig.wake_word_model),
        wake_word_threshold=float(client.get("wake_word_threshold", ClientConfig.wake_word_threshold)),
        api_token=os.environ.get("STELLA_API_TOKEN") or client.get("api_token", ClientConfig.api_token),
        overlay_corner=client.get("overlay_corner", ClientConfig.overlay_corner),
        overlay_opacity=float(client.get("overlay_opacity", ClientConfig.overlay_opacity)),
        overlay_margin=int(client.get("overlay_margin", ClientConfig.overlay_margin)),
        overlay_scale=float(client.get("overlay_scale", ClientConfig.overlay_scale)),
        overlay_width=int(client.get("overlay_width", ClientConfig.overlay_width)),
        overlay_auto_hide=bool(client.get("overlay_auto_hide", ClientConfig.overlay_auto_hide)),
        overlay_hide_seconds=float(client.get("overlay_hide_seconds", ClientConfig.overlay_hide_seconds)),
    )
