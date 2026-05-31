"""Pydantic models for the API and internal data flow."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CommandRequest(BaseModel):
    """Incoming transcribed text from the desktop client."""
    text: str = Field(..., min_length=1, description="Transcribed pilot speech")
    speak: bool = Field(True, description="Whether to synthesize TTS audio in the reply")


class SpeakRequest(BaseModel):
    """Ask the server to synthesize arbitrary text (no intent parsing)."""
    text: str = Field(..., min_length=1)


class SpeakResponse(BaseModel):
    text: str
    audio: Optional[str] = None
    audio_format: str = "wav"


class IntentResult(BaseModel):
    """What the LLM returns (keybind is NOT trusted from the model)."""
    intent: str
    confirm_required: bool = False
    response_text: str = ""


class CommandResponse(BaseModel):
    """Full reply to the client: intent + server-resolved keybind + audio."""
    intent: str
    keybind: Optional[str] = None
    hold: bool = Field(False, description="Hold the key rather than tap it (e.g. self destruct)")
    confirm_required: bool = False
    response_text: str = ""
    audio: Optional[str] = Field(None, description="base64-encoded WAV, or null")
    audio_format: str = "wav"


class HealthResponse(BaseModel):
    status: str
    provider: str
    model: str
    llm_reachable: bool
    tts_ready: bool


# --- Command management (CRUD) ---------------------------------------------
class CommandModel(BaseModel):
    """A ship command as exposed by the /commands API and the GUI."""
    intent: str = Field(..., description="Unique intent id, e.g. shields_max")
    key: Optional[str] = Field(None, description="pydirectinput key or combo, e.g. '0', 'alt+y'")
    confirm_required: bool = False
    hold: bool = Field(False, description="Hold the key rather than tap it")
    description: str = Field("", description="What this command does (shown to the LLM)")
    examples: list[str] = Field(default_factory=list, description="Sample phrases for the LLM")


class VoicesResponse(BaseModel):
    active: str
    available: list[str]


class VoiceRequest(BaseModel):
    voice: str = Field(..., description="Piper voice name, e.g. en_US-amy-medium")


class CommandUpdate(BaseModel):
    """Partial update; only provided fields change. 'intent' is the path, not editable here."""
    key: Optional[str] = None
    confirm_required: Optional[bool] = None
    hold: Optional[bool] = None
    description: Optional[str] = None
    examples: Optional[list[str]] = None
