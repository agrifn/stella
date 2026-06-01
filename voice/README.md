# Optional Chatterbox voice service

STELLA's default TTS is **Piper**, which runs inside the API container and needs
nothing here. This directory is for an **optional** higher-quality voice served by
[Chatterbox](https://github.com/resemble-ai/chatterbox) as a small host-side
service that STELLA calls when `STELLA_TTS_ENGINE=chatterbox`.

> Important: this repo ships **no trained voice and no reference audio**. To clone
> a voice you must supply your own reference clip and have the right to use it.
> Read the "Legal and privacy" section below first, and see the root
> [DISCLAIMER.md](../DISCLAIMER.md).

## Setup

On the machine that will host the voice (the same box as the backend is fine):

```bash
mkdir -p /opt/chatterbox && cd /opt/chatterbox
cp /path/to/repo/voice/chatterbox_server.py .
python3 -m venv venv && . venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu128   # your CUDA build
pip install -r /path/to/repo/voice/requirements.txt
```

Run it (foreground test):
```bash
CB_MODEL=turbo uvicorn chatterbox_server:app --host 0.0.0.0 --port 8123
curl -s -X POST localhost:8123/synthesize -H 'Content-Type: application/json' \
     -d '{"text":"Systems online."}' -o test.wav
```

Run it as a service: copy `chatterbox-tts.service.example` to
`/etc/systemd/system/chatterbox-tts.service`, edit the user/paths/env, then
`sudo systemctl enable --now chatterbox-tts`.

Point STELLA at it in the repo root `.env`:
```
STELLA_TTS_ENGINE=chatterbox
STELLA_CHATTERBOX_URL=http://host.docker.internal:8123
```
(Docker-in-WSL2 reaches the host via `host.docker.internal`; the API compose file
adds the matching `extra_hosts` entry.)

### Voice cloning (optional)
Set `CB_REF` to a path to your own reference `.wav` and Chatterbox will imitate
that voice. Leave it unset to use the Chatterbox default voice.

## Legal and privacy

- **Bring your own voice.** No voice model or reference audio is included here. You
  are responsible for having the rights to any reference audio you use, and for not
  using it to impersonate a real person or for any commercial purpose.
- **Third-party names are not ours.** "Cortana" and any other character or brand
  names are the property of their respective owners (for example, Cortana is a
  trademark and intellectual property of Microsoft Corporation). This project is an
  unofficial, non-commercial fan tool and is not affiliated with, endorsed by, or
  sponsored by Microsoft, Cloud Imperium Games, or Resemble AI.
- **Do not redistribute cloned voices.** Recreating and sharing a trademarked or
  real person's voice can infringe IP and publicity/privacy rights. Keep any voice
  you generate to personal use.
- **Privacy.** Reference audio and synthesis stay on your machine; this service
  makes no outbound calls. Treat any reference recording as you would any personal
  recording.
