"""PTT diagnostic for the Linux/Windows client.

Usage:
    python -m client.ptt_test
    python -m client.ptt_test "right ctrl"
"""
from __future__ import annotations

import sys
import time

from .config import load_client_config
from .key_state import get_key_state


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cfg = load_client_config()
    key = argv[0] if argv else cfg.ptt_key
    keys = get_key_state()
    print(f"PTT key: {key!r}")
    print(f"backend: {getattr(keys, '_backend', '?')} id={getattr(keys, '_xinput_id', '')}")
    print("Hold/release the PTT key now. Press Ctrl+C to stop.")
    last = None
    try:
        while True:
            down = keys.is_pressed(key)
            if down != last:
                print(f"{time.strftime('%H:%M:%S')} {key}: {'DOWN' if down else 'up'}", flush=True)
                last = down
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\ndone")


if __name__ == "__main__":
    main()
