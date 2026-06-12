"""In-process intent resolution - classification without the HTTP hop.

The embedding classifier is CPU/numpy-only and the keybinds config lives on this
machine, so the client can classify intent itself instead of POSTing /command to
the Docker backend. That removes a fresh TCP handshake through the WSL2 port
forward from the action path on every utterance; the server keeps /command for
the GUI, tools, and as a fallback, plus /speak (TTS stays HTTP, off the action
path) and the /commands CRUD.

Reuses the server's own modules directly (server.intent_classifier and
server.command_registry import nothing beyond numpy/model2vec/stdlib), so the
classification logic literally cannot drift between the two paths. The client is
launched as `python -m client.app` from the repo root, which puts the root on
sys.path the same way conftest.py does for tests.

Hot reload: the keybinds file is stat'ed before every classify; an mtime change
(GUI edit through the server, or a hand edit) triggers a registry reload and a
classifier rebuild. A malformed mid-edit JSON keeps serving the previous
classifier and logs a warning (fail soft) - this also fixes the old gap where
hand edits to keybinds.json were never picked up until restart.
"""
from __future__ import annotations

import logging
from pathlib import Path

from server.command_registry import CommandRegistry
from server.intent_classifier import CHAT, IntentClassifier

from .command_sender import CommandResult

log = logging.getLogger("stella.intent")


class LocalIntentResolver:
    def __init__(self, keybinds_path: Path, model_name: str,
                 reject_threshold: float, clarify_threshold: float):
        self._path = Path(keybinds_path)
        self._model_name = model_name
        self._reject = reject_threshold
        self._clarify = clarify_threshold
        self._registry = CommandRegistry(self._path)
        self._classifier = self._build()
        self._mtime = self._path.stat().st_mtime
        log.info("local intent ready: %d commands, model=%s",
                 len(self._registry.intents), model_name)

    def _build(self) -> IntentClassifier:
        # Same construction as server/main.py._build_classifier: the classifier
        # only needs each command's example phrases.
        cmds = {c.intent: {"examples": c.examples} for c in self._registry.list()}
        return IntentClassifier(cmds, model_name=self._model_name,
                                reject_threshold=self._reject)

    def _maybe_reload(self) -> None:
        """Rebuild registry + classifier if keybinds.json changed on disk. The
        attempted mtime is recorded even on failure so a permanently broken file
        warns once per save, not once per utterance; the next save retriggers."""
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return  # file briefly missing (atomic replace in flight): keep serving
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._registry.reload()
            self._classifier = self._build()
            log.info("keybinds.json changed: classifier rebuilt (%d commands)",
                     len(self._registry.intents))
        except Exception:  # noqa: BLE001 - mid-edit JSON must not kill the loop
            log.warning("keybinds.json reload failed; keeping previous classifier",
                        exc_info=True)

    def resolve(self, text: str, candidates: list[str] | None = None) -> CommandResult:
        """Classify text (plus optional ASR n-best alternates) and resolve the
        winning intent against the registry. Mirrors the server's /command handler
        exactly; audio is always None because the engine fetches TTS via /speak."""
        self._maybe_reload()
        texts = [text, *(candidates or [])]
        intent, conf, chosen = self._classifier.classify_best(texts)
        # Borderline command (score in the gray zone): tell the engine to ask
        # "Say again?" rather than firing a guess. Rule hits score 1.0, never clarify.
        clarify = intent != CHAT and conf < self._clarify

        bind = self._registry.resolve(intent)
        return CommandResult(
            intent=intent,
            keybind=bind.key if bind else None,
            hold=bind.hold if bind else False,
            confirm_required=bind.confirm_required if bind else False,
            response_text=bind.ack if bind else "",
            audio_b64=None,
            sequence=bind.sequence if bind else [],
            hold_duration=bind.hold_duration if bind else None,
            confidence=conf,
            clarify=clarify,
            chosen_text=chosen,
        )
