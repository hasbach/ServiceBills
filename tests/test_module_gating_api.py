import app as appmod
from flask_jwt_extended import jwt_required
from tests.conftest import make_tenant
from tenancy import require_module


# Registered at import time: Flask forbids add_url_rule after the first request.
@jwt_required()
@require_module("network")
def _probe():
    return "ok", 200


appmod.app.add_url_rule("/api/_probe_network", "_probe_network", _probe)


def _set_overrides(app, slug, overrides):
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug=slug).first()
        t.module_overrides = overrides
        appmod.db.session.commit()


def test_tenant_me_lists_modules(app, client):
    hdr = make_tenant(client, "Mod A", "moda_admin")
    mods = client.get("/api/tenant/me", headers=hdr).get_json()["modules"]
    assert mods == sorted(["core", "office", "whatsapp", "network", "upstream_sync"])


def test_tenant_me_reflects_overrides(app, client):
    hdr = make_tenant(client, "Mod B", "modb_admin")
    _set_overrides(app, "mod-b", {"network": False, "whish_payments": True})
    mods = client.get("/api/tenant/me", headers=hdr).get_json()["modules"]
    assert "network" not in mods and "whish_payments" in mods


def test_require_module_decorator(app, client):
    hdr = make_tenant(client, "Mod C", "modc_admin")
    assert client.get("/api/_probe_network", headers=hdr).status_code == 200
    _set_overrides(app, "mod-c", {"network": False})
    r = client.get("/api/_probe_network", headers=hdr)
    assert r.status_code == 403
    assert r.get_json() == {"msg": "Module not enabled", "module": "network"}
