# STELLA - System Overview and Research Briefing

A self-contained briefing on STELLA, a local voice copilot for the game Star Citizen.
Written so a fresh assistant can understand the whole system and help research open
problems. No secrets are included.

---

## 1. What STELLA is

STELLA is a private, locally-hosted AI voice assistant that lets a player control
Star Citizen (a flight/space sim with a very large keybind set) by talking. The pilot
speaks a phrase ("raise shields", "max engines", "fire three flares"); STELLA
transcribes it, classifies the intent with a local LLM, and sends the corresponding
keystroke(s) into the game. It also speaks short acknowledgements back in a custom
voice. It is single-user, runs entirely on the player's own hardware, and uses no
cloud services for the core loop.

Design goals: low latency (sub-second time-to-action), fully local/offline, robust
keystroke delivery that the game's anti-cheat accepts, and a characterful voice.

---

## 2. Hardware and topology

- One gaming PC, hostname "llamasys": AMD/Windows host with an NVIDIA RTX 5090 (32 GB
  VRAM, Blackwell / compute capability sm_120). Star Citizen runs here.
- The STELLA client runs natively on Windows (it needs the mic, the keyboard, and to
  inject input into the game).
- The backend runs in Docker inside WSL2 (Ubuntu 24.04) on the same machine. WSL2
  forwards localhost, so the client talks to the backend at http://127.0.0.1:8420.
- A development laptop holds the git repo and is used to edit and deploy. Deploy =
  copy files to the Windows client folder and to the WSL backend dir, rebuild the
  container.
- Measured end-to-end latency after consolidating everything on the 5090: roughly
  0.5 to 0.9 seconds (STT 0.07 to 0.3 s, intent + ack the rest).

Paths:
- Windows client: `C:\Users\agrif\Desktop\STELLA`
- Backend stack: `/opt/stella-stack` (WSL2)

---

## 3. Architecture and data flow

```
  mic ─▶ STT (Whisper) ─▶ HTTP /command ─▶ LLM intent ─▶ command registry
                                                              │
                          keybind / macro  ◀──────────────────┘
                                │
                pydirectinput ─▶ Star Citizen
                                │
                       TTS ack ◀┘  (spoken back in the Cortana voice)
```

Two trigger paths feed the same command pipeline:
1. Push-to-talk (PTT): hold a key, speak, release.
2. Wake word: say "Stella", then the command is captured hands-free (see section 8).

Two modes:
- COMMAND mode: the utterance is classified to a ship command and executed.
- CHAT mode: the utterance is typed into the in-game text chat instead.

### Client (native Windows, Python)
- STT: faster-whisper, model `large-v3-turbo`, CUDA, float16. Aggressive hallucination
  gating: utterances shorter than a duration threshold or below an RMS loudness gate
  are dropped before STT; transcript segments with high no_speech_prob or very low
  avg_logprob are dropped, plus a stoplist of known Whisper hallucination phrases
  ("Thank you.", "you", etc.). This stops phantom commands from silence/noise.
- Input capture: a single always-open sounddevice InputStream at 16 kHz mono. PTT
  gates the record buffer; an always-on "monitor" tap feeds the wake-word listener
  from the same stream (only one mic stream total).
- Keybind executor: pydirectinput (SendInput with DirectInput scancodes, the same
  external-virtual-input approach VoiceAttack uses). This matters because Star Citizen
  reads raw/DirectInput and the Easy Anti-Cheat (EAC) layer blocks DLL injection and
  memory tampering but allows synthetic input. Supports modifiers, left/right-specific
  modifiers (altleft/altright), a lone modifier used as the key (e.g. boost = held
  shift), double-tap (f10+f10), tap vs hold, configurable hold duration, and macros
  (ordered multi-step sequences with per-step hold/taps/delay).
- Multi-command and repeat (done purely client-side so the LLM stays single-intent):
  one utterance is split on "and"/"then" into multiple commands run in turn, and a
  leading small-number word/digit ("fire three flares") repeats the keybind N times.
- GUI: a PyQt6 "Command Manager" (CRUD over the command set, a "capture key" recorder,
  a "test phrase" classifier, mic/output device pickers, hotkey and mode settings,
  wake/sleep settings) plus a floating overlay that shows state.
- Confirm gate: commands flagged dangerous (eject, self destruct) require a spoken
  "yes" (captured via PTT) before firing. Negations veto.

### Backend (Docker in WSL2)
- Two containers via docker-compose: `stella-ollama` (the LLM runtime) and `stella-api`
  (FastAPI). The api reaches ollama over the internal network.
- stella-api endpoints: `/command` (classify + resolve + optionally synthesize),
  `/speak` (TTS only), `/health`, `/commands` CRUD (used by the GUI), `/voices`.
