"""Deleting a payment must clear rows that FK-reference it but aren't
ORM-cascaded with it (GeneratedReceipt, AddonPurchase), or the delete raises
a ForeignKeyViolation on Postgres. SQLite (used here) doesn't enforce FKs by
default, so these tests can't reproduce the original crash -- they instead
verify _detach_payment_dependents' behavior directly: the delete succeeds,
and each dependent row ends up in the expected post-delete state.
"""
import json
from datetime import datetime

import app as appmod
from tests.conftest import make_tenant


def _make_plan(client, hdr, price=50):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "Basic", "price": price, "billing_cycle": "monthly"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["plan"]["id"]


def _make_customer(client, hdr, plan_id):
    r = client.post("/api/customers", headers=hdr,
                    json={"name": "Cust", "phone": "111", "address": "addr",
                          "subscription_plan_id": plan_id, "subscription_start_date": "2026-01-01"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["customer_id"]


def _unpaid_payment_id(client, hdr, customer_id):
    payments = client.get("/api/payments", headers=hdr,
                          query_string={"customer_id": customer_id}).get_json()["payments"]
    return next(p["id"] for p in payments if not p["paid"])


def test_delete_payment_cascades_its_generated_receipt(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        receipt = appmod.GeneratedReceipt(
            tenant_id=tenant_id, customer_id=cust_id, payment_id=payment_id,
            billing_date=datetime.utcnow(), receipt_data=json.dumps({"ok": True}),
        )
        appmod.db.session.add(receipt)
        appmod.db.session.commit()
        receipt_id = receipt.id

    r = client.delete(f"/api/payments/{payment_id}", headers=a)
    assert r.status_code == 200, r.get_data(as_text=True)

    with app.app_context():
        assert appmod.db.session.get(appmod.Payment, payment_id) is None
        assert appmod.db.session.get(appmod.GeneratedReceipt, receipt_id) is None


def test_delete_payment_unlinks_its_addon_purchase(app, client):
    a = make_tenant(client, "Biz A", "a_admin2")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        addon = appmod.AddonPurchase(
            tenant_id=tenant_id, customer_id=cust_id, payment_id=payment_id,
            description="Extra router", amount=25.0,
        )
        appmod.db.session.add(addon)
        appmod.db.session.commit()
        addon_id = addon.id

    r = client.delete(f"/api/payments/{payment_id}", headers=a)
    assert r.status_code == 200, r.get_data(as_text=True)

    with app.app_context():
        assert appmod.db.session.get(appmod.Payment, payment_id) is None
        survived = appmod.db.session.get(appmod.AddonPurchase, addon_id)
        assert survived is not None, "AddonPurchase should survive its payment's deletion"
        assert survived.payment_id is None, "AddonPurchase should be unlinked, not left dangling"


def test_bulk_delete_payments_cascades_generated_receipts(app, client):
    a = make_tenant(client, "Biz A", "a_admin3")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        receipt = appmod.GeneratedReceipt(
            tenant_id=tenant_id, customer_id=cust_id, payment_id=payment_id,
            billing_date=datetime.utcnow(), receipt_data=json.dumps({"ok": True}),
        )
        appmod.db.session.add(receipt)
        appmod.db.session.commit()
        receipt_id = receipt.id

    r = client.post("/api/payments/bulk_delete", headers=a, json={"payment_ids": [payment_id]})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["succeeded"] == [payment_id]

    with app.app_context():
        assert appmod.db.session.get(appmod.GeneratedReceipt, receipt_id) is None
