# Latency tuning

What changed in the latency pass, which flags control it, and how to A/B each
piece. Baseline before the pass: ~1.0s from end of speech to in-game keypress
(STT ~0.3s, /command HTTP round trip through the WSL2 port forward, flat 0.45s
hands-free endpointing). Target after: roughly 0.4 to 0.5s on the PTT path. The
spoken ack always trails the action and is never on the action path.

## What changed

1. **Greedy STT decode** (`client/stt_handler.py`). The primary decode now runs
   `beam_size=1` with `condition_on_previous_text=False` and
   `without_timestamps=True`. The vocabulary is a ~30-command closed set; beam
   search buys almost nothing there, and recognition slips are backstopped by
   n-best rescoring plus the classifier reject/clarify thresholds. Expected: STT
   ~0.3s drops to roughly 0.1 to 0.15s on the target GPU.

2. **In-process intent classification** (`client/local_intent.py`). The
   embedding classifier and command registry now run inside the client process,
   so the action path no longer pays a fresh TCP handshake to the Docker backend
   per utterance (the client deliberately disables keep-alive; see
   `command_sender.py`). Intent adds no network hop. The server keeps `/command`
   as a fallback, plus `/speak` (TTS, off the action path) and the commands CRUD.
   Side benefit: hand edits to `config/keybinds.json` are picked up live (mtime
   watch with fail-soft reload).

3. **Tighter hands-free endpointing** (`client/config.py`). Wake-word,
   follow-up, and say-again captures close after 0.32s of trailing silence
   instead of 0.45s.

4. **Speculative STT during the PTT hold** (`client/endpointing.py`,
   `client/audio_capture.py`, `client/engine.py`). Pilots hold PTT 200 to 400ms
   past their last word; once 0.35s of trailing silence accumulates in the live
   buffer, transcription starts in the background so the text is ready at key
   release. At release the snapshot is validated by content: if anything was
   spoken after it, it is discarded and the full buffer is transcribed as usual.
   On a miss the only extra cost is waiting for the short in-flight decode to
   release the model lock.

## Flags (config/settings.json, "client" section unless noted)

| Key | Default | Off / fallback value |
| --- | --- | --- |
| `stt_beam_size` | 1 (greedy) | 5 restores Whisper's robust beam search |
| `local_intent` | true | false posts /command to the server as before |
| `wake_capture_silence` | 0.32 | 0.45 is the conservative pre-pass value |
| `speculative_stt` | true | false is byte-identical to the classic flow |
| `spec_silence_s` | 0.35 | raise if speculations almost always miss |

Classifier model and thresholds come from the top-level `"classifier"` section,
which the client and server now share, so both intent paths behave identically.

## How to A/B

Flip one flag at a time in `config/settings.json`, restart STELLA, and read
`stella.log`. The per-utterance line carries everything needed:

```
audio 1.5s | STT 0.04s (cuda) spec=hit -> 'landing gear'
classify 0.00s -> intent=landing_gear (0.93) key=n clarify=False x1
```

- `STT x.xx` is end-of-capture to transcript. With `spec=hit` it is near zero
  because the decode ran during the hold; `spec=miss` means the speculation was
  discarded (spoke again after the pause, or never paused long enough);
  `spec=off` means the flag is false.
- `classify x.xx` is the intent step. In-process it is effectively 0.00; with
  `local_intent=false` it shows the full HTTP round trip.
- The n-best and say-again recovery layers log their own timings on the
  uncertain path only.

## Per-user tuning notes

- `wake_capture_silence=0.32` and `spec_silence_s=0.35` suit a brisk speaker.
  If commands get clipped mid-sentence (hands-free) raise the former toward
  0.45; if speculative decodes rarely hit, lower the latter toward 0.3 or speak
  marginally ahead of releasing the key.
- Speculation needs `min_speech_rms > 0`: silence detection reuses the loudness
  gate, so disabling the gate (0) also disables speculative STT.
- Timing targets cannot be measured in CI; verify on the target rig and tune
  `spec_silence_s` against real hold habits.
