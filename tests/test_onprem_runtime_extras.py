import json
from datetime import date, timedelta
import pytest
from sqlalchemy import update
import license as lic
import app as appmod
import onprem as op


@pytest.fixture
def onprem(app):
    priv, pub = lic.generate_keypair()
    app.config.update(DEPLOYMENT_MODE="onprem", MACHINE_ID="m1", LICENSE_PUBLIC_KEY=pub)
    op.invalidate()
    yield app, priv
    app.config.update(DEPLOYMENT_MODE="saas")
    app.config.pop("LICENSE_PUBLIC_KEY", None)
    app.config.pop("ONPREM_STATE_FILE", None)
    op.invalidate()


def _text(priv, **over):
    p = {"license_id": "L1", "business_name": "Biz", "machine_id": "m1", "trial": False,
         "base": {"term": "lifetime", "expires_at": (date.today() + timedelta(days=3650)).isoformat()},
         "modules": {"network": {"term": "yearly", "expires_at": (date.today() + timedelta(days=300)).isoformat()}},
         "issued_at": "2026-09-30T00:00:00Z"}
    p.update(over)
    return lic.sign(p, priv)


def _other_worker_change(priv, bump):
    t = appmod.InstalledLicense
    vals = {"license_text": _text(priv, machine_id="other")}
    if bump:
        vals["revision"] = t.revision + 1
    appmod.db.session.execute(update(t).where(t.id == 1).values(**vals))
    appmod.db.session.commit()


def test_revision_bump_invalidates_cache_across_workers(onprem):
    app, priv = onprem
    op.store_license(_text(priv))
    assert op.current_state().state == "valid"
    _other_worker_change(priv, bump=True)
    assert op.current_state().state == "readonly"
    assert op.current_state().reason == "machine_mismatch"


def test_cache_still_used_without_revision_change(onprem):
    app, priv = onprem
    op.store_license(_text(priv))
    assert op.current_state().state == "valid"
    _other_worker_change(priv, bump=False)
    assert op.current_state().state == "valid"


def test_store_license_increments_revision(onprem):
    app, priv = onprem
    op.store_license(_text(priv))
    assert appmod.db.session.get(appmod.InstalledLicense, 1).revision == 1
    op.store_license(_text(priv))
    assert appmod.db.session.get(appmod.InstalledLicense, 1).revision == 2


def test_update_status_in_system_info(onprem, client, tmp_path):
    app, _ = onprem
    data = {"current": "1.2.3", "previous": "1.2.2",
            "last_update": {"status": "ok", "target": "1.2.3", "at": "2026-10-01T03:31:00Z", "message": ""}}
    f = tmp_path / "state.json"
    f.write_text(json.dumps(data))
    app.config["ONPREM_STATE_FILE"] = str(f)
    assert op.update_status() == data
    assert client.get("/api/system/info").get_json()["update"] == data


def test_update_status_missing_or_invalid(onprem, client, tmp_path):
    app, _ = onprem
    app.config["ONPREM_STATE_FILE"] = str(tmp_path / "nope.json")
    assert op.update_status() is None
    assert client.get("/api/system/info").get_json()["update"] is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    app.config["ONPREM_STATE_FILE"] = str(bad)
    assert op.update_status() is None
    assert client.get("/api/system/info").get_json()["update"] is None
