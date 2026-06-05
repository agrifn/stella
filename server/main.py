"""STELLA server: FastAPI app exposing /command, /health, and /commands CRUD.

Flow for /command:
  text -> IntentClassifier.classify -> (intent, confidence)
       -> CommandRegistry.resolve(intent) -> concrete key + confirm flag
       -> TTSHandler.synthesize(ack) -> WAV
       -> CommandResponse (intent, keybind, confirm_required, response_text, audio)

The /commands endpoints manage the command set at runtime (used by the GUI).
Because the classifier is rebuilt from the registry on every edit, adding or
editing a command immediately changes what it can recognize.
"""
from __future__ import annotations

import base64
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .command_registry import Command, CommandError, CommandRegistry
from .config import load_config
from .intent_classifier import IntentClassifier
from .models import (
    CommandModel,
    CommandRequest,
    CommandResponse,
    CommandUpdate,
    HealthResponse,
    SpeakRequest,
    SpeakResponse,
    VoiceRequest,
    VoicesResponse,
)
from .tts_handler import TTSHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("stella")


def _build_classifier(registry: CommandRegistry, cfg) -> IntentClassifier:
    """Build the embedding classifier from the current registry's example phrases."""
    cmds = {c.intent: {"examples": c.examples} for c in registry.list()}
    return IntentClassifier(cmds, model_name=cfg.classifier_model,
                            reject_threshold=cfg.classifier_reject)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    app.state.cfg = cfg
    app.state.registry = CommandRegistry(cfg.keybinds_path)
    app.state.classifier = _build_classifier(app.state.registry, cfg)
    app.state.tts = TTSHandler(cfg.tts)
    tts_ready = await app.state.tts.check_ready()
    log.info("STELLA starting: intent=embedding(%s) tts_engine=%s tts_ready=%s commands=%d auth=%s",
             cfg.classifier_model, app.state.tts.engine, tts_ready,
             len(app.state.registry.intents), bool(cfg.api_token))
    yield


def require_auth(request: Request, authorization: str | None = Header(default=None)) -> None:
    """If STELLA_API_TOKEN is set, require 'Authorization: Bearer <token>' on every
    route except /health. Unset (the default) leaves the API open for local use."""
    if request.url.path == "/health":
        return
    token = getattr(app.state, "cfg", None) and app.state.cfg.api_token
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="missing or invalid API token")


app = FastAPI(title="STELLA Server", version="0.2.0", lifespan=lifespan,
              dependencies=[Depends(require_auth)])


def _to_model(cmd: Command) -> CommandModel:
    return CommandModel(
        intent=cmd.intent,
        key=cmd.key,
        confirm_required=cmd.confirm_required,
        hold=cmd.hold,
        hold_duration=cmd.hold_duration,
        sequence=cmd.sequence,
        description=cmd.description,
        examples=cmd.examples,
        ack=cmd.ack,
    )


# --- Voice command ----------------------------------------------------------
@app.post("/command", response_model=CommandResponse)
async def command(req: CommandRequest) -> CommandResponse:
    registry: CommandRegistry = app.state.registry
    tts: TTSHandler = app.state.tts

    # Local embedding classifier: text -> intent (microseconds, CPU). Below the reject
    # threshold it returns 'chat' (not a command).
    intent, conf = app.state.classifier.classify(req.text)

    bind = registry.resolve(intent)
    # Server is authoritative for keybind/macro and the safety/confirm flag.
    keybind = bind.key if bind else None
    hold = bind.hold if bind else False
    hold_duration = bind.hold_duration if bind else None
    sequence = bind.sequence if bind else []
    confirm_required = bind.confirm_required if bind else False
    response_text = bind.ack if bind else ""  # canned ack; no LLM-generated text

    audio_b64 = None
    if req.speak and response_text:
        wav = await tts.synthesize(response_text, route="ack")
        if wav:
            audio_b64 = base64.b64encode(wav).decode("ascii")

    log.info("cmd: %r -> intent=%s (%.2f) key=%s confirm=%s",
             req.text, intent, conf, keybind, confirm_required)
    return CommandResponse(
        intent=intent,
        keybind=keybind,
        hold=hold,
        hold_duration=hold_duration,
        sequence=sequence,
        confirm_required=confirm_required,
        response_text=response_text,
        audio=audio_b64,
    )


