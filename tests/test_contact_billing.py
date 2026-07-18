import app as appmod
from tests.conftest import make_tenant
from tests.test_superadmin import _superadmin_headers


def test_billing_config_defaults_to_contact_only(client):
    hdr = make_tenant(client, "Biz A", "a_admin")
    cfg = client.get("/api/billing/config", headers=hdr).get_json()
    assert cfg["stripe_enabled"] is False   # no Stripe keys in tests
    assert cfg["contact_enabled"] is True


def test_contact_request_then_superadmin_approves(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    make_tenant(client, "Biz B", "b_admin")

    r = client.post("/api/billing/contact", headers=a,
                    json={"plan": "pro", "name": "Al", "email": "al@a.com", "message": "please upgrade"})
    assert r.status_code == 201

    sa = _superadmin_headers(app, client)
    reqs = client.get("/api/admin/upgrade-requests", headers=sa).get_json()
    assert len(reqs) == 1
    assert reqs[0]["tenant_name"] == "Biz A"
    a_tid = reqs[0]["tenant_id"]

    # Approve -> tenant becomes Pro and the request is marked handled.
    assert client.post(f"/api/admin/tenants/{a_tid}/set-plan", headers=sa,
                       json={"plan": "pro"}).status_code == 200
    with app.app_context():
        assert appmod.db.session.get(appmod.Tenant, a_tid).plan == "pro"
    assert client.get("/api/admin/upgrade-requests", headers=sa).get_json() == []


def test_set_plan_rejects_unknown_plan(app, client):
    make_tenant(client, "Biz A", "a_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
    assert client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                       json={"plan": "platinum"}).status_code == 400


def test_contact_and_set_plan_require_proper_roles(client):
    hdr = make_tenant(client, "Biz A", "a_admin")
    # A tenant admin cannot hit the super-admin set-plan route.
    assert client.post("/api/admin/tenants/1/set-plan", headers=hdr, json={"plan": "pro"}).status_code == 403
    # ...nor list upgrade requests.
    assert client.get("/api/admin/upgrade-requests", headers=hdr).status_code == 403
