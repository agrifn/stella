"""The command registry: the single source of truth for ship commands.

Owns config/keybinds.json. Each command maps an LLM intent to a concrete key
plus metadata the model needs (description, optional example phrases) and the
safety flag (confirm_required). Supports CRUD with write-back so the GUI can
manage commands at runtime, and is thread-safe for use under the async server.

The LLM never chooses keybinds; it only classifies intent. This registry both
resolves the key (server-authoritative) AND feeds the LLM's prompt, so adding a
command here makes it executable and known to the model in one place.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 'chat' is a reserved intent meaning "not a ship command"; it has no keybind
# and cannot be added or deleted.
RESERVED_INTENTS = {"chat"}


class CommandError(ValueError):
    """Raised for invalid command operations (duplicate, missing, reserved)."""


@dataclass
class Command:
    intent: str
    key: Optional[str] = None
    confirm_required: bool = False
    hold: bool = False
    description: str = ""
    examples: list[str] = field(default_factory=list)
    # Macro: an ordered list of steps to run instead of a single key. Each step:
    # {"key": "f7", "hold": false, "taps": 1, "delay": 0.1}. When non-empty, this
    # command is a macro and `key` is ignored.
    sequence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"key": self.key, "confirm_required": self.confirm_required}
        if self.hold:
            d["hold"] = True
        d["description"] = self.description
        if self.examples:
            d["examples"] = list(self.examples)
        if self.sequence:
            d["sequence"] = [dict(s) for s in self.sequence]
        return d

    @staticmethod
    def from_spec(intent: str, spec: dict) -> "Command":
        return Command(
            intent=intent,
            key=spec.get("key"),
            confirm_required=bool(spec.get("confirm_required", False)),
            hold=bool(spec.get("hold", False)),
            description=spec.get("description", ""),
            examples=list(spec.get("examples", []) or []),
            sequence=list(spec.get("sequence", []) or []),
        )


class CommandRegistry:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.RLock()
        self._comment: str = ""
        self._commands: dict[str, Command] = {}
        self.reload()

    # -- persistence ------------------------------------------------------
    def reload(self) -> None:
        with self._lock:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._comment = data.get("_comment", "")
            self._commands = {
                intent: Command.from_spec(intent, spec)
                for intent, spec in data.get("keybinds", {}).items()
            }

    def _persist(self) -> None:
        # caller holds the lock
        out = {
            "_comment": self._comment,
            "keybinds": {c.intent: c.to_dict() for c in self._commands.values()},
        }
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
        tmp.replace(self._path)  # atomic on the same filesystem

    # -- reads ------------------------------------------------------------
    def resolve(self, intent: str) -> Optional[Command]:
        with self._lock:
            return self._commands.get(intent)

    def list(self) -> list[Command]:
        with self._lock:
            return list(self._commands.values())

    @property
    def intents(self) -> list[str]:
        with self._lock:
            return list(self._commands.keys())

    # -- writes -----------------------------------------------------------
    @staticmethod
    def _validate_intent_name(intent: str) -> None:
        intent = (intent or "").strip()
        if not intent:
            raise CommandError("intent name is required")
        if intent in RESERVED_INTENTS:
            raise CommandError(f"'{intent}' is reserved")
        if not all(ch.isalnum() or ch == "_" for ch in intent):
            raise CommandError("intent must be alphanumeric/underscore (e.g. shields_max)")

    def add(self, cmd: Command) -> Command:
        with self._lock:
            self._validate_intent_name(cmd.intent)
            if cmd.intent in self._commands:
                raise CommandError(f"command '{cmd.intent}' already exists")
            self._commands[cmd.intent] = cmd
            self._persist()
            return cmd

    def update(self, intent: str, **fields) -> Command:
        with self._lock:
            if intent not in self._commands:
                raise CommandError(f"command '{intent}' not found")
            cmd = self._commands[intent]
            for k, v in fields.items():
                if v is not None and hasattr(cmd, k) and k != "intent":
                    setattr(cmd, k, v)
            self._persist()
            return cmd

    def delete(self, intent: str) -> None:
        with self._lock:
            if intent in RESERVED_INTENTS:
                raise CommandError(f"'{intent}' is reserved")
            if intent not in self._commands:
                raise CommandError(f"command '{intent}' not found")
            del self._commands[intent]
            self._persist()
