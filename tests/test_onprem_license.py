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


def test_last_seen_bump_does_not_commit_callers_session(onprem, client):
    app, priv = onprem
    hdr, _ = _admin(client)
    client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    import onprem as op
    row = appmod.db.session.get(appmod.InstalledLicense, 1)
    row.last_seen_at = datetime.utcnow() - timedelta(hours=1)
    appmod.db.session.commit()
    op.invalidate()
    appmod.db.session.add(appmod.Tenant(name="Ghost", slug="ghost", plan="pro"))
    assert op.current_state().state == "valid"
    appmod.db.session.rollback()
    assert appmod.Tenant.query.filter_by(slug="ghost").count() == 0
    assert appmod.db.session.get(appmod.InstalledLicense, 1).last_seen_at > datetime.utcnow() - timedelta(minutes=5)


def test_revocation_is_sticky_for_same_license_file(onprem, client):
    app, priv = onprem
    hdr, _ = _admin(client)
    text = _text(priv)
    client.post("/api/license", headers=hdr, json={"license_file": text})
    row = appmod.db.session.get(appmod.InstalledLicense, 1)
    row.revoked = True
    appmod.db.session.commit()
    import onprem as op
    op.invalidate()
    r = client.post("/api/license", headers=hdr, json={"license_file": text})
    assert r.status_code == 400 and r.get_json() == {"msg": "revoked"}
    assert op.current_state().reason == "revoked"
    r = client.post("/api/license", headers=hdr, json={"license_file": _text(priv, license_id="L2")})
    assert r.status_code == 200 and r.get_json()["state"] == "valid"


def test_server_issued_license_clears_revocation(onprem, client):
    app, priv = onprem
    hdr, _ = _admin(client)
    text = _text(priv)
    import onprem as op
    op.store_license(text)
    appmod.db.session.get(appmod.InstalledLicense, 1).revoked = True
    appmod.db.session.commit()
    assert op.store_license(text, from_server=True).state == "valid"


def test_non_string_license_fields_400(onprem, client):
    hdr, _ = _admin(client)
    for body in ({"license_file": 123}, {"license_file": {"a": 1}}, {"license_key": 5}, {"license_key": {"x": 1}}):
        r = client.post("/api/license", headers=hdr, json=body)
        assert r.status_code == 400 and r.get_json() == {"msg": "invalid_license"}


def test_readonly_allowlist_exact_and_writes_blocked(onprem, client):
    app, priv = onprem
    hdr, _ = _admin(client)
    r = client.post("/api/license", headers=hdr, json={"license_file": _text(priv)})
    assert r.status_code == 200
    import onprem as op
    appmod.db.session.get(appmod.InstalledLicense, 1).revoked = True
    appmod.db.session.commit()
    op.invalidate()
    for m in (client.put, client.delete):
        r = m("/api/subscription_plans/1", headers=hdr, json={})
        assert r.status_code == 403 and r.get_json() == {"license_readonly": True, "reason": "revoked"}
    r = client.post("/api/licensex", headers=hdr, json={})
    assert r.status_code == 403 and r.get_json().get("license_readonly") is True


def test_readonly_hook_skipped_without_tenant(onprem, client):
    r = client.post("/api/subscription_plans", json={"name": "P"})
    assert "license_readonly" not in (r.get_json(silent=True) or {})