- LLM: Ollama running `llama3.2:3b`, temperature 0, num_ctx 4096, keep_alive 24h
  (model held resident to avoid multi-second cold loads). The system prompt is
  generated dynamically from the command registry on every request, so adding/editing
  a command immediately changes what the model recognizes. The model only classifies
  intent; it never picks the keystroke. The server is authoritative for the
  key/macro/hold/confirm mapping.
- Command registry: `config/keybinds.json`, the single source of truth. Each command:
  intent name, key or macro sequence, hold flag, optional per-command hold_duration,
  confirm_required, a description, and example spoken phrases (fed to the prompt).

---

## 4. The command set (about 40 commands)

Curated from Star Citizen Alpha 4.x defaults. Highlights:

- Power management (the PIP system). Each pool (Weapons F5, Engines F6, Shields F7)
  has four actions, distinguished by tap vs hold and a Left-Alt decrease modifier:
  - increase one pip (tap F-key), set to max (hold F-key)
  - decrease one pip (tap Alt+F-key), set to min (hold Alt+F-key)
  So twelve commands across the three pools, plus reset/balance (F8) and on/off
  toggles for weapons (P), shields (O), thrusters (I), and all power (U). The "set to
  max/min" commands use a short per-command hold (~0.25 s) so they snap; eject/self
  destruct keep a long (~1.5 s) safety hold.
- Flight: landing gear, VTOL, decouple, cruise control, nav/quantum mode, request
  landing, systems ready.
- Combat/utility: countermeasures (decoy burst), gimbal cycle, unlock target, scan
  mode, ping, MFD cycle, headlights, look behind, camera cycle, exit seat.
- Apps: mobiglas, comms, starmap.
- Dangerous (confirm-gated): eject, self destruct.

---

## 5. The voice (custom "Cortana", Piper)

The TTS voice is a custom clone of Cortana (the Halo AI character, voiced by Jen
Taylor). Note this is a personal, non-distributed project; the voice model and any
reference audio are never committed or shared (trademark/IP reasons).

Current production engine: Piper (specifically piper1-gpl, the OHF-Voice fork), a fast
local VITS-based TTS. The voice `cortana_v3` was fine-tuned from the rhasspy
`en_US-lessac-medium` checkpoint on isolated Halo 4 Cortana game-dialogue clips
(roughly 1,595 clean clips, ~81 minutes, resampled to 22050 Hz mono). Training ran on
the 5090 in WSL2.

Inference is tuned via three Piper parameters baked into the voice's config json
(piper1-gpl reads them as defaults):
- noise_scale 0.55 (prosodic variation; lower is calmer/steadier, higher more
  expressive/intense)
- noise_w 0.60 (phoneme timing variation; lower is crisper articulation)
- length_scale 1.35 (pace; higher is slower. Tuned up from 1.0 for intelligibility
  "in action")

Known voice characteristics:
- The training data is intense combat dialogue, so the model's baseline delivery reads
  as urgent/commanding ("angry"). Lowering noise_scale calms it; the timbre itself is
  learned and can only be shifted, not removed, by inference params.
- The clips carry the in-game comms/hologram reverb, so the voice has reverb baked in.
  This is kept on purpose (it sounds right for the character).

### History worth knowing (engine decision)
- Earlier the system used Resemble AI's Chatterbox (a zero-shot voice clone, no
  training, runs as a resident GPU service ~5-7 GB VRAM) for higher-quality/expressive
  chat replies, with Piper for fast command acks (a hybrid, routed per utterance).
- Once the Piper Cortana was tuned to "good enough", Chatterbox was dropped entirely to
  simplify: Piper-only for both acks and chat. This freed the VRAM, removed a host-side
  service, and made TTS fully self-contained in the container again.
- A de-reverb pass (resemble-enhance) was explored to dry out the training data and
  improve clarity, but abandoned because the reverb is wanted and the tool was slow on
  CPU and incompatible with the Blackwell GPU.

---

## 6. Intent classification details

- Single small model: llama3.2:3b. Chosen over qwen3:4b which misclassified intents.
- The prompt is compact: a role/format preamble plus, per command, the intent name, a
  short description, and a few example phrases, all generated from the registry.
- Critical gotcha discovered the hard way: if the prompt exceeds num_ctx, Ollama
  silently truncates it, the model loses command definitions, and classification goes
  wild. The fix was raising num_ctx from 2048 to 4096 (the prompt grew to ~2050 tokens
  with the full command set). num_ctx must be identical on every call or the model
  reloads.
- A "knowledge" feature (looking up ship/equipment stats, where-to-buy, crafting from
  community APIs) was built and then disabled: STELLA is currently a pure
  voice-command assistant. Disabling it also shrank the prompt (~350 tokens) and
  removed command-vs-info misclassification, improving command accuracy. It may return
  later.

---

## 7. Latency tuning notes

