"""Staff can re-label a collected/paid payment as a direct Whish transfer (not
cash, not a payment-link payment): it leaves the daily cash total, shows in
the Whish payments report, and can be switched back."""
from datetime import datetime, timedelta

import app as appmod
from tests.conftest import make_tenant


def _pro(client, tenant_name):
    """Pro bundle includes the whish_payments module the report route needs."""
    with client.application.app_context():
        appmod.Tenant.query.filter_by(name=tenant_name).first().plan = 'pro'
        appmod.db.session.commit()


def _login(client, username, password="pw"):
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _paid_payment(client, hdr, name="Cust", price=50):
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": price, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    cid = client.post("/api/customers", headers=hdr, json={
        "name": name, "phone": "70123456", "address": "a", "subscription_plan_id": plan,
        "subscription_start_date": "2026-01-01"}).get_json()["customer_id"]
    pid = next(p["id"] for p in client.get("/api/payments", headers=hdr, query_string={"customer_id": cid})
               .get_json()["payments"] if not p["paid"])
    assert client.put(f"/api/payments/{pid}/mark_paid", headers=hdr, json={"action": "pay"}).status_code == 200
    return pid


def _today():
    start = datetime.utcnow().strftime("%Y-%m-%dT00:00:00.000Z")
    end = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
    return {"start_date": start, "end_date": end}


def _cash_total(client, hdr):
    return client.get("/api/reports/daily-cash", headers=hdr, query_string=_today()).get_json()["grand_total"]


def test_marking_whish_transfer_moves_it_out_of_cash_and_into_whish_report(client):
    hdr = make_tenant(client, "Biz W1", "w1_admin")
    _pro(client, "Biz W1")
    pid = _paid_payment(client, hdr)
    assert _cash_total(client, hdr) == 50

    r = client.put(f"/api/payments/{pid}/method", headers=hdr, json={"method": "whish_transfer", "reference": "WT-889"})
    assert r.status_code == 200, r.get_json()
    assert (r.get_json()["collected_via"], r.get_json()["whish_transaction_number"]) == ("whish_transfer", "WT-889")
    assert _cash_total(client, hdr) == 0

    rows = client.get("/api/reports/customer-whish-payments", headers=hdr, query_string=_today()).get_json()["links"]
    transfer = next(r for r in rows if r["source"] == "manual_transfer")
    assert (transfer["payment_id"], transfer["amount"], transfer["whish_transaction_number"]) == (pid, 50, "WT-889")
    only = client.get("/api/reports/customer-whish-payments", headers=hdr,
                      query_string={**_today(), "status": "manual_transfer"}).get_json()["links"]
    assert [r["payment_id"] for r in only] == [pid]

    listed = next(p for p in client.get("/api/payments", headers=hdr).get_json()["payments"] if p["id"] == pid)
    assert listed["collected_via"] == "whish_transfer"

    # ...and back to cash.
    assert client.put(f"/api/payments/{pid}/method", headers=hdr, json={"method": "cash"}).status_code == 200
    assert _cash_total(client, hdr) == 50
    rows = client.get("/api/reports/customer-whish-payments", headers=hdr, query_string=_today()).get_json()["links"]
    assert not [r for r in rows if r.get("source") == "manual_transfer"]


def test_collected_not_yet_confirmed_payment_can_be_marked_too(client):
    hdr = make_tenant(client, "Biz W2", "w2_admin")
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 30, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    cid = client.post("/api/customers", headers=hdr, json={
        "name": "C", "phone": "1", "address": "a", "subscription_plan_id": plan,
        "subscription_start_date": "2026-01-01"}).get_json()["customer_id"]
    pid = next(p["id"] for p in client.get("/api/payments", headers=hdr, query_string={"customer_id": cid})
               .get_json()["payments"] if not p["paid"])
    client.put(f"/api/payments/{pid}/mark_paid", headers=hdr, json={"action": "collect"})
    assert client.put(f"/api/payments/{pid}/method", headers=hdr, json={"method": "whish_transfer"}).status_code == 200
    # Confirming receipt later keeps the method.
    client.put(f"/api/payments/{pid}/mark_paid", headers=hdr, json={"action": "pay"})
    assert appmod.Payment.query.get(pid).collected_via == "whish_transfer"


