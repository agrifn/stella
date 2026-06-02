# Setup

STELLA runs entirely on one gaming PC: a native desktop client plus a Dockerized
backend. Windows can use WSL2 for Docker; Linux can run the same compose stack
natively.

---

## Prerequisites

### All platforms
- **Python 3.10+** for the client venv.
- **Docker Engine + Docker Compose** (native Linux, or WSL2 on Windows).
- **NVIDIA GPU + driver 525+** for local GPU Ollama and GPU STT. Older drivers or
  non-NVIDIA GPUs still work — STT falls back to CPU automatically (slower).

### Windows
- **WSL2** with an Ubuntu distro and **systemd enabled**:
  `/etc/wsl.conf` → `[boot] systemd=true`

### Linux — system packages

These must be installed via your system package manager. Everything else (Whisper,
PyQt6, CUDA libraries, etc.) is installed automatically into the Python venv.

| Package | Why needed | Required? |
|---|---|---|
| `python3-venv` | create the client venv | yes |
| `portaudio19-dev` | microphone capture (sounddevice links against PortAudio) | yes |
| `libxcb-cursor0` | PyQt6 platform plugin | yes |
| `ydotool` + `ydotoold` | key injection on **Wayland** | Wayland only |
| `xdotool` | key injection on **X11/XWayland** | X11 only |
| `xinput` + `x11-xserver-utils` | fallback PTT detection on X11 | X11 only |

Ubuntu/Debian one-liner:
```bash
sudo apt install python3-venv portaudio19-dev libxcb-cursor0 \
                 ydotool xdotool xinput x11-xserver-utils
```

Fedora/RHEL:
```bash
sudo dnf install python3 portaudio-devel libxcb xcb-util-cursor \
                 ydotool xdotool xinput xorg-x11-server-utils
```

Arch:
```bash
sudo pacman -S python portaudio libxcb xcb-util-cursor ydotool xdotool xorg-xinput
```

> **CUDA toolkit not required.** The Python venv installs `nvidia-cublas-cu12` and
> `nvidia-cudnn-cu12` as pip packages, which bundle the CUDA libraries ctranslate2
> needs. Only the NVIDIA driver (525+) must be installed at the system level.

### Docker GPU passthrough (Linux)

After installing Docker and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html):
```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
# Verify:
docker run --rm --gpus all ubuntu nvidia-smi
```

---

## Backend (Docker)

From the repo root:
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec ollama ollama pull llama3.2:3b   # first time only
```

Two containers start:
- `stella-ollama` — Ollama LLM on GPU (`--gpus all`), internal only.
- `stella-api` — FastAPI intent API + Piper TTS on `:8420`. Downloads the default
  voice into a persistent `voices` volume on first run.

Check it:
```bash
curl http://localhost:8420/health
curl -X POST http://localhost:8420/command -H "Content-Type: application/json" \
     -d '{"text":"turn on the lights","speak":false}'
