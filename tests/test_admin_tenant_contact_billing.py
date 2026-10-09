"""Super-admin page: tenant contact details (signup mobile, settings mobile,
admin email) and the Billing tab's cross-tenant payment list (Whish checkout
attempts + payments recorded manually on a Pro grant)."""
from datetime import datetime

import app as appmod
from tests.conftest import make_tenant
from tests.test_admin_plan_grant import _superadmin_headers


def _tid(app, slug):
    with app.app_context():
        return appmod.Tenant.query.filter_by(slug=slug).first().id


def test_register_stores_mobile_and_admin_list_shows_contact(app, client):
    r = client.post("/api/register", json={"username": "acme_admin", "password": "pw",
                                           "business_name": "Acme", "email": "boss@acme.com",
                                           "mobile": " +961 70 123 456 "})
    assert r.status_code == 201
    sa = _superadmin_headers(app, client)

    rows = client.get("/api/admin/tenants", headers=sa).get_json()
    acme = next(t for t in rows if t["slug"] == "acme")
    assert acme["contact_phone"] == "+961 70 123 456"
    assert acme["admins"] == [{"username": "acme_admin", "email": "boss@acme.com"}]
    assert acme["settings_mobile"] is None
    assert acme["created_at"].endswith("Z")


def test_register_without_mobile_still_works(client):
    r = client.post("/api/register", json={"username": "u", "password": "pw"})
    assert r.status_code == 201


def test_register_rejects_overlong_mobile(client):
    r = client.post("/api/register", json={"username": "u", "password": "pw", "mobile": "1" * 31})
    assert r.status_code == 400


def test_grant_with_payment_records_it_and_lists_it(app, client):
    make_tenant(client, "Delta Net", "delta_admin")
    sa = _superadmin_headers(app, client)
    tid = _tid(app, "delta-net")

    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa, json={
        "plan": "pro", "duration": "1_month",
        "payment": {"amount": 120, "method": "transfer", "note": "BOB ref 991"}})
    assert r.status_code == 200

    rows = client.get("/api/admin/billing/payments", headers=sa).get_json()
    assert len(rows) == 1
    p = rows[0]
    assert p["source"] == "manual" and p["tenant_name"] == "Delta Net"
    assert p["amount"] == 120 and p["method"] == "transfer" and p["period"] == "1_month"
    assert p["note"] == "BOB ref 991" and p["recorded_by"] == "root"
    assert p["status"] == "succeeded"


def test_grant_with_bad_payment_changes_nothing(app, client):
    make_tenant(client, "Delta Net", "delta_admin")
    sa = _superadmin_headers(app, client)
    tid = _tid(app, "delta-net")

    for bad in ({"amount": 0}, {"amount": "abc"}, {"amount": 10, "method": "bitcoin"}):
        r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                        json={"plan": "pro", "duration": "1_month", "payment": bad})
        assert r.status_code == 400, bad
    r = client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                    json={"plan": "free", "payment": {"amount": 5}})
    assert r.status_code == 400

    with app.app_context():
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "free" and t.plan_expires_at is None
        assert appmod.PlatformPayment.query.count() == 0


def test_grant_without_payment_records_nothing(app, client):
    make_tenant(client, "Delta Net", "delta_admin")
    sa = _superadmin_headers(app, client)
    tid = _tid(app, "delta-net")
    client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa, json={"plan": "pro", "duration": "1_month"})
    assert client.get("/api/admin/billing/payments", headers=sa).get_json() == []


def test_payments_list_includes_whish_attempts_newest_first(app, client):
    make_tenant(client, "Delta Net", "delta_admin")
    sa = _superadmin_headers(app, client)
    tid = _tid(app, "delta-net")
    with app.app_context():
        appmod.db.session.add_all([
            appmod.BillingPaymentAttempt(tenant_id=tid, billing_cycle="monthly", amount=120, currency="USD",
                                         whish_external_id="a1", callback_token="t", status="failed",
                                         created_at=datetime(2026, 9, 1)),
            appmod.BillingPaymentAttempt(tenant_id=tid, billing_cycle="yearly", amount=1000, currency="USD",
                                         whish_external_id="a2", callback_token="t", status="succeeded",
                                         created_at=datetime(2026, 10, 1), completed_at=datetime(2026, 10, 1)),
        ])
        appmod.db.session.commit()

    rows = client.get("/api/admin/billing/payments", headers=sa).get_json()
    assert [(r["source"], r["status"], r["period"]) for r in rows] == [
        ("whish", "succeeded", "yearly"), ("whish", "failed", "monthly")]
    assert rows[0]["created_at"] == "2026-10-01T00:00:00Z"


def test_payments_list_is_superadmin_only(app, client):
    hdr = make_tenant(client, "Delta Net", "delta_admin")
    assert client.get("/api/admin/billing/payments", headers=hdr).status_code == 403


def test_deleting_tenant_removes_its_manual_payments(app, client):
    make_tenant(client, "Delta Net", "delta_admin")
    sa = _superadmin_headers(app, client)
    tid = _tid(app, "delta-net")
    client.post(f"/api/admin/tenants/{tid}/set-plan", headers=sa,
                json={"plan": "pro", "duration": "1_month", "payment": {"amount": 120}})
    assert client.delete(f"/api/admin/tenants/{tid}", headers=sa).status_code == 200
    with app.app_context():
        assert appmod.PlatformPayment.query.count() == 0
