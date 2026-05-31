"""Console harness for the STELLA client (no overlay).

Live mode drives the same StellaEngine the overlay app uses, printing events to the
console instead of a HUD. File mode transcribes WAVs through the server to test
the STT -> server -> TTS chain without a microphone.

    python -m client.test_loop                 # live, presses keys
    python -m client.test_loop --dry-run        # live, logs keys only
    python -m client.test_loop --file [a.wav...] # headless transcription test
"""
from __future__ import annotations

import argparse
import logging
import time
import wave
from pathlib import Path

import numpy as np

from .audio_player import AudioPlayer
from .command_sender import CommandSender
from .config import load_client_config
from .stt_handler import STTHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("stella.client")


def _load_wav_16k_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        rate, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f"{path}: expected 16-bit WAV")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        dst = int(round(len(audio) * 16000 / rate))
        audio = np.interp(np.linspace(0, len(audio), dst, endpoint=False),
                          np.arange(len(audio)), audio).astype(np.float32)
    return audio


def run_file_mode(paths: list[Path], play: bool) -> None:
    cfg = load_client_config()
    stt = STTHandler(cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute_type)
    sender = CommandSender(cfg.server_url)
    player = AudioPlayer(cfg.output_device)
    log.info("server health: %s", sender.health())
    for p in paths:
        audio = _load_wav_16k_mono(p)
        t = time.time(); text = stt.transcribe(audio); t_stt = time.time() - t
        t = time.time(); res = sender.send(text); t_srv = time.time() - t
        print(f"\n{p.name}\n  STT [{t_stt:5.2f}s] -> {text!r}\n"
              f"  CMD [{t_srv:5.2f}s] -> intent={res.intent} key={res.keybind} "
              f"confirm={res.confirm_required}\n  SAY {res.response_text!r}")
        if play and res.audio_b64:
            player.play_b64(res.audio_b64, blocking=True)


def run_live_mode(execute_keys=None) -> None:
    import keyboard
    from .engine import StellaEngine

    cfg = load_client_config()

    def on_event(name: str, data: dict):
        if name == "status":
            print(f"  [status] {data.get('text')}")
        elif name == "mode":
            print(f"\n>>> MODE: {data.get('mode')}")
        elif name == "listening":
            print("  [listening...]" if data.get("on") else "  [processing...]")
        elif name == "transcript":
            print(f"  heard: {data.get('text')!r}")
        elif name == "response":
            print(f"  [COMMAND] intent={data.get('intent')} key={data.get('keybind')}  "
                  f"STELLA: {data.get('text')!r}")
        elif name == "chat_sent":
            print(f"  [CHAT] sent: {data.get('text')!r}")
        elif name == "await_confirm":
            print(f"  >>> CONFIRM '{data.get('intent')}'? hold PTT and say YES")
        elif name in ("confirmed", "cancelled"):
            print(f"  [{name}] {data.get('intent')}")
        elif name == "executed":
            print(f"  [executed] {data.get('keybind')}")
        elif name == "error":
            print(f"  [error] {data.get('text')}")

    engine = StellaEngine(cfg, on_event=on_event, execute_keys=execute_keys)
    keyboard.add_hotkey(cfg.mode_toggle_key, engine.toggle_mode)
    print("=" * 60)
    print(f"STELLA console. PTT(hold)={cfg.ptt_key}  mode-toggle={cfg.mode_toggle_key}  "
          f"exec={'DRY-RUN' if execute_keys is False else 'ON'}")
    print("Ctrl+C to quit.")
    print("=" * 60)
    engine.warm()
    engine.run(should_stop=lambda: False)


def main(argv=None):
    ap = argparse.ArgumentParser(description="STELLA console harness")
    ap.add_argument("--file", nargs="*", help="WAV file(s) to transcribe instead of live mic")
    ap.add_argument("--play", action="store_true", help="play TTS audio in file mode")
    ap.add_argument("--dry-run", action="store_true", help="live: log keybinds instead of pressing")
    args = ap.parse_args(argv)

    if args.file is not None:
        paths = [Path(p) for p in args.file] if args.file else sorted(
            (Path(__file__).parent / "test_audio").glob("phrase_*.wav"))
        run_file_mode(paths, play=args.play)
    else:
        run_live_mode(execute_keys=False if args.dry_run else None)


if __name__ == "__main__":
    main()
