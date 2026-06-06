# Setup

STELLA runs entirely on one Windows gaming PC: a native Windows client plus a Dockerized
backend inside WSL2. (Earlier builds used a separate Proxmox/LXC server and a local LLM -
both are gone; everything is in one Docker container now, and intent is a local embedding
classifier, not an LLM.)

## Prerequisites

- Windows 11 with an NVIDIA GPU and a current driver (the GPU is for the client's Whisper
  STT; the backend container is CPU-only).
- **WSL2** with an Ubuntu distro, **systemd enabled** (`/etc/wsl.conf` -> `[boot]
  systemd=true`).
- **Docker Engine + Docker Compose** inside the WSL2 distro (Docker Desktop also works).
- **Python 3.12** on Windows (for the client venv).

## Backend (Docker, in WSL2)

From the repo:
```bash
cp .env.example .env                 # optional; defaults work with no edits
docker compose up -d --build
```
This brings up a single container:
- `stella-api` - FastAPI intent classifier + Piper TTS, published on `:8420`. The build
  bakes the embedding classifier model into the image; on first run the entrypoint
  downloads the default Piper voice into a persistent `voices` volume. There is no LLM
  and no model to pull.

Check it:
```bash
curl http://localhost:8420/health
curl -X POST http://localhost:8420/command -H "Content-Type: application/json" \
     -d '{"text":"turn on the lights","speak":false}'
```
`/health` reports `provider: embedding` and the classifier model.

## Client (Windows)

```bat
py -3.12 -m venv client\.venv
client\.venv\Scripts\pip install -r client\requirements.txt
```
The client reaches the backend at `http://127.0.0.1:8420` (use `127.0.0.1`, not
`localhost` - `localhost` resolves to IPv6 first and WSL2's forward ignores it, causing
a ~21s stall per connection).

Run it:
- `start_manager.bat` - the Command & Voice Manager GUI (no admin needed). Use its
  "Launch STELLA" button to start the overlay.
- `start_stella.bat` - the overlay directly. It self-elevates via UAC, which is required
  so keystrokes reach Star Citizen (SC + EAC run elevated).

## Operating the backend

```bash
docker compose ps
docker compose logs -f stella-api
# After editing server code/config, a clean cycle is the most reliable:
docker compose build stella-api && docker compose down && docker compose up -d
```

## Voices

The backend ships with the free `en_GB-jenny_dioco-medium` Piper voice. In the Command &
Voice Manager you can:
- **Add voice...** - download any of the ~100 free Piper voices by name
  (browse: https://rhasspy.github.io/piper-samples/).
- **Set / Test** - switch the active voice and preview it.
- **Export... / Import...** - save a voice as a single `.zip` bundle to share with another
  STELLA user, or install one shared with you. This is how you share a custom voice
  without putting it in the repo.

## Security

The API has no auth by default, which is fine for the intended single-PC loopback setup.
Two things to know:

- **Port exposure.** `stella-api` publishes `:8420` and the container listens on
  `0.0.0.0` (WSL2's NAT forward generally requires this so the Windows host can reach it
  via `localhost`). On a shared or untrusted network, block inbound `8420` from
  non-loopback addresses in the Windows firewall, or use WSL **mirrored** networking so
  you can bind `127.0.0.1`.
- **Optional shared secret.** Set `STELLA_API_TOKEN` in `.env` (server) and the same
  value in the client (`client.api_token` in `config/settings.json`, or the
  `STELLA_API_TOKEN` env var). When set, every route except `/health` requires
  `Authorization: Bearer <token>`; the client sends it automatically. Unset (default)
  leaves the API open for local use.

## TTS engine

Default is **piper** (self-contained in the image; the entrypoint downloads a voice).
An optional host-side **Chatterbox** clone service lives under `voice/` and is off by
default; point `STELLA_TTS_CHAT_ENGINE=chatterbox` (and `STELLA_CHATTERBOX_URL`) at it
only if you run that service yourself. `GET /health` reports `tts_ready: false` if the
selected engine cannot actually synthesize.

## Testing

Pure-logic unit tests (no CUDA / PyQt / pydirectinput needed) live in `tests/`:
```bash
pip install -r requirements-dev.txt
pytest
```
They cover the confirmation gate, keybind parsing, the power slot-rule and its collision
cases, n-best selection, multi-command splitting, mode-switch detection, voice-bundle
export/import, the macro parser, and the command registry. Offline accuracy/eval scripts
(needing the model) are under `tools/` (`classifier_eval`, `slot_eval`, `combined_eval`).

## Notes / gotchas

- **Run the overlay as Administrator** or keystrokes won't reach SC. `start_stella.bat`
  handles this; the overlay also warns if it's not elevated.
- **Overlay over the game:** use borderless/windowed; an exclusive-fullscreen game may
  hide the overlay.
- **Audio device:** if you can't hear STELLA, the default output may be the wrong device
  (e.g. an HDMI/TV sink). `python -m client.audiotest --list` then set `output_device` in
  `config/settings.json`.
- **Whisper on Windows** needs the cuBLAS/cuDNN DLLs from the `nvidia-*-cu12` wheels on
  PATH; `client/cuda_paths.py` handles this. On an 8GB GPU drop `whisper_model` to
  `small`/`int8` in settings.
- **Classifier thresholds** (`classifier.reject_threshold` / `clarify_threshold` in
  settings) are the main accuracy lever: below reject = treated as chat; in the gray band
  STELLA asks "Say again?". Tune them on your own transcribed commands.