# --- Speak arbitrary text (TTS only, no intent parsing) --------------------
@app.post("/speak", response_model=SpeakResponse)
async def speak(req: SpeakRequest) -> SpeakResponse:
    # route defaults to "chat" (Chatterbox); the client sends "ack" for command
    # feedback like "Confirmed."/"Cancelled." so those bark via fast Piper.
    wav = await app.state.tts.synthesize(req.text, route=req.route)
    audio_b64 = base64.b64encode(wav).decode("ascii") if wav else None
    return SpeakResponse(text=req.text, audio=audio_b64)


# --- Voice management -------------------------------------------------------
@app.get("/voices", response_model=VoicesResponse)
async def list_voices() -> VoicesResponse:
    tts = app.state.tts
    return VoicesResponse(active=tts.voice, available=tts.available_voices())


@app.put("/voices/active", response_model=VoicesResponse)
async def set_voice(req: VoiceRequest) -> VoicesResponse:
    tts = app.state.tts
    try:
        tts.set_voice(req.voice)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return VoicesResponse(active=tts.voice, available=tts.available_voices())


@app.post("/voices/download", response_model=VoicesResponse)
async def download_voice(req: VoiceRequest) -> VoicesResponse:
    tts = app.state.tts
    try:
        await tts.download_voice(req.voice)
    except Exception as e:  # noqa: BLE001 - bad name / network / 404
        raise HTTPException(status_code=400, detail=f"download failed: {e}") from e
    return VoicesResponse(active=tts.voice, available=tts.available_voices())


# --- Command management (CRUD) ---------------------------------------------
@app.get("/commands", response_model=list[CommandModel])
async def list_commands() -> list[CommandModel]:
    return [_to_model(c) for c in app.state.registry.list()]


@app.post("/commands", response_model=CommandModel, status_code=201)
async def create_command(cmd: CommandModel) -> CommandModel:
    try:
        created = await run_in_threadpool(app.state.registry.add, Command(
            intent=cmd.intent.strip(),
            key=cmd.key,
            confirm_required=cmd.confirm_required,
            hold=cmd.hold,
            hold_duration=cmd.hold_duration,
            sequence=[s.model_dump() for s in cmd.sequence],
            description=cmd.description,
            examples=cmd.examples,
            ack=cmd.ack,
        ))
    except CommandError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    app.state.classifier = _build_classifier(app.state.registry, app.state.cfg)
    log.info("command added: %s", created.intent)
    return _to_model(created)


@app.put("/commands/{intent}", response_model=CommandModel)
async def update_command(intent: str, patch: CommandUpdate) -> CommandModel:
    try:
        updated = await run_in_threadpool(
            app.state.registry.update, intent, **patch.model_dump(exclude_unset=True))
    except CommandError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    app.state.classifier = _build_classifier(app.state.registry, app.state.cfg)
    log.info("command updated: %s", intent)
    return _to_model(updated)


@app.delete("/commands/{intent}", status_code=204)
async def delete_command(intent: str) -> None:
    try:
        await run_in_threadpool(app.state.registry.delete, intent)
    except CommandError as e:
        code = 409 if "reserved" in str(e) else 404
        raise HTTPException(status_code=code, detail=str(e)) from e
    app.state.classifier = _build_classifier(app.state.registry, app.state.cfg)
    log.info("command deleted: %s", intent)


# --- Health -----------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    cfg = app.state.cfg
    tts = app.state.tts
    tts_ready = await tts.check_ready()
    return HealthResponse(
        status="ok",
        provider="embedding",
        model=cfg.classifier_model,
        llm_reachable=True,  # no LLM; the local classifier is always ready
        tts_ready=tts_ready,
        tts_chat_ready=tts.chat_ready(),
        tts_engine=tts.engine,
    )
