from datetime import timedelta, date
import pytest
import license as lic
import app as appmod


@pytest.fixture
def onprem(app):
    priv, pub = lic.generate_keypair()
    app.config.update(DEPLOYMENT_MODE="onprem", MACHINE_ID="m1", LICENSE_PUBLIC_KEY=pub)
    import onprem as op
    op.invalidate()
    yield app, priv
    app.config.update(DEPLOYMENT_MODE="saas")
    app.config.pop("LICENSE_PUBLIC_KEY", None)
    op.invalidate()


def _text(priv, **over):
    p = {"license_id": "L1", "business_name": "Biz", "machine_id": "m1", "trial": False,
         "base": {"term": "lifetime", "expires_at": (date.today() + timedelta(days=3650)).isoformat()},
         "modules": {}, "issued_at": "2026-09-30T00:00:00Z"}
    p.update(over)
    return lic.sign(p, priv)


def _body(**over):
    b = {"business_name": "Acme ISP", "username": "boss", "password": "secret12",
         "owner_phone": "+9611", "mode": "file"}
    b.update(over)
    return b


class Resp:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, str(data)

    def json(self):
        return self._d


def test_before_setup_gates_api(onprem, client):
    r = client.get("/api/customers")
    assert r.status_code == 409 and r.get_json() == {"setup_required": True}
    assert client.get("/api/setup/status").get_json()["setup_required"] is True
    assert client.get("/api/system/info").get_json()["setup_required"] is True


def test_setup_with_file(onprem, client):
    app, priv = onprem
    r = client.post("/api/setup", json=_body(license_file=_text(priv)))
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["license"]["state"] == "valid"
    tok = client.post("/api/login", json={"username": "boss", "password": "secret12"}).get_json()["access_token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    assert appmod.Tenant.query.first().plan == "pro"
    assert client.get("/api/license", headers=hdr).get_json()["state"] == "valid"
    assert client.get("/api/setup/status").get_json()["setup_required"] is False
    assert client.get("/api/system/info").get_json()["setup_required"] is False
    bs = appmod.BusinessSettings.query.filter_by(tenant_id=appmod.Tenant.query.first().id).first()
    assert bs is not None and bs.business_name == _body()["business_name"]


def test_second_setup_rejected(onprem, client):
    app, priv = onprem
    assert client.post("/api/setup", json=_body(license_file=_text(priv))).status_code == 201
    r = client.post("/api/setup", json=_body(username="x", license_file=_text(priv)))
    assert r.status_code == 409 and r.get_json()["msg"] == "Setup already completed"


def test_setup_trial(onprem, client, monkeypatch):
    app, priv = onprem
    calls = []

    def fake(url, json=None, timeout=None, **k):
        calls.append((url, json))
        return Resp(201, {"license": _text(priv, trial=True)})
    monkeypatch.setattr(appmod.requests, "post", fake)
    r = client.post("/api/setup", json=_body(mode="trial"))
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["license"]["trial"] is True
    assert calls[0][0].endswith("/api/licenses/trial")
    assert calls[0][1]["machine_id"] == "m1" and calls[0][1]["business_name"] == "Acme ISP"


def test_trial_already_used(onprem, client, monkeypatch):
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: Resp(409, {"error": "trial_already_used"}))
    r = client.post("/api/setup", json=_body(mode="trial"))
    assert r.status_code == 409 and r.get_json()["msg"] == "trial_already_used"
    assert appmod.Tenant.query.count() == 0


def test_trial_network_error(onprem, client, monkeypatch):
    def boom(*a, **k):
        raise appmod.requests.ConnectionError("offline")
    monkeypatch.setattr(appmod.requests, "post", boom)
    r = client.post("/api/setup", json=_body(mode="trial"))
    assert r.status_code == 502
    assert appmod.Tenant.query.count() == 0


def test_wrong_machine_file_leaves_no_tenant(onprem, client):
    app, priv = onprem
    r = client.post("/api/setup", json=_body(license_file=_text(priv, machine_id="other")))
    assert r.status_code == 400
    assert appmod.Tenant.query.count() == 0
    assert appmod.User.query.count() == 0
    assert appmod.InstalledLicense.query.count() == 0


def test_missing_fields(onprem, client):
    assert client.post("/api/setup", json={"mode": "file"}).status_code == 400


def test_setup_refuses_without_machine_id(onprem, client):
    app, priv = onprem
    app.config["MACHINE_ID"] = None
    r = client.post("/api/setup", json=_body(license_file=_text(priv)))
    assert r.status_code == 400 and r.get_json() == {"msg": "machine_id_missing"}
    assert appmod.Tenant.query.count() == 0


def test_setup_password_min_length(onprem, client):
    app, priv = onprem
    r = client.post("/api/setup", json=_body(password="12345", license_file=_text(priv)))
    assert r.status_code == 400
    assert appmod.Tenant.query.count() == 0


def test_setup_concurrent_tenant_aborts(onprem, client, monkeypatch):
    app, priv = onprem
    import onprem as op
    orig = op.store_license

    def racing(*a, **k):
        # a second setup slipped a tenant in between our first check and our commit
        appmod.db.session.add(appmod.Tenant(name="Racer", slug="racer", plan="pro"))
        appmod.db.session.flush()
        return orig(*a, **k)
    monkeypatch.setattr(op, "store_license", racing)
    r = client.post("/api/setup", json=_body(license_file=_text(priv)))
    assert r.status_code == 409
    assert appmod.Tenant.query.count() == 0
