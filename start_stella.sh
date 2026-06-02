#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
VENV="client/.venv-linux"
REQS="client/requirements-linux.txt"
SENTINEL="$VENV/.reqs_hash"

_pip_install() {
  "$VENV/bin/pip" install --upgrade pip --quiet
  "$VENV/bin/pip" install -r "$REQS" --quiet
  sha256sum "$REQS" | cut -d' ' -f1 > "$SENTINEL"
}

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating venv and installing dependencies (first run)..." >&2
  "$PY" -m venv "$VENV"
  _pip_install
elif [ ! -f "$SENTINEL" ] || [ "$(cat "$SENTINEL")" != "$(sha256sum "$REQS" | cut -d' ' -f1)" ]; then
  echo "Requirements changed — reinstalling dependencies..." >&2
  _pip_install
fi

if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  export YDOTOOL_SOCKET="${YDOTOOL_SOCKET:-/tmp/.ydotool_socket}"
  if ! command -v ydotool >/dev/null 2>&1 || ! command -v ydotoold >/dev/null 2>&1; then
    echo "Warning: Wayland mode needs ydotool and ydotoold for key injection." >&2
  else
    if [ ! -S "$YDOTOOL_SOCKET" ]; then
      mkdir -p "$(dirname "$YDOTOOL_SOCKET")"
      LOG="${XDG_RUNTIME_DIR:-/tmp}/stella-ydotoold.log"
      nohup ydotoold -p "$YDOTOOL_SOCKET" -P 0600 >"$LOG" 2>&1 &
      sleep 0.4
      if [ ! -S "$YDOTOOL_SOCKET" ]; then
        echo "Warning: ydotoold did not create $YDOTOOL_SOCKET. See $LOG" >&2
      fi
    fi
  fi
  echo "Wayland mode: PTT uses /dev/input/event* and key injection uses ydotool/ydotoold." >&2
fi
# Make ctranslate2's dlopen() find the pip-installed CUDA libraries.
PYVER=$("$VENV/bin/python" -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')")
NVIDIA_LIB="$VENV/lib/$PYVER/site-packages/nvidia"
for lib_dir in cublas/lib cudnn/lib; do
  [ -d "$NVIDIA_LIB/$lib_dir" ] && export LD_LIBRARY_PATH="$NVIDIA_LIB/$lib_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
done

exec "$VENV/bin/python" -m client.app "$@"
