# STELLA backend: FastAPI intent API + Piper TTS.
# Build from the repo root:  docker build -f server/Dockerfile -t stella-api .
FROM python:3.12-slim

# Piper needs libgomp (onnxruntime); curl for fetching piper + the voice.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Piper TTS binary (bundles its own libs) + the default voice model.
RUN curl -fsSL -o /tmp/piper.tar.gz \
        https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz \
    && tar -xzf /tmp/piper.tar.gz -C /opt && rm /tmp/piper.tar.gz
# Voices live in a mounted volume; the entrypoint downloads the default at first run.

WORKDIR /app
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

COPY server/ /app/server/
COPY config/ /app/config/
RUN sed -i 's/\r$//' /app/server/entrypoint.sh  # tolerate CRLF from Windows

ENV STELLA_PIPER_BIN=/opt/piper/piper \
    STELLA_VOICES_DIR=/opt/voices \
    STELLA_HOST=0.0.0.0 \
    STELLA_PORT=8420

EXPOSE 8420
CMD ["sh", "/app/server/entrypoint.sh"]
