# STELLA - Star Citizen voice copilot

STELLA is a local, voice-controlled AI ship assistant for Star Citizen. You hold a
push-to-talk key and speak; STELLA transcribes it, decides the intent, presses the
matching ship keybind in-game, and speaks a short confirmation - in about a second.

Everything runs on the gaming PC. Nothing is sent to the cloud unless you opt into an
external LLM provider.

## Architecture

```
ONE machine (Windows + WSL2, or Linux + Docker)

  Native desktop client                     Docker backend  ("stella-stack")
  ---------------------                     --------------------------------
  faster-whisper STT (CUDA/CPU)               stella-ollama   ollama/ollama --gpus all
  push-to-talk capture          HTTP            -> llama3.2:3b (100% GPU)
  PyQt6 overlay HUD          127.0.0.1:8420    stella-api      FastAPI + Piper TTS
  Win: pydirectinput                       /command  /speak  /commands  /voices
  Linux: ydotool on Wayland or xdotool/X11 <---- /health
  CHAT-mode text injection
  audio playback
```

- The client is a **native desktop app** (it needs the mic, global hotkeys,
  keystroke injection into the game, audio out, and an overlay - none of which work
  from a container). Windows uses pydirectinput; Linux uses ydotool on Wayland or
  xdotool on X11/XWayland. Wayland PTT reads `/dev/input/event*` directly.
- The brain (LLM + TTS) is a **two-container Docker stack** with GPU access.
- They talk over `127.0.0.1:8420`.

## Features

- **Speech to intent:** faster-whisper (`large-v3-turbo`) -> Ollama classifies into a
  ship command using a strict JSON schema (no rambling).
- **Server-resolved keybinds:** the LLM only picks an intent; the server maps it to a
  key via `config/keybinds.json`, so the model can never hallucinate a keybind. The
  server is also authoritative for the `confirm_required` safety flag.
- **Keystroke execution:** Windows uses pydirectinput (SendInput scancodes - the
  VoiceAttack-style approach that works under EAC); Linux uses ydotool on Wayland or
  xdotool on X11/XWayland. Supports combos, left/right modifiers, double-taps, and
  held keys.
- **Voice-confirm gate:** dangerous commands (`eject`, `self_destruct`) require a spoken
  "yes" before they fire.
- **CHAT mode:** speak and it types the text wherever your cursor is.
- **Overlay HUD:** frameless, click-through, always-on-top - mode, listening state,
  last speech, last reply.
- **Command & Voice manager (GUI):** add/edit/delete commands at runtime (updates the
  keybind map AND the LLM prompt together), switch/download Piper voices, and a
  "Launch STELLA" button.
- **Pluggable LLM:** local Ollama (default), any OpenAI-compatible endpoint, or Anthropic
  - selected in `.env`.
- **Two TTS engines:** **Piper** (default, built into the API image, manage voices from
  the GUI) or an optional **Chatterbox** host service for a custom/cloned voice (see
  [voice/](voice/)). Pick via `STELLA_TTS_ENGINE` in `.env`; `/health` and the GUI show
  which engine is live. The GUI voice catalog applies to Piper only.
- **Knowledge lookups (experimental, off by default):** an optional add-on can answer
  factual SC questions (ship/equipment stats, "where to buy", crafting) from the
  StarCitizenWiki and UEX Corp APIs. It is **disabled by default** so STELLA stays a
  focused voice-command assistant. Re-enable it with `knowledge.enabled` in settings
  (and a free `STELLA_UEX_TOKEN` for the "where to buy" part); when off it adds zero
  overhead and the command path is never touched.

Measured: ~1.0s from end of speech to in-game action (STT ~0.3s + intent ~0.7s); the
spoken reply follows in the background so it never delays the action.

## Quick start

Prereqs: Windows 11 + WSL2 or Linux, Docker + NVIDIA Container Toolkit for local GPU
Ollama, an NVIDIA GPU (or an external LLM provider), and Python 3.10+. See
[docs/setup.md](docs/setup.md) for full details including Fedora/Arch package names.

**Backend** (same on Windows WSL2 and Linux):
```bash
cp .env.example .env            # default = local Ollama
docker compose up -d --build
docker compose exec ollama ollama pull llama3.2:3b   # first time
```
If `docker compose up` starts but Ollama runs on CPU, the NVIDIA Container Toolkit
needs to be wired to Docker (one-time setup):
```bash
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
```

