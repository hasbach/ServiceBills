from datetime import datetime, timedelta, date
import pytest
import license as lic
import app as appmod
import modules


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
         "modules": {"network": {"term": "yearly", "expires_at": (date.today() + timedelta(days=300)).isoformat()}},
         "issued_at": "2026-09-30T00:00:00Z"}
    p.update(over)
    return lic.sign(p, priv)


def _admin(client):
    # On-prem has no /api/register; build tenant + admin directly (setup wizard is Task 6).
    t = appmod.Tenant(name="Biz", slug="biz", plan="pro")
    appmod.db.session.add(t); appmod.db.session.flush()
    u = appmod.User(username="own", role="admin", tenant_id=t.id); u.set_password("pw")
    appmod.db.session.add(u); appmod.db.session.commit()
    tok = client.post("/api/login", json={"username": "own", "password": "pw"}).get_json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, t


def test_no_license_is_readonly_and_blocks_writes(onprem, client):
    hdr, _ = _admin(client)
    r = client.post("/api/subscription_plans", headers=hdr, json={"name": "P", "price": 1, "billing_cycle": "monthly"})
    assert r.status_code == 403 and r.get_json() == {"license_readonly": True, "reason": "no_license"}
    assert client.get("/api/subscription_plans", headers=hdr).status_code == 200


def test_offline_file_unlocks_and_drives_modules(onprem, client):
    app, priv = onprem
    hdr, t = _admin(client)
    r = client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    assert r.status_code == 200 and r.get_json()["state"] == "valid"
    assert client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 1, "billing_cycle": "monthly"}).status_code in (200, 201)
    assert modules.enabled_for(t) == {"core", "office", "network"}


def test_machine_mismatch_file_rejected(onprem, client):
    app, priv = onprem
    hdr, _ = _admin(client)
    r = client.post("/api/license", headers=hdr, json={"license_file": _text(priv, machine_id="other")})
    assert r.status_code == 400


def test_clock_rollback_goes_readonly(onprem, client):
    app, priv = onprem
    hdr, _ = _admin(client)
    client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    row = appmod.db.session.get(appmod.InstalledLicense, 1)
    row.last_seen_at = datetime.utcnow() + timedelta(days=5)
    appmod.db.session.commit()
    import onprem as op; op.invalidate()
    assert op.current_state().reason == "clock_rollback"


def test_refresh_revoked(onprem, client, monkeypatch):
    app, priv = onprem
    hdr, _ = _admin(client)
    client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    class R:
        status_code = 403
        def json(self): return {"error": "revoked"}
        text = "revoked"
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: R())
    import onprem as op
    op.refresh_license()
    assert op.current_state().reason == "revoked"


def test_refresh_network_error_keeps_license(onprem, client, monkeypatch):
    app, priv = onprem
    hdr, _ = _admin(client)
    client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    def boom(*a, **k): raise appmod.requests.ConnectionError("offline")
    monkeypatch.setattr(appmod.requests, "post", boom)
    import onprem as op
    op.refresh_license()
    assert op.current_state().state == "valid"
    assert "ConnectionError" in appmod.db.session.get(appmod.InstalledLicense, 1).last_refresh_error


def test_system_info_reports_license(onprem, client):
    _admin(client)
    j = client.get("/api/system/info").get_json()
    assert j["deployment_mode"] == "onprem" and j["license"]["reason"] == "no_license"
