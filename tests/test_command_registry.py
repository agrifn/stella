"""CommandRegistry is the single source of truth for keybinds, with CRUD +
atomic write-back. Tests cover the spec round-trip, intent-name validation, and
that add/update/delete persist to disk."""
import json

import pytest

from server.command_registry import Command, CommandError, CommandRegistry


def _make_registry(tmp_path):
    p = tmp_path / "keybinds.json"
    p.write_text(json.dumps({
        "_comment": "test",
        "keybinds": {
            "shields_max": {"key": "f7", "hold": True, "description": "max shields"},
            "eject": {"key": "altright+y", "confirm_required": True, "description": "eject"},
        },
    }), encoding="utf-8")
    return CommandRegistry(p), p


def test_spec_roundtrip():
    spec = {"key": "f7", "hold": True, "confirm_required": False,
            "description": "d", "examples": ["a", "b"], "sequence": [{"key": "f6"}]}
    cmd = Command.from_spec("x", spec)
    out = cmd.to_dict()
    assert out["key"] == "f7" and out["hold"] is True
    assert out["examples"] == ["a", "b"] and out["sequence"] == [{"key": "f6"}]


def test_to_dict_omits_empty_optionals():
    out = Command(intent="x", key="0").to_dict()
    assert "hold" not in out and "examples" not in out and "sequence" not in out


@pytest.mark.parametrize("bad", ["", "  ", "chat", "has space", "bad-dash"])
def test_invalid_intent_names(tmp_path, bad):
    reg, _ = _make_registry(tmp_path)
    with pytest.raises(CommandError):
        reg.add(Command(intent=bad, key="0"))


def test_resolve_and_list(tmp_path):
    reg, _ = _make_registry(tmp_path)
    assert reg.resolve("shields_max").key == "f7"
    assert reg.resolve("nope") is None
    assert set(reg.intents) == {"shields_max", "eject"}


def test_add_persists(tmp_path):
    reg, p = _make_registry(tmp_path)
    reg.add(Command(intent="boost", key="left_shift", hold=True))
    # a fresh registry reading the same file must see it (write-back happened)
    assert CommandRegistry(p).resolve("boost").key == "left_shift"


def test_add_duplicate_raises(tmp_path):
    reg, _ = _make_registry(tmp_path)
    with pytest.raises(CommandError):
        reg.add(Command(intent="shields_max", key="f7"))


def test_update_changes_field(tmp_path):
    reg, p = _make_registry(tmp_path)
    reg.update("shields_max", key="5", confirm_required=True)
    fresh = CommandRegistry(p).resolve("shields_max")
    assert fresh.key == "5" and fresh.confirm_required is True


def test_update_missing_raises(tmp_path):
    reg, _ = _make_registry(tmp_path)
    with pytest.raises(CommandError):
        reg.update("nope", key="5")


def test_delete(tmp_path):
    reg, p = _make_registry(tmp_path)
    reg.delete("eject")
    assert CommandRegistry(p).resolve("eject") is None


def test_delete_reserved_raises(tmp_path):
    reg, _ = _make_registry(tmp_path)
    with pytest.raises(CommandError):
        reg.delete("chat")
