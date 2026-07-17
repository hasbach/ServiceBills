import app as appmod
from tests.conftest import make_tenant
from tests.test_superadmin import _superadmin_headers


def _suspend(app, slug):
    with app.app_context():
        appmod.Tenant.query.filter_by(slug=slug).first().status = "suspended"
        appmod.db.session.commit()


def test_suspended_tenant_blocked_from_data_but_can_reach_billing(app, client):
    hdr = make_tenant(client, "Biz A", "a_admin")
    _suspend(app, "biz-a")
    # Data route is blocked with 402...
    assert client.get("/api/customers", headers=hdr).status_code == 402
    # ...but export and billing remain reachable so the tenant can pay/leave.
    assert client.get("/api/tenant/export", headers=hdr).status_code == 200
    # Billing checkout gets past the suspend gate (free plan -> 400, not 402).
    assert client.post("/api/billing/checkout", headers=hdr, json={"plan": "free"}).status_code == 400


def test_export_contains_tenant_data(client):
    hdr = make_tenant(client, "Biz E", "e_admin")
    pid = client.post("/api/subscription_plans", headers=hdr,
                      json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr,
                json={"name": "Cust", "phone": "1", "address": "a",
                      "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})
    export = client.get("/api/tenant/export", headers=hdr).get_json()
    assert any(c["name"] == "Cust" for c in export["customer"])
    assert len(export["subscription_plan"]) == 1


def test_superadmin_delete_removes_only_that_tenant(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    make_tenant(client, "Biz B", "b_admin")
    pid = client.post("/api/subscription_plans", headers=a,
                      json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    client.post("/api/customers", headers=a,
                json={"name": "Cust", "phone": "1", "address": "a",
                      "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})
    sa = _superadmin_headers(app, client)
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        b_tid = appmod.Tenant.query.filter_by(slug="biz-b").first().id

    assert client.delete(f"/api/admin/tenants/{a_tid}", headers=sa).status_code == 200
    with app.app_context():
        assert appmod.db.session.get(appmod.Tenant, a_tid) is None
        assert appmod.Customer.query.filter_by(tenant_id=a_tid).count() == 0
        assert appmod.User.query.filter_by(tenant_id=a_tid).count() == 0
        assert appmod.db.session.get(appmod.Tenant, b_tid) is not None   # B untouched
