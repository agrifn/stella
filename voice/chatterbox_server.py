"""Optional host-side TTS microservice (Chatterbox) for STELLA.

A tiny FastAPI wrapper that keeps a Chatterbox TTS model warm and exposes:
  POST /synthesize  {"text": "..."}  -> 16-bit PCM WAV
  GET  /health

STELLA's backend talks to this when STELLA_TTS_ENGINE=chatterbox (see the repo
root .env.example). It is entirely optional: the default engine is Piper, which
is self-contained in the API image and needs none of this.

Voice cloning: if you point CB_REF at a reference audio clip, Chatterbox will
imitate that voice. You must provide your own reference audio and have the right
to use it. This repository ships NO reference audio or trained voice. See
voice/README.md for the legal / privacy notice before cloning any voice.

Env:
  CB_MODEL  base | turbo            (default base)
  CB_REF    path to reference .wav  (optional; unset = Chatterbox default voice)
  CB_EXAG   exaggeration            (base model only, default 0.7)
  CB_CFG    cfg weight              (base model only, default 0.4)
  CB_TEMP   sampling temperature    (default 0.8)
"""
import io
import os
import threading
import wave

import numpy as np
import perth
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel

# Chatterbox pulls in an audio watermarker; tolerate it being unavailable.
if perth.PerthImplicitWatermarker is None:
    class _NoWatermark:
        def apply_watermark(self, wav, sample_rate=None, **k):
            return wav
    perth.PerthImplicitWatermarker = _NoWatermark

MODEL = os.environ.get("CB_MODEL", "base").lower()
REF = os.environ.get("CB_REF") or None          # bring your own; none = default voice
EXAG = float(os.environ.get("CB_EXAG", "0.7"))
CFG = float(os.environ.get("CB_CFG", "0.4"))
TEMP = float(os.environ.get("CB_TEMP", "0.8"))

if MODEL == "turbo":
    from chatterbox.tts_turbo import ChatterboxTurboTTS as _CB
else:
    from chatterbox.tts import ChatterboxTTS as _CB

app = FastAPI()
_lock = threading.Lock()
_device = "cuda" if os.environ.get("CB_DEVICE", "cuda") == "cuda" else "cpu"
model = _CB.from_pretrained(device=_device)
SR = int(getattr(model, "sr", 24000))


class Req(BaseModel):
    text: str


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "sr": SR, "ref": bool(REF),
            "exaggeration": EXAG, "cfg_weight": CFG, "temperature": TEMP}


@app.post("/synthesize")
def synthesize(r: Req):
    text = (r.text or "").strip()
    if not text:
        return Response(status_code=204)
    kw = {"temperature": TEMP}
    if MODEL != "turbo":
        kw["exaggeration"] = EXAG
        kw["cfg_weight"] = CFG
    if REF:
        kw["audio_prompt_path"] = REF
    with _lock:
        wav = model.generate(text, **kw)
    w = wav.squeeze().detach().cpu().numpy()
    pcm = (np.clip(w, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm)
    return Response(content=buf.getvalue(), media_type="audio/wav")
