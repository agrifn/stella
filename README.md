# STELLA - Star Citizen voice copilot

STELLA is a local, voice-controlled AI ship assistant for Star Citizen. You hold a
push-to-talk key and speak; STELLA transcribes it, decides the intent, presses the
matching ship keybind in-game, and speaks a short confirmation - in about a second.

Everything runs on the gaming PC. Nothing is sent to the cloud unless you opt into an
external LLM provider.

## Architecture

```
ONE machine (Windows + WSL2, RTX 5090)

  Native Windows client                     Docker backend  (WSL2, "stella-stack")
  --------------------                      --------------------------------------
  faster-whisper STT (CUDA)                   stella-ollama   ollama/ollama --gpus all
  push-to-talk capture          HTTP            -> llama3.2:3b (100% GPU)
  PyQt6 overlay HUD          127.0.0.1:8420    stella-api      FastAPI + Piper TTS
  pydirectinput keybinds  <-------------->       /command  /speak  /commands  /voices
  CHAT-mode text injection                       /health
  audio playback
```

- The client is **native Windows** (it needs the mic, global hotkeys, keystroke
  injection into the game, audio out, and an overlay - none of which work from a
  container).
- The brain (LLM + TTS) is a **two-container Docker stack** in WSL2 with GPU access.
- They talk over `127.0.0.1:8420` (WSL2 forwards the port to Windows).

## Features

- **Speech to intent:** faster-whisper (`large-v3-turbo`) -> Ollama classifies into a
  ship command using a strict JSON schema (no rambling).
- **Server-resolved keybinds:** the LLM only picks an intent; the server maps it to a
  key via `config/keybinds.json`, so the model can never hallucinate a keybind. The
  server is also authoritative for the `confirm_required` safety flag.
- **Keystroke execution:** pydirectinput (SendInput scancodes - the VoiceAttack-style
  approach that works under EAC). Supports combos, left/right modifiers, double-taps,
  and held keys.
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

Prereqs: Windows 11, WSL2 (Ubuntu, systemd on), Docker + nvidia-container-toolkit inside
WSL2, an NVIDIA GPU, and Python 3.12. See [docs/setup.md](docs/setup.md) for details.

**Backend** (in WSL2):
```bash
cp .env.example .env            # default = local Ollama
docker compose up -d --build
docker compose exec ollama ollama pull llama3.2:3b   # first time
```

**Client** (Windows, in this folder):
```bat
py -3.12 -m venv client\.venv
client\.venv\Scripts\pip install -r client\requirements.txt
```
Then launch (both .bat files self-/non-elevate as needed):
- `start_manager.bat`  -> the Command & Voice Manager GUI (has a "Launch STELLA" button)
- `start_stella.bat`   -> the overlay directly (runs as Administrator so keystrokes reach
  the game; SC + EAC run elevated)

Hold the PTT key (Right Ctrl) to talk; `Ctrl+Alt+M` toggles CHAT/COMMAND. Run Star Citizen
in borderless/windowed so the overlay shows.

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
config/   keybinds.json, settings.json
docs/     setup.md
voice/    optional Chatterbox TTS service (host-side; bring your own voice)
docker-compose.yml   start_stella.bat   start_manager.bat
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
- **Privacy:** STT, the local LLM, and Piper TTS run on your machine; nothing leaves it
  unless you opt into an external LLM or the UEX/knowledge APIs.
