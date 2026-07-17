import io
import storage


class _FS:
    """Minimal werkzeug-FileStorage stand-in."""
    def __init__(self, name, data):
        self.filename = name
        self._data = data

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self._data)


def test_keys_are_tenant_namespaced_and_distinct(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())

    k1 = storage.save(_FS("logo.png", b"a"), tenant_id=1)
    k2 = storage.save(_FS("logo.png", b"b"), tenant_id=2)

    assert k1.startswith("1/") and k2.startswith("2/")   # tenant-namespaced
    assert k1 != k2                                       # same filename -> distinct keys
    assert storage.url(k1) == f"/uploads/{k1}"
    # File actually written under the tenant prefix.
    assert (tmp_path / k1).read_bytes() == b"a"


def test_manifest_is_public(client):
    r = client.get("/manifest.json")
    assert r.status_code == 200
    assert r.get_json()["name"] == "servicesBills"
