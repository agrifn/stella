# STELLA - Star Citizen AI Copilot

Local AI copilot for Star Citizen. Voice in, ship actions and spoken AI confirmation out.
Split across a gaming desktop (STT, input, overlay) and a Proxmox server (LLM + TTS).

Status: Phases 1-3 complete and validated. The overlay GUI is Phase 4.
- Phase 1: server (LLM intent + Piper TTS)
- Phase 2: desktop STT (faster-whisper, PTT)
- Phase 3: keybind execution (pydirectinput), voice-confirm gate, and a command-manager GUI
  with live add/edit/delete backed by the server. Live in-game/EAC test pending.

## Architecture

```
Desktop (RTX 5090, Windows)                Server LXC 'stella' (Proxmox, Quadro P2200)
  PTT mic capture                            Ollama (Docker, --gpus all) : llama3.2:3b
  faster-whisper STT            HTTP         FastAPI :8420
  mode switch (CHAT/COMMAND)  -------->        /command  intent + keybind + TTS audio
  pydirectinput keybinds                       /health
  PyQt6 overlay                              Piper TTS (CPU) en_US-lessac-medium
  audio playback              <--------
```

## How /command works

1. Desktop sends transcribed text: `POST /command {"text": "put all power to shields"}`
2. `llm_handler` asks Ollama (llama3.2:3b) to classify the text into an intent, using a
   strict JSON schema (Ollama structured outputs) so the model cannot ramble.
3. `keybind_resolver` maps the intent to a real key via `config/keybinds.json`.
   The LLM never chooses keybinds, so it cannot hallucinate one. The server is also
   authoritative for `confirm_required` (eject, self_destruct).
4. `tts_handler` runs Piper on the spoken `response_text` and returns base64 WAV.
5. Response:
   ```json
   {"intent":"shields_max","keybind":"0","confirm_required":false,
    "response_text":"Routing all power to shields.","audio":"<base64 wav>","audio_format":"wav"}
   ```

## Design notes

- LLM classifies intent only; keybinds + safety flags live in `config/keybinds.json`
  (single source of truth, user editable, SOLID).
- Model is pinned in VRAM via `keep_alive` to avoid cold-load latency.
- Handlers (LLM, keybind, TTS) are constructed once and injected into the route.
- TTS is non-critical: if Piper fails the command still returns, just without audio.

## Measured performance (Phase 1, P2200)

| Stage | Result |
|-------|--------|
| qwen3:4b on GPU | 100% GPU, 3.1 GB VRAM, 30 tok/s (rejected: misclassified intents) |
| llama3.2:3b (chosen) | correct intents, ~1.5 s warm |
| End-to-end /command (LLM + TTS + LAN) | 1.7 - 2.3 s |
| TTS audio | 22050 Hz mono 16-bit WAV |

## Desktop client (Phase 2)

Runs on the gaming PC `llamasys` (Windows, RTX 5090 32GB, Blackwell). faster-whisper
`large-v3-turbo`/float16 on CUDA (~1.5GB VRAM, transient; 0.07-0.3s per short clip),
push-to-talk capture, HTTP to the server, TTS playback. Python 3.12 venv.
On an 8GB card, drop to `small`/int8 in settings.json.

```
cd sc-ai-copilot
python -m venv client\.venv
client\.venv\Scripts\pip install -r client\requirements.txt

# Headless test (no mic): transcribe sample phrases through the server
client\.venv\Scripts\python -m client.test_loop --file --play

# Live: hold PTT (Scroll Lock) to talk, F8 toggles CHAT/COMMAND
client\.venv\Scripts\python -m client.test_loop
```

Windows note: faster-whisper needs the cuBLAS/cuDNN DLLs from the nvidia-*-cu12 wheels
on PATH; `client/cuda_paths.py` handles this automatically. If the PTT key does not
register while Star Citizen is focused, run the client as administrator.

## Managing commands

Commands live in `config/keybinds.json` (server side) and are managed at runtime via
the server's CRUD API or the GUI. Adding a command updates BOTH the keybind mapping and
the LLM's prompt, so a new command is recognized immediately.

- API: `GET/POST /commands`, `PUT/DELETE /commands/{intent}`
- GUI (run on llamasys): `client\.venv\Scripts\python -m client.command_manager`
  Table of commands; Add/Edit/Delete; "Capture key" records a keypress as the bind;
  "Test phrase" shows how the AI classifies any phrase.

## Keybind execution & safety

The client presses the server-resolved key into Star Citizen via pydirectinput
(SendInput virtual keystrokes - the VoiceAttack-style approach compatible with EAC).
Dangerous commands (`eject`, `self_destruct`) are flagged `confirm_required`; in live
mode STELLA speaks a prompt and waits for a spoken "yes" before executing. Run the loop
with `--dry-run` to log keys without pressing them.

## Layout

```
server/   FastAPI app: main, config, models, llm_handler, command_registry,
          prompt_builder (dynamic prompt), tts_handler
client/   stt_handler, audio_capture (PTT), command_sender, audio_player,
          keybind_executor, cuda_paths, config, test_loop (harness),
          commands_api + command_manager (PyQt6 GUI)
config/   keybinds.json (intent -> key, source of truth), settings.json
docs/     setup.md
```

See `docs/setup.md` for how the server was provisioned and how to operate it.