```

### Using an external LLM instead of local Ollama
Edit `.env` and `docker compose up -d`:
```
STELLA_LLM_PROVIDER=openai          # or: anthropic
STELLA_LLM_MODEL=gpt-4o-mini
STELLA_LLM_BASE_URL=https://api.openai.com/v1
STELLA_LLM_API_KEY=sk-...
```

---

## Client — Windows

```bat
py -3.12 -m venv client\.venv
client\.venv\Scripts\pip install -r client\requirements.txt
```

Use `127.0.0.1` (not `localhost`) for `server_url` — `localhost` resolves to IPv6
first and WSL2's port-forward ignores it, causing a ~21 s stall per connection.

Run:
- `start_manager.bat` — Command & Voice Manager GUI (no admin needed). Use its
  "Launch STELLA" button to start the overlay.
- `start_stella.bat` — overlay directly. Self-elevates via UAC; elevation is required
  so keystrokes reach Star Citizen (SC + EAC run elevated).

---

## Client — Linux

Install system packages (see table above), then:
```bash
./start_manager.sh        # Command & Voice Manager GUI
./start_stella.sh         # overlay + voice loop; add --dry-run to log keys without pressing
```

The scripts create `client/.venv-linux` and install all Python dependencies on first
run. Subsequent launches skip the install unless `requirements-linux.txt` changes.

### Wayland (recommended)
PTT reads `/dev/input/event*` directly. Your user must have permission to read those
devices:
```bash
sudo usermod -aG input $USER   # then log out and back in
```
Verify with `client/.venv-linux/bin/python -m client.ptt_test` — hold Right Ctrl and
confirm it prints `DOWN`.

`start_stella.sh` starts `ydotoold` automatically if it is not already running. If key
injection fails, check that `ydotoold` is running and that your user can write to
`/dev/uinput` (usually the `input` group covers this).

### X11 / XWayland
Key injection uses `xdotool`. PTT falls back to `xinput`/`xmodmap` if evdev is not
accessible. No extra setup required beyond the system packages above.

### GPU STT
With a supported NVIDIA GPU the Whisper model runs on CUDA (~0.3 s/utterance). The
venv includes `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` so no separate CUDA toolkit
install is needed. Without a CUDA-capable GPU, STT falls back to CPU automatically
(~4 s/utterance on a mid-range CPU).

To use a smaller model on a lower-VRAM card, edit `config/settings.json`:
```json
"whisper_model": "small",
"whisper_compute_type": "int8"
```

### Testing injection
```bash
# PTT detection
client/.venv-linux/bin/python -m client.ptt_test

# Key injection (focus a text field first)
client/.venv-linux/bin/python -m client.keytest h e l l o
```

---

## Operating the backend

```bash
docker compose ps
docker compose logs -f stella-api
docker compose restart stella-api            # after editing server code/config
docker compose exec ollama ollama ps         # confirm model is resident on GPU
```

---

## Security

The API has no auth by default, which is fine for the intended single-PC loopback
setup.

- **Port exposure.** `stella-api` publishes `:8420` on `0.0.0.0` (WSL2's NAT
  forward requires this). On a shared or untrusted network, block inbound `8420` in
  your firewall, or use WSL **mirrored** networking to bind `127.0.0.1`.
- **Optional shared secret.** Set `STELLA_API_TOKEN` in `.env` (server) and the same
  value in the client (`client.api_token` in `config/settings.json` or the
  `STELLA_API_TOKEN` env var). All routes except `/health` then require
  `Authorization: Bearer <token>`; the client sends it automatically.

---

## TTS engine

Default is **Piper** (self-contained in the image; the entrypoint downloads a voice on
first run). To use a host-side **Chatterbox** service, set `STELLA_TTS_ENGINE=chatterbox`
and `STELLA_CHATTERBOX_URL` in `.env`. `GET /health` reports `tts_ready: false` if the
selected engine cannot synthesize.

---

## Testing

Pure-logic unit tests (no CUDA / PyQt / pydirectinput needed):
```bash
pip install -r requirements-dev.txt
pytest
```
Covers the confirmation gate, keybind parsing, knowledge routing, macro parser, JSON
extraction, and the command registry.

---

## Gotchas

- **Windows:** run the overlay as Administrator or keystrokes won't reach SC.
  `start_stella.bat` handles this automatically.
- **Linux Wayland:** fix `/dev/input/event*` permissions (`input` group) before
  debugging anything else — if `ptt_test` never shows `DOWN`, PTT will never work.
- **Overlay over the game:** use borderless/windowed mode; exclusive fullscreen hides
  the overlay.
- **Wrong audio device:** if you can't hear STELLA, run
  `python -m client.audiotest --list` and set `output_device` in `config/settings.json`.
- **Ollama on CPU:** run `docker compose exec ollama ollama ps` — if the GPU column is
  empty, Docker doesn't have GPU access. Re-run `nvidia-ctk runtime configure` and
  restart Docker.
- **num_ctx consistency:** all Ollama calls use the same context size or the model
  reloads (multi-second stalls). The server pins this automatically.
