# STELLA - Star Citizen voice copilot

STELLA is a local, voice-controlled AI ship assistant for Star Citizen. You hold a
push-to-talk key (or say the wake word) and speak; STELLA transcribes it, classifies the
intent locally, presses the matching ship keybind in-game, and speaks a short
confirmation - typically well under a second from the end of speech.

Everything runs on the gaming PC. There is no LLM and no cloud in the loop: intent is
decided by a tiny local embedding classifier, so nothing you say leaves your machine.

## Architecture

```
ONE machine (Windows + WSL2, NVIDIA GPU)

  Native Windows client                     Docker backend (WSL2, "stella-stack")
  --------------------                      -------------------------------------
  faster-whisper STT (CUDA)                   stella-api   (single container)
  embedding intent classifier (CPU)   HTTP      FastAPI
  push-to-talk + "Stella" wake word 127.0.0.1:8420  Piper TTS
  PyQt6 overlay HUD        <-------------->       command management (CRUD)
  pydirectinput keybinds                          /command /speak /commands /voices /health
  CHAT-mode text injection
  audio playback
```

- The client is **native Windows** (it needs the mic, global hotkeys, keystroke
  injection into the game, audio out, and an overlay - none of which work from a
  container).
- **Intent is decided in-client**: the static-embedding classifier (model2vec
  `potion-32M`, CPU/numpy) runs in the client process, so classification adds no
  network hop to the action path. The backend is a **single Docker container** in
  WSL2 serving Piper TTS and command management; its `/command` endpoint remains as
  a fallback (set `local_intent: false` in settings.json to use it). No LLM, no
  Ollama; the backend does not need the GPU.
- They talk over `127.0.0.1:8420` (WSL2 forwards the port to Windows) for TTS and
  command edits.

## How intent recognition works

No LLM. Three layers, cheapest first, running in the client process and rebuilt from
`keybinds.json` on every edit (GUI or by hand - changes are picked up live):

1. **Deterministic power slot-rule** - a closed-vocabulary rule maps pool
   (weapons / engines / shields) x direction (max / min / up one / down one / toggle)
   to the exact power intent. Removes the near-synonym confusion an embedder has across
   the dozen power commands.
2. **A few disambiguation rules** for pairs that look alike (reset vs all power, MFD
   forward vs back).
3. **Static-embedding nearest-example match** (`potion-32M`) over the command example
   phrases, cosine similarity. Below a reject threshold the utterance is treated as
   chat, not a command. A borderline match (just above the threshold) makes STELLA ask
   **"Say again?"** rather than fire a guess.

It runs in well under a millisecond on the CPU.

## Features

- **Speech to intent, fully local:** faster-whisper (`large-v3-turbo`, greedy decode
  tuned for the closed command vocabulary) -> in-process embedding classifier. No
  cloud, no LLM, no API keys.
- **Speculative STT:** you hold PTT a beat past your last word anyway - STELLA starts
  transcribing the moment you stop talking, so the text is ready at key release. If
  you speak again before releasing, the early result is safely discarded.
- **Push-to-talk or hands-free:** hold Right Ctrl, or say the wake word **"Stella"**.
- **Registry-resolved keybinds:** the classifier only picks an intent; the command
  registry (`config/keybinds.json`) maps it to a key and owns the `confirm_required`
  safety flag, so the recognizer can never invent a keybind. Hand edits to the file
  are picked up live (the classifier rebuilds on change).
- **Keystroke execution:** pydirectinput (SendInput scancodes - the VoiceAttack-style
  approach that works under EAC). Combos, left/right modifiers, double-taps, held keys.
- **Multi-command & repeat:** "lower shields and raise engine power", "fire three
  flares" - split into steps client-side so the classifier stays single-intent.
- **Recovery layers:** ASR **n-best rescoring** (re-score alternate transcripts) and the
  spoken **"Say again?"** prompt, both only on the uncertain path so confident commands
  keep full speed.
- **Follow-up mode:** after a command fires, a short hands-free window listens for the
  next command with no PTT or wake word.
- **Voice-confirm gate:** dangerous commands (`eject`, `self_destruct`) require a spoken
  "yes" before they fire.
- **CHAT mode:** speak and it types the text wherever your cursor is; switch by voice
  ("switch to chat" / "switch to command") or `Ctrl+Alt+M`.
