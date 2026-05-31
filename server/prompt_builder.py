"""Builds the LLM system prompt from a static preamble + the live command list.

The command list is generated from the registry, so adding/editing/removing a
command is reflected immediately. To keep the prompt compact (it must fit num_ctx
alongside many commands), each intent is listed with its description and a few
inline example phrases, plus a handful of static format examples - rather than a
full few-shot pair per phrase, which would not scale.
"""
from __future__ import annotations

from pathlib import Path

from .command_registry import CommandRegistry

# Static examples that only teach the JSON SHAPE (not specific intents).
_FORMAT_EXAMPLES = [
    'Input: "put all power to shields"\n'
    'Output: {"intent":"shields_power_max","confirm_required":false,"response_text":"Diverting power to shields."}',
    'Input: "eject eject eject"\n'
    'Output: {"intent":"eject","confirm_required":true,"response_text":"Confirm ejection? This cannot be undone."}',
    'Input: "what is our heading"\n'
    'Output: {"intent":"chat","confirm_required":false,"response_text":"Check the HUD compass for heading."}',
]


class PromptBuilder:
    def __init__(self, preamble_path: Path):
        self._preamble = preamble_path.read_text(encoding="utf-8").rstrip()

    def build(self, registry: CommandRegistry) -> str:
        lines = [self._preamble, "", "Valid intents (intent: description; example phrases):"]
        for cmd in registry.list():
            desc = cmd.description or cmd.intent
            danger = " [DANGEROUS-confirm]" if cmd.confirm_required else ""
            hints = ""
            if cmd.examples:
                hints = "  e.g. " + ", ".join(f'"{p}"' for p in cmd.examples[:4])
            lines.append(f"- {cmd.intent}: {desc}{danger}{hints}")
        lines.append('- chat: not a ship command, or a question/conversation')
        lines += ["", "Examples:", *_FORMAT_EXAMPLES]
        return "\n".join(lines)
