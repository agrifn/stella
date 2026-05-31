"""Client configuration, loaded from config/settings.json.

Mirrors the server's typed-config approach: the rest of the client depends on a
small immutable object, not on raw dict access.
"""
from __future__ import annotations

import json
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
    execute_keys: bool = True        # actually press keys (False = dry-run / log only)
    hold_duration: float = 1.5       # seconds to hold a 'hold' key (e.g. self destruct)
    # CHAT mode: by default just type at the cursor and press Enter (the user
    # opens/focuses chat themselves). Set chat_open_key (e.g. "f12") to auto-open.
    chat_open_key: str = ""          # empty = don't open a chat box, just type
    chat_send_key: str = "enter"     # key that sends the message ("" = don't send)
    chat_open_delay: float = 0.25    # settle pause before typing (avoids dropped 1st char)
    # Overlay HUD
    overlay_corner: str = "top-left"  # top-left | top-right | bottom-left | bottom-right
    overlay_opacity: float = 0.85
    overlay_margin: int = 24


def load_client_config(settings_path: Path | None = None) -> ClientConfig:
    settings_path = settings_path or (CONFIG_DIR / "settings.json")
    data: dict = {}
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))

    client = data.get("client", {})
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
        execute_keys=bool(client.get("execute_keys", ClientConfig.execute_keys)),
        hold_duration=float(client.get("hold_duration", ClientConfig.hold_duration)),
        chat_open_key=client.get("chat_open_key", ClientConfig.chat_open_key),
        chat_send_key=client.get("chat_send_key", ClientConfig.chat_send_key),
        chat_open_delay=float(client.get("chat_open_delay", ClientConfig.chat_open_delay)),
        overlay_corner=client.get("overlay_corner", ClientConfig.overlay_corner),
        overlay_opacity=float(client.get("overlay_opacity", ClientConfig.overlay_opacity)),
        overlay_margin=int(client.get("overlay_margin", ClientConfig.overlay_margin)),
    )
