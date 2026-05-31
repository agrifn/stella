"""Audio device diagnostic / picker.

List devices:
    python -m client.audiotest --list
Play a 1s test tone to the DEFAULT output:
    python -m client.audiotest
Play it to a specific output device index (from --list):
    python -m client.audiotest --device 7
Speak a real STELLA phrase through the server to that device:
    python -m client.audiotest --device 7 --say "shields to maximum"

Once you find the device that you can hear, set it in config/settings.json:
    "client": { "output_device": 7 }
"""
from __future__ import annotations

import argparse

import numpy as np
import sounddevice as sd

from .audio_player import AudioPlayer
from .command_sender import CommandSender
from .config import load_client_config


def list_devices() -> None:
    default_in, default_out = sd.default.device
    print(f"default input={default_in}  default output={default_out}\n")
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            host = sd.query_hostapis(d["hostapi"])["name"]
            mark = "  <- default out" if i == default_out else ""
            print(f"  [{i:2}] OUT {d['name']}  ({host}){mark}")


def tone(device: int | None) -> None:
    sr = 22050
    t = np.linspace(0, 1.0, sr, endpoint=False)
    wave = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    print(f"playing 440Hz tone to device={device} ...")
    sd.play(wave, sr, device=device)
    sd.wait()
    print("done")


def main(argv=None):
    ap = argparse.ArgumentParser(description="STELLA audio device diagnostic")
    ap.add_argument("--list", action="store_true", help="list output devices and exit")
    ap.add_argument("--device", type=int, default=None, help="output device index")
    ap.add_argument("--say", type=str, default=None,
                    help="speak this phrase via the server's TTS to the device")
    args = ap.parse_args(argv)

    if args.list:
        list_devices()
        return

    if args.say:
        cfg = load_client_config()
        sender = CommandSender(cfg.server_url)
        res = sender.send(args.say)
        if not res.audio_b64:
            print("server returned no audio (TTS disabled?)")
            return
        print(f"STELLA: {res.response_text!r}  -> device={args.device}")
        AudioPlayer(args.device).play_b64(res.audio_b64, blocking=True)
    else:
        tone(args.device)


if __name__ == "__main__":
    main()
