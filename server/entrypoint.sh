#!/bin/sh
# Ensure the default Piper voice exists in the (volume-mounted) voices dir, then
# start the API. Added/downloaded voices live in the same volume and persist.
set -e
VOICE_DIR="${STELLA_VOICES_DIR:-/opt/voices}"
DEFAULT=en_GB-jenny_dioco-medium
mkdir -p "$VOICE_DIR"
if [ ! -f "$VOICE_DIR/$DEFAULT.onnx" ]; then
  echo "Downloading default voice $DEFAULT ..."
  base="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/jenny_dioco/medium"
  curl -fsSL -o "$VOICE_DIR/$DEFAULT.onnx" "$base/$DEFAULT.onnx"
  curl -fsSL -o "$VOICE_DIR/$DEFAULT.onnx.json" "$base/$DEFAULT.onnx.json"
fi
exec uvicorn server.main:app --host 0.0.0.0 --port 8420