- Time-to-action is the priority, so TTS is kept off the action path: a command is
  classified with speak=false, executed immediately, and the spoken ack is synthesized
  and played afterwards in the background.
- Keep the LLM resident (keep_alive 24h) to avoid cold loads.
- The num_ctx value must be pinned consistently to avoid model reloads.
- STT runs locally on the 5090; large-v3-turbo gives good accuracy at low latency.

---

## 8. Wake word ("Stella") - and the main open problem

- Framework: openWakeWord. A custom "Stella" model was trained using synthetic
  positive samples (piper-sample-generator with a LibriTTS voice), negative speech
  features (ACAV100M), reverb impulse responses (MIT RIRs), and ESC-50 background
  noise. Exported to ONNX, run client-side with onnxruntime over the always-on mic
  stream in 80 ms / 1280-sample frames.
- Behavior: the listener is always on. Saying "Stella" acts like a push-to-talk press:
  it fires an event and the run loop captures the following command hands-free using a
  simple VAD (start recording, stop after ~0.8 s of trailing silence or 6 s max, give
  up if no speech within a grace period). PTT still works alongside it.

Problems with the current wake model (good research targets):
- Recall is low: about 0.34 at 0 false-positives-per-hour at the chosen operating
  point. In practice it can miss "Stella" and needs the detection threshold lowered
  (currently 0.2) to trigger reliably.
- Low threshold plus always-on plus a noisy game environment risks false triggers,
  which now fire stray hands-free commands. There is tension between recall (catch
  "Stella") and precision (do not fire on random speech/gunfire/comms chatter).
- The single-word, two-syllable target ("Stella") is inherently harder than longer
  wake phrases.

Open questions for research:
- How to raise recall and precision for a custom single-word wake model: more and more
  varied positive data (real recordings vs synthetic, augmentation strategies), better
  negative/hard-negative mining, training duration, model capacity.
- Whether openWakeWord is the best framework here, or alternatives (microWakeWord,
  Picovoice Porcupine, snowboy-style, or a small custom CNN/RNN) would do better for a
  single custom word, fully local, on Windows.
- Practical false-trigger mitigation for an always-on assistant in a loud game
  (thresholding, debouncing, requiring speech to follow, secondary verification).

---

## 9. Current state (what is live)

- Backend: Piper-only TTS, voice `cortana_v3` tuned (noise 0.55 / noise_w 0.60 /
  length 1.35), Chatterbox dropped and its VRAM freed. LLM llama3.2:3b at num_ctx 4096.
  40 commands. Healthy.
- Client: PTT working; wake word "Stella" working as a hands-free PTT trigger
  (always-on). Multi-command and repeat working. GUI with full settings.
- Voice quality: accepted by the user (clear enough, energetic, reverb kept).
- The repo is private. There is an MIT license and disclaimers prepared
  (not affiliated with the game's publisher, Microsoft, or Resemble AI; the voice
  model is not shipped).

---

## 10. Key technical learnings / gotchas (for context)

- Anti-cheat: send DirectInput scancodes (pydirectinput), and hold keys for a real
  interval, not an instantaneous event, or the game treats a hold as a tap.
- Blackwell (sm_120) GPU: needs torch >= 2.7 with recent CUDA; older torch pins (e.g.
  resemble-enhance's torch 2.1.1) have no sm_120 kernels and fail on this GPU. CPU torch
  is fine but slow.
- piper1-gpl reads inference params (noise_scale, length_scale, noise_w) from the
  voice's .onnx.json, so retuning is a config edit plus a restart, no code change.
- Piper duration is stochastic (noise_w), so per-run clip lengths vary; not a reliable
  signal for comparing settings.
- Prompt size vs num_ctx is the single biggest classification footgun.

---

## 11. Likely research directions (pick what is relevant)

1. Wake word: improve a custom single-word ("Stella") detector for high recall AND low
   false-trigger rate, fully local on Windows, in a noisy gaming context. This is the
   top pain point. (See section 8.)
2. Voice: is Piper the best local TTS for a characterful, low-latency voice, or would
   alternatives (Piper high-quality models, XTTS, Kokoro, StyleTTS2, etc.) be better
   while staying local and fast? How to better control emotion/energy in a local TTS.
3. Intent classification: robustness and scaling of a small local LLM (3B) as the
   command set grows past 40; structured output, function-calling style, or a
   classifier model instead of a generative one.
4. "Reasoning" features previously parked: suggest a good weapon loadout for a ship,
   auto-activate on entering a ship, track a targeted ship's stats. These need game
   data sources and possibly screen/log parsing.
5. Knowledge lookups (currently disabled): ship/equipment stats, where-to-buy,
   crafting, from community APIs.
6. Latency and resource footprint optimization on a single shared GPU that is also
   running the game.

---

*End of briefing. Ask for any subsystem in more depth.*
