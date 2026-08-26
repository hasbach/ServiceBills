"""Tests for the Phase 1 critical security/integrity hotfixes:
1) server-side rejection of negative/zero/non-numeric payment amounts, plus
   the new dedicated refund endpoint
2) admin/finance lockdown of previously-ungated routes
"""
from tests.conftest import make_tenant


def _make_plan(client, hdr, name="Basic", price=50):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": name, "price": price, "billing_cycle": "monthly"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["plan"]["id"]


def _make_customer(client, hdr, plan_id, name="Cust"):
    r = client.post("/api/customers", headers=hdr,
                    json={"name": name, "phone": "111", "address": "addr",
                          "subscription_plan_id": plan_id,
                          "subscription_start_date": "2026-01-01"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["customer_id"]


def _unpaid_payment_id(client, hdr, customer_id):
    payments = client.get("/api/payments", headers=hdr,
                          query_string={"customer_id": customer_id}).get_json()["payments"]
    return next(p["id"] for p in payments if not p["paid"])


def _add_collector(client, admin_hdr, username):
    client.post("/api/users", headers=admin_hdr,
               json={"username": username, "password": "pw", "role": "collector"})
    r = client.post("/api/login", json={"username": username, "password": "pw"})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


# --- 1) Financial validation --------------------------------------------

def test_add_payment_rejects_negative_amount(app, client):
    a = make_tenant(client, "Biz A", "a_negpay1")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)

    r = client.post("/api/payments", headers=a,
                    json={"customer_id": cust_id, "amount": -10, "reason": "manual"})
    assert r.status_code == 400


def test_add_payment_rejects_zero_amount(app, client):
    a = make_tenant(client, "Biz A", "a_negpay2")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)

    r = client.post("/api/payments", headers=a,
                    json={"customer_id": cust_id, "amount": 0, "reason": "manual"})
    assert r.status_code == 400


def test_add_payment_rejects_non_numeric_amount(app, client):
    a = make_tenant(client, "Biz A", "a_negpay3")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)

    r = client.post("/api/payments", headers=a,
                    json={"customer_id": cust_id, "amount": "not-a-number", "reason": "manual"})
    assert r.status_code == 400


def test_add_customer_rejects_negative_discount(app, client):
    a = make_tenant(client, "Biz A", "a_negdisc1")
    plan = _make_plan(client, a)
    r = client.post("/api/customers", headers=a,
                    json={"name": "Cust", "phone": "111", "address": "addr",
                          "subscription_plan_id": plan,
                          "subscription_start_date": "2026-01-01",
                          "discount": -5})
    assert r.status_code == 400


def test_update_customer_rejects_negative_cost_override(app, client):
    a = make_tenant(client, "Biz A", "a_negcost1")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    r = client.put(f"/api/customers/{cust_id}", headers=a, json={"cost_override": -1})
    assert r.status_code == 400


# --- 1) New refund endpoint ----------------------------------------------

def test_refund_requires_reason(app, client):
    a = make_tenant(client, "Biz A", "a_refund1")
    plan = _make_plan(client, a, price=80)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})

    r = client.post(f"/api/payments/{payment_id}/refund", headers=a, json={"reason": "  "})
    assert r.status_code == 400


def test_refund_requires_admin_or_finance_role(app, client):
    a = make_tenant(client, "Biz A", "a_refund2")
    plan = _make_plan(client, a, price=80)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})

    collector_hdr = _add_collector(client, a, "collector_refund2")
    r = client.post(f"/api/payments/{payment_id}/refund", headers=collector_hdr,
                    json={"reason": "customer complaint"})
    assert r.status_code == 403


