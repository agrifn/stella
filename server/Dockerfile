# STELLA backend: FastAPI intent API + Piper TTS.
# Build from the repo root:  docker build -f server/Dockerfile -t stella-api .
FROM python:3.12-slim

# piper-tts (the pip package, invoked as `python3 -m piper`) needs libgomp for its
# onnxruntime backend; curl is used by entrypoint.sh to fetch the default voice.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*
# TTS engine = the piper-tts pip package (see requirements.txt + STELLA_PIPER_BIN).
# Voices live in a mounted volume; the entrypoint downloads the default at first run.

WORKDIR /app
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

COPY server/ /app/server/
COPY config/ /app/config/
RUN sed -i 's/\r$//' /app/server/entrypoint.sh  # tolerate CRLF from Windows

ENV STELLA_PIPER_BIN="python3 -m piper" \
    STELLA_VOICES_DIR=/opt/voices \
    STELLA_HOST=0.0.0.0 \
    STELLA_PORT=8420

EXPOSE 8420
CMD ["sh", "/app/server/entrypoint.sh"]
