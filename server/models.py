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
    route: str = Field("chat", description="'ack' -> fast Piper, 'chat' -> Chatterbox")


class SpeakResponse(BaseModel):
    text: str
    audio: Optional[str] = None
    audio_format: str = "wav"


class MacroStep(BaseModel):
    """One step of a macro sequence."""
    key: str
    hold: bool = False
    taps: int = 1
    delay: float = Field(0.1, description="Pause after this step, seconds")


class CommandResponse(BaseModel):
    """Full reply to the client: intent + server-resolved keybind/macro + audio."""
    intent: str
    keybind: Optional[str] = None
    hold: bool = Field(False, description="Hold the key rather than tap it (e.g. self destruct)")
    hold_duration: Optional[float] = Field(None, description="Seconds to hold (None = client default)")
    sequence: list[MacroStep] = Field(default_factory=list, description="Macro steps (if any)")
    confirm_required: bool = False
    response_text: str = ""
    audio: Optional[str] = Field(None, description="base64-encoded WAV, or null")
    audio_format: str = "wav"


class HealthResponse(BaseModel):
    status: str
    provider: str
    model: str
    llm_reachable: bool
    tts_ready: bool                    # ACK engine ready (command path is the critical one)
    tts_chat_ready: bool = True        # chat engine ready (chat/knowledge replies)
    tts_engine: str = "piper"          # "ack=<engine>,chat=<engine>" summary


# --- Command management (CRUD) ---------------------------------------------
class CommandModel(BaseModel):
    """A ship command as exposed by the /commands API and the GUI."""
    intent: str = Field(..., description="Unique intent id, e.g. shields_max")
    key: Optional[str] = Field(None, description="pydirectinput key or combo, e.g. '0', 'alt+y'")
    confirm_required: bool = False
    hold: bool = Field(False, description="Hold the key rather than tap it")
    hold_duration: Optional[float] = Field(None, description="Seconds to hold (None = client default)")
    sequence: list[MacroStep] = Field(default_factory=list, description="Macro steps (overrides key)")
    description: str = Field("", description="What this command does")
    examples: list[str] = Field(default_factory=list, description="Sample phrases (classifier training)")
    ack: str = Field("", description="Canned spoken acknowledgement")


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
    hold_duration: Optional[float] = None
    sequence: Optional[list[MacroStep]] = None
    description: Optional[str] = None
    examples: Optional[list[str]] = None
    ack: Optional[str] = None