def test_refund_issues_a_distinct_auditable_record_and_debits_balance(app, client):
    a = make_tenant(client, "Biz A", "a_refund3")
    plan = _make_plan(client, a, price=80)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    paid = client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})
    balance_after_pay = paid.get_json()["customer_new_balance"]

    r = client.post(f"/api/payments/{payment_id}/refund", headers=a,
                    json={"amount": 30, "reason": "partial service outage credit"})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body["amount"] == 30
    assert body["customer_new_balance"] == balance_after_pay - 30

    payments = client.get("/api/payments", headers=a,
                          query_string={"customer_id": cust_id}).get_json()["payments"]
    refund_row = next(p for p in payments if p["id"] == body["refund_payment_id"])
    assert refund_row["amount"] == 30
    assert refund_row["reason"] == f"Refund for payment #{payment_id}"


def test_refund_rejects_amount_exceeding_original_payment(app, client):
    a = make_tenant(client, "Biz A", "a_refund4")
    plan = _make_plan(client, a, price=50)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})

    r = client.post(f"/api/payments/{payment_id}/refund", headers=a,
                    json={"amount": 999, "reason": "too much"})
    assert r.status_code == 400


def test_refund_rejects_negative_amount(app, client):
    a = make_tenant(client, "Biz A", "a_refund5")
    plan = _make_plan(client, a, price=50)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})

    r = client.post(f"/api/payments/{payment_id}/refund", headers=a,
                    json={"amount": -5, "reason": "trying to sneak a negative in"})
    assert r.status_code == 400


def test_refund_rejects_unpaid_payment(app, client):
    a = make_tenant(client, "Biz A", "a_refund6")
    plan = _make_plan(client, a, price=50)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    r = client.post(f"/api/payments/{payment_id}/refund", headers=a, json={"reason": "n/a"})
    assert r.status_code == 400


# --- 2) Authorization lockdown -------------------------------------------

def test_resellers_get_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_authz1")
    collector_hdr = _add_collector(client, a, "collector_authz1")
    r = client.get("/api/resellers", headers=collector_hdr)
    assert r.status_code == 403


def test_expenses_post_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_authz2")
    collector_hdr = _add_collector(client, a, "collector_authz2")
    r = client.post("/api/expenses", headers=collector_hdr,
                    json={"description": "x", "amount": 10, "category": "misc"})
    assert r.status_code == 403


def test_whatsapp_settings_get_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_authz3")
    collector_hdr = _add_collector(client, a, "collector_authz3")
    r = client.get("/api/whatsapp-settings", headers=collector_hdr)
    assert r.status_code == 403


def test_suppliers_post_rejects_unauthenticated(app, client):
    r = client.post("/api/suppliers", json={"name": "Sup"})
    assert r.status_code == 401


def test_mikrotik_servers_get_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_authz4")
    collector_hdr = _add_collector(client, a, "collector_authz4")
    r = client.get("/api/mikrotik-servers", headers=collector_hdr)
    assert r.status_code == 403


def test_customer_feedback_requires_auth_and_is_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "a_authz5")
    b = make_tenant(client, "Biz B", "b_authz5")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)

    unauth = client.post("/api/customer-feedback",
                         json={"customer_id": cust_id, "rating": 5, "category": "service"})
    assert unauth.status_code == 401

    cross_tenant = client.post("/api/customer-feedback", headers=b,
                               json={"customer_id": cust_id, "rating": 5, "category": "service"})
    assert cross_tenant.status_code == 404

    ok = client.post("/api/customer-feedback", headers=a,
                     json={"customer_id": cust_id, "rating": 5, "category": "service"})
    assert ok.status_code == 200


def test_service_status_post_rejects_cross_tenant_customer_id(app, client):
    """IDOR fix: a tenant cannot create a ServiceStatus row against another
    tenant's customer_id, which previously let one tenant probe another's
    customer names via ID enumeration through the GET endpoint."""
    a = make_tenant(client, "Biz A", "a_authz6")
    b = make_tenant(client, "Biz B", "b_authz6")
    plan_b = _make_plan(client, b)
    cust_b = _make_customer(client, b, plan_b, name="Secret Customer")

    r = client.post(f"/api/service-status/{cust_b}", headers=a, json={"status": "active"})
    assert r.status_code == 404
