import pytest

import app as appmod
from tests.conftest import make_tenant
from tests.test_admin_plan_grant import _superadmin_headers


@pytest.fixture
def sa_headers(app, client):
    return _superadmin_headers(app, client)


def _tid(app, slug):
    with app.app_context():
        return appmod.Tenant.query.filter_by(slug=slug).first().id


def test_set_and_clear_override(app, client, sa_headers):
    make_tenant(client, "Adm M", "admm_admin")
    tid = _tid(app, "adm-m")
    r = client.post(f"/api/admin/tenants/{tid}/modules", headers=sa_headers,
                    json={"overrides": {"ai_cs": True, "network": False}})
    assert r.status_code == 200
    body = r.get_json()["tenant"]
    assert "ai_cs" in body["modules"] and "network" not in body["modules"]
    assert body["module_overrides"] == {"ai_cs": True, "network": False}
    r = client.post(f"/api/admin/tenants/{tid}/modules", headers=sa_headers,
                    json={"overrides": {"network": None}})
    assert r.get_json()["tenant"]["module_overrides"] == {"ai_cs": True}


def test_rejects_always_on_and_unknown(app, client, sa_headers):
    make_tenant(client, "Adm N", "admn_admin")
    tid = _tid(app, "adm-n")
    for bad in ({"core": False}, {"bogus": True}, {"network": "yes"}):
        assert client.post(f"/api/admin/tenants/{tid}/modules", headers=sa_headers,
                           json={"overrides": bad}).status_code == 400


def test_unknown_tenant_404(app, client, sa_headers):
    assert client.post("/api/admin/tenants/99999/modules", headers=sa_headers,
                       json={"overrides": {}}).status_code == 404


def test_tenant_admin_cannot_call(app, client):
    hdr = make_tenant(client, "Adm O", "admo_admin")
    tid = _tid(app, "adm-o")
    assert client.post(f"/api/admin/tenants/{tid}/modules", headers=hdr,
                       json={"overrides": {"ai_cs": True}}).status_code == 403


def test_admin_tenant_list_has_overrides(app, client, sa_headers):
    make_tenant(client, "Adm P", "admp_admin")
    rows = client.get("/api/admin/tenants", headers=sa_headers).get_json()
    rows = rows if isinstance(rows, list) else rows.get("tenants", [])
    row = next(r for r in rows if r["slug"] == "adm-p")
    assert row["module_overrides"] == {} and "modules" in row
