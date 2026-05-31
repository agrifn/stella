# Setup

STELLA runs entirely on one Windows gaming PC: a native Windows client plus a Dockerized
backend inside WSL2. (Earlier builds used a separate Proxmox/LXC server - that is no
longer used; everything is in the Docker containers now.)

## Prerequisites

- Windows 11 with an NVIDIA GPU and a current driver.
- **WSL2** with an Ubuntu distro, **systemd enabled** (`/etc/wsl.conf` -> `[boot]
  systemd=true`).
- **Docker Engine + Docker Compose** inside the WSL2 distro (Docker Desktop also works).
  Confirm GPU access:
  ```bash
  docker run --rm --gpus all ubuntu nvidia-smi
  ```
  If that fails, install the **nvidia-container-toolkit** in WSL2 and
  `nvidia-ctk runtime configure --runtime=docker && systemctl restart docker`.
- **Python 3.12** on Windows (for the client venv).

## Backend (Docker, in WSL2)

From the repo:
```bash
cp .env.example .env                 # default = local Ollama
docker compose up -d --build
docker compose exec ollama ollama pull llama3.2:3b
```
This brings up two containers:
- `stella-ollama` - the LLM on the GPU (`--gpus all`), internal only.
- `stella-api` - FastAPI intent API + Piper TTS, published on `:8420`. On first run its
  entrypoint downloads the default voice into a persistent `voices` volume.

Check it:
```bash
curl http://localhost:8420/health
curl -X POST http://localhost:8420/command -H "Content-Type: application/json" \
     -d '{"text":"turn on the lights","speak":false}'
```

### Using an external LLM instead of local Ollama
Edit `.env` and `docker compose up -d` again:
```
STELLA_LLM_PROVIDER=openai          # or: anthropic
STELLA_LLM_MODEL=gpt-4o-mini
STELLA_LLM_BASE_URL=https://api.openai.com/v1   # OpenRouter/Groq/LM Studio also work
STELLA_LLM_API_KEY=sk-...
```

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
docker compose restart stella-api          # after editing server code/config
docker compose exec ollama ollama ps       # confirm the model is resident on GPU
```

## Security

The API has no auth by default, which is fine for the intended single-PC loopback
setup. Two things to know:

- **Port exposure.** `stella-api` publishes `:8420` and the container listens on
  `0.0.0.0` (WSL2's NAT forward generally requires this so the Windows host can reach
  it via `localhost`). On a shared or untrusted network, block inbound `8420` from
  non-loopback addresses in the Windows firewall, or use WSL **mirrored** networking
  so you can bind `127.0.0.1`.
- **Optional shared secret.** Set `STELLA_API_TOKEN` in `.env` (server) and the same
  value in the client (`client.api_token` in `config/settings.json`, or the
  `STELLA_API_TOKEN` env var). When set, every route except `/health` requires
  `Authorization: Bearer <token>`; the client sends it automatically. Unset (default)
  leaves the API open for local use.

## TTS engine

Default is **piper** (self-contained in the image; the entrypoint downloads a voice).
To use a host-side **Chatterbox** service (for a custom voice), set `STELLA_TTS_ENGINE=chatterbox`
and `STELLA_CHATTERBOX_URL` in `.env`, and run that service yourself on the host.
`GET /health` reports `tts_ready: false` if the selected engine cannot actually
synthesize (e.g. chatterbox selected but not running).

## Testing

Pure-logic unit tests (no CUDA / PyQt / pydirectinput needed) live in `tests/`:
```bash
pip install -r requirements-dev.txt
pytest
```
They cover the confirmation gate, keybind parsing, knowledge routing, the macro
parser, JSON extraction, and the command registry.

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
- **num_ctx must be consistent** on every Ollama call or the model reloads (multi-second
  stalls); the server pins it.
