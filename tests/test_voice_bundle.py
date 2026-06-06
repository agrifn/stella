"""Tests for voice-bundle export/import (sharing a voice between users).

These exercise TTSHandler.export_bundle / import_bundle with a temp voices dir and
fake voice files - no model, no network, no synthesis."""
import io
import zipfile

import pytest

from server.config import TTSConfig
from server.tts_handler import TTSHandler


def _handler(tmp_path):
    return TTSHandler(TTSConfig(voices_dir=tmp_path))


def _make_voice(d, name="myvoice", onnx=b"ONNXBYTES"):
    (d / f"{name}.onnx").write_bytes(onnx)
    (d / f"{name}.onnx.json").write_text('{"audio":{"sample_rate":22050}}', encoding="utf-8")


def test_export_import_round_trip(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    _make_voice(src, "myvoice", b"ONNXBYTES")
    bundle = _handler(src).export_bundle("myvoice")

    dst = tmp_path / "dst"; dst.mkdir()
    h2 = _handler(dst)
    name = h2.import_bundle(bundle)

    assert name == "myvoice"
    assert (dst / "myvoice.onnx").read_bytes() == b"ONNXBYTES"
    assert (dst / "myvoice.onnx.json").exists()
    assert "myvoice" in h2.available_voices()


def test_import_make_active_sets_voice(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    _make_voice(src, "cortana")
    bundle = _handler(src).export_bundle("cortana")
    dst = tmp_path / "dst"; dst.mkdir()
    h2 = _handler(dst)
    h2.import_bundle(bundle, make_active=True)
    assert h2.voice == "cortana"


def test_import_ignores_zip_member_paths(tmp_path):
    # A crafted zip with traversal paths must NOT write outside the voices dir;
    # only the basename is used and the install name is validated.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.onnx", b"X")
        z.writestr("../../evil.onnx.json", b"{}")
    dst = tmp_path / "d"; dst.mkdir()
    name = _handler(dst).import_bundle(buf.getvalue())
    assert name == "evil"
    assert (dst / "evil.onnx").exists()
    assert not (tmp_path / "evil.onnx").exists()  # nothing escaped the voices dir


def test_import_rejects_bad_zip(tmp_path):
    with pytest.raises(ValueError):
        _handler(tmp_path).import_bundle(b"this is not a zip")


def test_import_requires_json(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("v.onnx", b"X")  # no config
    with pytest.raises(ValueError):
        _handler(tmp_path).import_bundle(buf.getvalue())


def test_import_rejects_invalid_voice_name(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bad name.onnx", b"X")        # space -> invalid name
        z.writestr("bad name.onnx.json", b"{}")
    with pytest.raises(ValueError):
        _handler(tmp_path).import_bundle(buf.getvalue())


def test_export_unknown_voice_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        _handler(tmp_path).export_bundle("nope")
