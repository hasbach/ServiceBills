from datetime import datetime

import app as appmod
from tests.conftest import make_tenant


def _setup_bridged_customer(client, hdr, upstream_username="cust1", product="proradius"):
    plan_id = client.post("/api/subscription_plans", headers=hdr,
                          json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    provider_id = client.post("/api/upstream-providers", headers=hdr,
                              json={"name": "Terra", "product": product,
                                    "portal_url": "https://acppro.terra.net.lb/login/",
                                    "portal_username": "reseller1", "portal_password": "pw"}
                              ).get_json()["provider"]["id"]
    customer_resp = client.post("/api/customers", headers=hdr,
                                json={"name": "Cust", "phone": "1", "address": "a",
                                      "subscription_plan_id": plan_id,
                                      "subscription_start_date": "2026-01-01",
                                      "upstream_provider_id": provider_id,
                                      "upstream_username": upstream_username})
    return customer_resp.get_json()["customer_id"] if customer_resp.status_code in (200, 201) else None


def test_sync_requires_upstream_link(client):
    hdr = make_tenant(client, "Biz A", "a_admin")
    plan_id = client.post("/api/subscription_plans", headers=hdr,
                          json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    customer_id = client.post("/api/customers", headers=hdr,
                              json={"name": "Cust", "phone": "1", "address": "a",
                                    "subscription_plan_id": plan_id,
                                    "subscription_start_date": "2026-01-01"}).get_json()["customer_id"]

    resp = client.post(f"/api/customers/{customer_id}/upstream-status-sync", headers=hdr)

    assert resp.status_code == 400


def test_sync_success_persists_fields_and_returns_drift(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz B", "b_admin")
    customer_id = _setup_bridged_customer(client, hdr)
    assert customer_id is not None

    with app.app_context():
        customer = appmod.Customer.query.get(customer_id)
        customer.subscription_expiry_date = datetime(2026, 9, 1)
        appmod.db.session.commit()

    monkeypatch.setattr(
        appmod.upstream_portal, "get_subscriber_status",
        lambda provider, username: (True, {"status": "online", "expiry": datetime(2026, 9, 5)}),
    )

    resp = client.post(f"/api/customers/{customer_id}/upstream-status-sync", headers=hdr)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["upstream_last_status"] == "online"
    assert body["upstream_actual_expiry"] == "2026-09-05"
    assert body["upstream_drift"] == {"severity": "info", "days": 4}

    with app.app_context():
        customer = appmod.Customer.query.get(customer_id)
        assert customer.upstream_last_status == "online"
        assert customer.upstream_last_synced_at is not None


def test_sync_failure_returns_502_and_leaves_fields_untouched(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz C", "c_admin")
    customer_id = _setup_bridged_customer(client, hdr)
    assert customer_id is not None

    monkeypatch.setattr(appmod.upstream_portal, "get_subscriber_status",
                        lambda provider, username: (False, "auth_failed"))

    resp = client.post(f"/api/customers/{customer_id}/upstream-status-sync", headers=hdr)

    assert resp.status_code == 502
    assert resp.get_json() == {"ok": False, "error": "auth_failed"}

    with app.app_context():
        customer = appmod.Customer.query.get(customer_id)
        assert customer.upstream_last_status is None
        assert customer.upstream_last_synced_at is None


def test_customer_list_includes_upstream_drift(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz D", "d_admin")
    customer_id = _setup_bridged_customer(client, hdr)
    assert customer_id is not None

    with app.app_context():
        customer = appmod.Customer.query.get(customer_id)
        customer.subscription_expiry_date = datetime(2026, 9, 1)
        customer.upstream_actual_expiry = datetime(2026, 8, 30)
        customer.upstream_last_status = "expired"
        appmod.db.session.commit()

    resp = client.get("/api/customers", headers=hdr)

    assert resp.status_code == 200
    listed = [c for c in resp.get_json()["customers"] if c["id"] == customer_id][0]
    assert listed["upstream_last_status"] == "expired"
    assert listed["upstream_drift"] == {"severity": "alert", "days": 2}


def test_sync_dispatches_to_krypton_adapter_for_krypton_product(client, monkeypatch):
    hdr = make_tenant(client, "Biz E", "e_admin")
    customer_id = _setup_bridged_customer(client, hdr, product="krypton")
    assert customer_id is not None

    monkeypatch.setattr(
        appmod.upstream_portal_krypton, "get_subscriber_status",
        lambda provider, username: (True, {"status": "online", "expiry": None}),
    )
    monkeypatch.setattr(
        appmod.upstream_portal, "get_subscriber_status",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("PROradius adapter must not be called for a krypton provider")),
    )

    resp = client.post(f"/api/customers/{customer_id}/upstream-status-sync", headers=hdr)

    assert resp.status_code == 200
    assert resp.get_json()["upstream_last_status"] == "online"
