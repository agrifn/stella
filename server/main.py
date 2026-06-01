"""STELLA server: FastAPI app exposing /command, /health, and /commands CRUD.

Flow for /command:
  text -> LLMHandler.parse_intent -> IntentResult (intent + spoken text)
       -> CommandRegistry.resolve(intent) -> concrete key + confirm flag
       -> TTSHandler.synthesize(response_text) -> WAV
       -> CommandResponse (intent, keybind, confirm_required, response_text, audio)

The /commands endpoints manage the command set at runtime (used by the GUI).
Because the LLM prompt is generated from the registry on every request, adding or
editing a command immediately changes what the model can recognize.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .command_registry import Command, CommandError, CommandRegistry
from .config import load_config
from .knowledge import KnowledgeHandler
from .llm_handler import LLMHandler
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
from .prompt_builder import PromptBuilder
from .tts_handler import TTSHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("stella")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    app.state.cfg = cfg
    app.state.registry = CommandRegistry(cfg.keybinds_path)
    app.state.prompt_builder = PromptBuilder(cfg.system_prompt_path)
    app.state.knowledge = KnowledgeHandler(cfg.knowledge)
    app.state.llm = LLMHandler(
        cfg.llm,
        system_prompt_provider=lambda: app.state.prompt_builder.build(
            app.state.registry,
            app.state.knowledge.intent_lines(),
            app.state.knowledge.examples()),
    )
    app.state.tts = TTSHandler(cfg.tts)
    tts_ready = await app.state.tts.check_ready()  # probes the chatterbox host service if selected
    log.info("STELLA starting: model=%s engine=%s tts_ready=%s commands=%d knowledge=%s uex=%s auth=%s",
             cfg.llm.model, app.state.tts.engine, tts_ready,
             len(app.state.registry.intents), cfg.knowledge.enabled,
             bool(cfg.knowledge.enabled and cfg.knowledge.uex_token), bool(cfg.api_token))
    await app.state.llm.warm()
    # Load the knowledge index in the background so it never delays startup.
    asyncio.create_task(app.state.knowledge.load())
    yield
    await app.state.llm.aclose()


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
        sequence=cmd.sequence,
        description=cmd.description,
        examples=cmd.examples,
    )


# --- Voice command ----------------------------------------------------------
@app.post("/command", response_model=CommandResponse)
async def command(req: CommandRequest) -> CommandResponse:
    llm: LLMHandler = app.state.llm
    registry: CommandRegistry = app.state.registry
    tts: TTSHandler = app.state.tts

    try:
        result = await llm.parse_intent(req.text)
    except Exception as e:  # noqa: BLE001
        log.exception("LLM parse failed")
        raise HTTPException(status_code=502, detail=f"LLM error: {e}") from e

    # Knowledge path: a knowledge question is answered from real data, not the keybind map.
    if result.intent in app.state.knowledge.intents:
        ans = await app.state.knowledge.answer(result.intent, req.text, llm)
        if ans:
            result.response_text = ans

    bind = registry.resolve(result.intent)
    # Server is authoritative for keybind/macro and the safety/confirm flag.
    keybind = bind.key if bind else None
    hold = bind.hold if bind else False
    sequence = bind.sequence if bind else []
    confirm_required = bind.confirm_required if bind else False

    audio_b64 = None
    if req.speak and result.response_text:
        # Ship-command acks use the fast Piper engine; chat/knowledge replies use the
        # higher-quality Chatterbox engine. synthesize() fails soft (returns None).
        route = "chat" if result.intent in {"chat", *app.state.knowledge.intents} else "ack"
        wav = await tts.synthesize(result.response_text, route=route)
        if wav:
            audio_b64 = base64.b64encode(wav).decode("ascii")

    log.info("cmd: %r -> intent=%s key=%s confirm=%s",
             req.text, result.intent, keybind, confirm_required)
    return CommandResponse(
        intent=result.intent,
        keybind=keybind,
        hold=hold,
        sequence=sequence,
        confirm_required=confirm_required,
        response_text=result.response_text,
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
            sequence=[s.model_dump() for s in cmd.sequence],
            description=cmd.description,
            examples=cmd.examples,
        ))
    except CommandError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    log.info("command added: %s", created.intent)
    return _to_model(created)


@app.put("/commands/{intent}", response_model=CommandModel)
async def update_command(intent: str, patch: CommandUpdate) -> CommandModel:
    try:
        updated = await run_in_threadpool(
            app.state.registry.update, intent, **patch.model_dump(exclude_unset=True))
    except CommandError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    log.info("command updated: %s", intent)
    return _to_model(updated)


@app.delete("/commands/{intent}", status_code=204)
async def delete_command(intent: str) -> None:
    try:
        await run_in_threadpool(app.state.registry.delete, intent)
    except CommandError as e:
        code = 409 if "reserved" in str(e) else 404
        raise HTTPException(status_code=code, detail=str(e)) from e
    log.info("command deleted: %s", intent)


# --- Health -----------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    cfg = app.state.cfg
    llm_ok = await app.state.llm.health()
    tts = app.state.tts
    tts_ready = await tts.check_ready()  # probes both engines; returns ack-engine readiness
    return HealthResponse(
        status="ok" if llm_ok else "degraded",
        provider=cfg.llm.provider,
        model=cfg.llm.model,
        llm_reachable=llm_ok,
        tts_ready=tts_ready,
        tts_chat_ready=tts.chat_ready(),
        tts_engine=tts.engine,
    )
