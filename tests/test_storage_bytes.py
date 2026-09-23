import pytest
import storage


def test_local_save_and_read_bytes_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    key = storage.save_bytes(b"\x00\x01voice", 7, "voice.ogg", "audio/ogg")
    assert key.startswith("7/") and key.endswith("-voice.ogg")
    assert storage.read_bytes(key) == b"\x00\x01voice"


def test_local_read_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    with pytest.raises(FileNotFoundError):
        storage.read_bytes("7/nope.bin")


def test_local_read_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    with pytest.raises(FileNotFoundError):
        storage.read_bytes("../../etc/passwd")