def test_rules_unpaid_gateway_bad_method_and_roles(client):
    hdr = make_tenant(client, "Biz W3", "w3_admin")
    pid = _paid_payment(client, hdr)

    assert client.put(f"/api/payments/{pid}/method", headers=hdr, json={"method": "bitcoin"}).status_code == 400

    p = appmod.Payment.query.get(pid)
    p.collected_via = "whish"
    appmod.db.session.commit()
    assert client.put(f"/api/payments/{pid}/method", headers=hdr, json={"method": "cash"}).status_code == 400
    p.collected_via = None
    appmod.db.session.commit()

    for role in ("cashier", "collector"):
        client.post("/api/users", headers=hdr, json={"username": f"w3_{role}", "password": "pw", "role": role})
        r = client.put(f"/api/payments/{pid}/method", headers=_login(client, f"w3_{role}"), json={"method": "whish_transfer"})
        assert r.status_code == 403, role

    unpaid = appmod.Payment(tenant_id=p.tenant_id, customer_id=p.customer_id, amount=5, paid=False)
    appmod.db.session.add(unpaid)
    appmod.db.session.commit()
    assert client.put(f"/api/payments/{unpaid.id}/method", headers=hdr, json={"method": "whish_transfer"}).status_code == 400


def test_revert_clears_the_transfer_mark(client):
    hdr = make_tenant(client, "Biz W4", "w4_admin")
    pid = _paid_payment(client, hdr)
    client.put(f"/api/payments/{pid}/method", headers=hdr, json={"method": "whish_transfer", "reference": "X1"})
    assert client.put(f"/api/payments/{pid}/revert", headers=hdr, json={"reason": "wrong customer"}).status_code == 200
    p = appmod.Payment.query.get(pid)
    assert (p.collected_via, p.whish_transaction_number) == (None, None)


def _unpaid(client, hdr, price=40):
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": price, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    cid = client.post("/api/customers", headers=hdr, json={
        "name": "C", "phone": "1", "address": "a", "subscription_plan_id": plan,
        "subscription_start_date": "2026-01-01"}).get_json()["customer_id"]
    return next(p["id"] for p in client.get("/api/payments", headers=hdr, query_string={"customer_id": cid})
                .get_json()["payments"] if not p["paid"])


def test_cashier_and_collector_can_pick_whish_transfer_when_collecting(client):
    hdr = make_tenant(client, "Biz W5", "w5_admin")
    for role in ("cashier", "collector"):
        client.post("/api/users", headers=hdr, json={"username": f"w5_{role}", "password": "pw", "role": role})
        pid = _unpaid(client, hdr)
        r = client.put(f"/api/payments/{pid}/mark_paid", headers=_login(client, f"w5_{role}"),
                       json={"action": "collect", "method": "whish_transfer", "reference": f"REF-{role}"})
        assert r.status_code == 200, (role, r.get_json())
        p = appmod.Payment.query.get(pid)
        assert (p.collected, p.paid, p.collected_via, p.whish_transaction_number) == (True, False, "whish_transfer", f"REF-{role}")


def test_collect_defaults_to_cash_and_rejects_unknown_method(client):
    hdr = make_tenant(client, "Biz W6", "w6_admin")
    pid = _unpaid(client, hdr)
    assert client.put(f"/api/payments/{pid}/mark_paid", headers=hdr,
                      json={"action": "collect", "method": "card"}).status_code == 400
    assert appmod.Payment.query.get(pid).collected is False
    assert client.put(f"/api/payments/{pid}/mark_paid", headers=hdr, json={"action": "collect"}).status_code == 200
    assert appmod.Payment.query.get(pid).collected_via is None


def test_whish_transfer_collected_by_cashier_stays_out_of_cash_after_confirm(client):
    hdr = make_tenant(client, "Biz W7", "w7_admin")
    _pro(client, "Biz W7")
    client.post("/api/users", headers=hdr, json={"username": "w7_cash", "password": "pw", "role": "cashier"})
    pid = _unpaid(client, hdr, price=60)
    client.put(f"/api/payments/{pid}/mark_paid", headers=_login(client, "w7_cash"),
               json={"action": "collect", "method": "whish_transfer"})
    client.put(f"/api/payments/{pid}/mark_paid", headers=hdr, json={"action": "pay"})
    assert _cash_total(client, hdr) == 0
    rows = client.get("/api/reports/customer-whish-payments", headers=hdr, query_string=_today()).get_json()["links"]
    assert [r["payment_id"] for r in rows if r["source"] == "manual_transfer"] == [pid]
