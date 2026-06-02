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

exec "$VENV/bin/python" -m client.command_manager "$@"
