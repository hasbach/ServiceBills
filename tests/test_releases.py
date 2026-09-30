import pytest

H = {"X-Release-Secret": "s3cret"}


@pytest.fixture
def secret(app, monkeypatch):
    monkeypatch.setenv("RELEASE_PUBLISH_SECRET", "s3cret")
    yield


def _pub(client, version, headers=H, **kw):
    body = {"version": version, "release_date": "2026-10-01"}
    body.update(kw)
    return client.post("/api/internal/releases", json=body, headers=headers)


def test_publish_unconfigured_503(client, monkeypatch):
    monkeypatch.delenv("RELEASE_PUBLISH_SECRET", raising=False)
    assert _pub(client, "1.0.0").status_code == 503


def test_publish_wrong_secret_401(client, secret):
    assert _pub(client, "1.0.0", headers={"X-Release-Secret": "nope"}).status_code == 401
    assert _pub(client, "1.0.0", headers={}).status_code == 401


@pytest.mark.parametrize("kw", [{"version": "1.2"}, {"version": "1.2.x"},
                                {"version": "1.0.0", "min_upgrade_from": "2"},
                                {"version": "1.0.0", "release_date": "nope"},
                                {"version": "1.0.0", "notes": 5}])
def test_publish_validation_400(client, secret, kw):
    body = {"version": "1.0.0", "release_date": "2026-10-01"}
    body.update(kw)
    assert client.post("/api/internal/releases", json=body, headers=H).status_code == 400


def test_latest_none_404(client):
    r = client.get("/api/updates/latest")
    assert r.status_code == 404 and r.get_json() == {"error": "no_release"}


def test_latest_is_highest_semver_and_upsert(client, secret):
    for v in ("1.2.0", "1.10.0", "1.9.3"):
        assert _pub(client, v).status_code == 201
    j = client.get("/api/updates/latest").get_json()
    assert j["version"] == "1.10.0"
    assert set(j) == {"version", "release_date", "notes", "min_upgrade_from"}
    r = _pub(client, "1.10.0", notes="new notes", min_upgrade_from="1.2.0")
    assert r.status_code == 200 and r.get_json()["notes"] == "new notes"
    j = client.get("/api/updates/latest").get_json()
    assert j["notes"] == "new notes" and j["min_upgrade_from"] == "1.2.0"


def test_download_without_installer_url_503(client, app, monkeypatch):
    monkeypatch.delenv("INSTALLER_URL", raising=False)
    app.config["INSTALLER_URL"] = None
    r = client.get("/download")
    assert r.status_code == 503 and r.get_json() == {"error": "installer not available"}


def test_download_redirects(client, app, monkeypatch):
    monkeypatch.setenv("INSTALLER_URL", "https://example.com/ServiceBills-Setup.exe")
    r = client.get("/download")
    assert r.status_code == 302 and r.headers["Location"] == "https://example.com/ServiceBills-Setup.exe"


def test_onprem_blocks_updates_and_download(app, client):
    app.config["DEPLOYMENT_MODE"] = "onprem"
    try:
        assert client.get("/api/updates/latest").status_code == 404
        assert client.get("/download").status_code == 404
    finally:
        app.config["DEPLOYMENT_MODE"] = "saas"