- **Overlay HUD:** frameless, click-through, always-on-top - mode, listening state, last
  speech, last reply.
- **Command & Voice manager (GUI):** add/edit/delete commands at runtime (updates the
  keybind map and the classifier together), browse/download/switch Piper voices, and
  **export/import a voice** as a single `.zip` to share it with a friend.
- **Piper TTS:** built into the API image; ~100 free voices are downloadable in-app and
  it ships with `en_GB-jenny_dioco-medium`. (An optional host-side Chatterbox clone
  service still lives under [voice/](voice/) but is off by default.)

Latency: intent classification runs in-process (no network hop) and takes well under a
millisecond; STT dominates the action path and the spoken reply follows in the
background so it never delays the action. See [docs/latency.md](docs/latency.md) for
the tuning flags (greedy decode, speculative STT, endpointing).

## Quick start

Prereqs: Windows 11, WSL2 (Ubuntu, systemd on), Docker inside WSL2, an NVIDIA GPU (for
the client's Whisper STT - the backend itself is CPU-only), and Python 3.12. See
[docs/setup.md](docs/setup.md) for details.

**Backend** (in WSL2):
```bash
cp .env.example .env            # optional; the defaults work with no edits
docker compose up -d --build
```
First build bakes the classifier model into the image and the entrypoint downloads the
default Piper voice into a persistent volume. There is no model to pull.

**Client** (Windows, in this folder):
```bat
py -3.12 -m venv client\.venv
client\.venv\Scripts\pip install -r client\requirements.txt
```
Then launch (both .bat files self-/non-elevate as needed):
- `start_manager.bat`  -> the Command & Voice Manager GUI (has a "Launch STELLA" button)
- `start_stella.bat`   -> the overlay directly (runs as Administrator so keystrokes reach
  the game; SC + EAC run elevated)

Hold the PTT key (Right Ctrl) to talk, or say "Stella"; `Ctrl+Alt+M` toggles
CHAT/COMMAND. Run Star Citizen in borderless/windowed so the overlay shows.

## Configuration

- `config/keybinds.json` - the command set (intent -> key, hold, confirm, example
  phrases, and a canned spoken ack). Curated for SC Alpha 4.0. Edit by hand or via the GUI.
- `config/settings.json` - PTT key, wake word, mode toggle, Whisper model, audio device,
  overlay position, classifier thresholds, n-best / follow-up toggles, server URL, and
  the latency flags (`stt_beam_size`, `local_intent`, `speculative_stt`,
  `spec_silence_s`, `wake_capture_silence` - see [docs/latency.md](docs/latency.md)).
- `.env` - all optional (see `.env.example`): classifier model override, TTS engine, and
  an optional API token. No LLM keys; never commit it.

## Layout

```
server/   FastAPI app (main, config, models, command_registry, intent_classifier,
          tts_handler), Dockerfile, entrypoint.sh
client/   engine (shared voice loop), app (overlay), overlay, audio_capture (PTT +
          hands-free), endpointing (speculative STT decisions), wake_word,
          stt_handler, local_intent (in-process classifier), mode_switch, multicmd,
          confirm, keybind_executor, chat_injector, command_sender, audio_player,
          command_manager + commands_api (GUI), cuda_paths, config
config/   keybinds.json, settings.json
tools/    offline eval scripts (classifier_eval, slot_eval, combined_eval, add_acks)
tests/    pure-logic unit tests (no CUDA / PyQt / pydirectinput needed)
docs/     setup.md, latency.md
voice/    optional host-side Chatterbox clone service (off by default; bring your own voice)
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
  is not ours; this repo ships **no Cortana voice or audio**. The default voice is a
  free Piper voice; downloadable voices carry their own per-voice licenses.
- **Voice cloning / sharing:** the optional Chatterbox path in [voice/](voice/) and the
  voice export/import feature use audio you supply. Only use voices you have the right to
  use, and do not use them to impersonate anyone. No voice or reference audio is included
  here.
- **Anti-cheat:** STELLA uses synthetic keystrokes (VoiceAttack-style SendInput, which
  the community runs under EAC). That has worked in practice, but anti-cheat behavior
  can change at any time and is outside this project's control. Use at your own risk.
- **Privacy:** STT, the intent classifier, and Piper TTS all run on your machine; nothing
  you say leaves it.