**Client — Windows:**
```bat
py -3.12 -m venv client\.venv
client\.venv\Scripts\pip install -r client\requirements.txt
```
Launch `start_manager.bat` or `start_stella.bat` (self-elevates for EAC compatibility).

**Client — Linux:**

Install system packages first (Ubuntu/Debian shown — see [docs/setup.md](docs/setup.md)
for Fedora/Arch equivalents):
```bash
sudo apt install python3-venv portaudio19-dev libxcb-cursor0 \
                 ydotool xdotool xinput x11-xserver-utils
```

Then launch:
```bash
./start_manager.sh        # Command & Voice Manager GUI
./start_stella.sh         # overlay + voice loop; --dry-run logs keys without pressing
```

The scripts create `client/.venv-linux` and install all Python dependencies (including
CUDA libraries for GPU STT) on first run. Subsequent launches are instant — deps only
reinstall when `requirements-linux.txt` changes.

**Wayland (recommended):** PTT reads `/dev/input/event*` directly; key injection uses
`ydotool`. Your user needs permission to read keyboard event devices:
```bash
sudo usermod -aG input $USER   # then log out and back in
```
`start_stella.sh` starts `ydotoold` automatically if it is not already running.

**X11/XWayland:** key injection uses `xdotool`; PTT falls back to `xinput`. No extra
setup beyond the packages above.

**GPU STT:** with a supported NVIDIA GPU (driver 525+), Whisper runs on CUDA
(~0.3 s/utterance). No CUDA toolkit install required — the venv bundles the needed
libraries. Without a CUDA GPU, STT falls back to CPU automatically (~4 s/utterance).

Hold Right Ctrl to talk; use the tray menu to toggle CHAT/COMMAND mode or wake/sleep.
Run Star Citizen in borderless/windowed so the overlay can show on top.

## Configuration

- `config/keybinds.json` - the command set (intent -> key, hold, confirm, example
  phrases). Curated for SC Alpha 4.0. Edit by hand or via the GUI.
- `config/settings.json` - PTT key, mode toggle, Whisper model, audio device, overlay
  position, chat keys, server URL.
- `.env` - LLM provider/model/key (see `.env.example`). Never commit it.

## Layout

```
server/   FastAPI app (main, config, models, command_registry, prompt_builder,
          llm_handler + llm_providers, tts_handler), Dockerfile, entrypoint.sh
client/   engine (shared voice loop), app (overlay), overlay, audio_capture (PTT),
          stt_handler, keybind_executor, chat_injector, command_sender, audio_player,
          command_manager + commands_api (GUI), cuda_paths, config, test_loop, audiotest
          input_backend (cross-platform key injection), key_state (cross-platform PTT),
          ptt_test (PTT diagnostic), requirements-linux.txt
config/   keybinds.json, settings.json
docs/     setup.md
voice/    optional Chatterbox TTS service (host-side; bring your own voice)
docker-compose.yml   start_stella.bat/.sh   start_manager.bat/.sh
```

## License

MIT, see [LICENSE](LICENSE). Free to use, modify, and share, with no warranty.

## Disclaimer

A personal, non-commercial hobby project, provided as is (see [DISCLAIMER.md](DISCLAIMER.md)
for the full legal and privacy notice). In short:

- **Not affiliated** with Cloud Imperium Games / Roberts Space Industries, Microsoft,
  or Resemble AI. All names and trademarks belong to their owners. In particular,
  **"Cortana" is a trademark and intellectual property of Microsoft Corporation** and
  is not ours; this repo ships **no Cortana voice or audio**.
- **Voice cloning** (the optional Chatterbox path in [voice/](voice/)) needs your own
  reference audio, which you must have the right to use and must not use to impersonate
  anyone. No voice or reference audio is included here.
- **Anti-cheat:** STELLA uses synthetic keystrokes (VoiceAttack-style SendInput, which
  the community runs under EAC). That has worked in practice, but anti-cheat behavior
  can change at any time and is outside this project's control. Use at your own risk.
  On Linux (Bottles/Proton-GE/Wine), `ydotool` (Wayland) injects at the kernel uinput
  level and appears as real hardware to Wine and EAC — the closest equivalent to Windows
  SendInput. `xdotool` (X11) uses the XTEST extension which is more detectable in
  principle. Prefer Wayland + ydotool if anti-cheat is a concern.
- **Privacy:** STT, the local LLM, and Piper TTS run on your machine; nothing leaves it
  unless you opt into an external LLM or the UEX/knowledge APIs.
