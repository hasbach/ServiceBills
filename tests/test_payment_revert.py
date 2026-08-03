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


def test_revert_restores_balance_and_pending_state(app, client):
    a = make_tenant(client, "Biz A", "a_revert1")
    plan = _make_plan(client, a, price=80)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    paid = client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})
    assert paid.status_code == 200, paid.get_data(as_text=True)
    balance_after_pay = paid.get_json()["customer_new_balance"]

    r = client.put(f"/api/payments/{payment_id}/revert", headers=a,
                   json={"reason": "marked paid by mistake"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["paid"] is False
    assert body["collected"] is False
    assert body["customer_new_balance"] == balance_after_pay - 80

    payments = client.get("/api/payments", headers=a,
                          query_string={"customer_id": cust_id}).get_json()["payments"]
    reverted = next(p for p in payments if p["id"] == payment_id)
    assert reverted["paid"] is False
    assert reverted["collected"] is False
    assert reverted["paid_at"] is None
    assert reverted["revert_reason"] == "marked paid by mistake"
    assert reverted["reverted_by"] == "a_revert1"


def test_revert_gratis_payment_does_not_touch_balance(app, client):
    a = make_tenant(client, "Biz A", "a_revert2")
    plan = _make_plan(client, a, price=60)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    gratis = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=a, json={})
    assert gratis.status_code == 200

    balance_before = client.get(f"/api/customers/{cust_id}/balance", headers=a).get_json()["stored_balance"]

    r = client.put(f"/api/payments/{payment_id}/revert", headers=a,
                   json={"reason": "should not have been waived"})
    assert r.status_code == 200, r.get_data(as_text=True)

    balance_after = client.get(f"/api/customers/{cust_id}/balance", headers=a).get_json()["stored_balance"]
    assert balance_after == balance_before  # gratis never credited the balance

    payments = client.get("/api/payments", headers=a,
                          query_string={"customer_id": cust_id}).get_json()["payments"]
    reverted = next(p for p in payments if p["id"] == payment_id)
    assert reverted["paid"] is False
    assert reverted["is_gratis"] is False


def test_revert_requires_a_reason(app, client):
    a = make_tenant(client, "Biz A", "a_revert3")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})

    r = client.put(f"/api/payments/{payment_id}/revert", headers=a, json={"reason": "  "})
    assert r.status_code == 400


def test_revert_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_revert4")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})

    collector_hdr = _add_collector(client, a, "collector_revert4")
    r = client.put(f"/api/payments/{payment_id}/revert", headers=collector_hdr,
                   json={"reason": "mistake"})
    assert r.status_code == 403


def test_revert_rejects_unpaid_payment(app, client):
    a = make_tenant(client, "Biz A", "a_revert5")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    r = client.put(f"/api/payments/{payment_id}/revert", headers=a, json={"reason": "mistake"})
    assert r.status_code == 400


def test_revert_is_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "a_revert6")
    b = make_tenant(client, "Biz B", "b_revert6")
    plan_b = _make_plan(client, b)
    cust_b = _make_customer(client, b, plan_b)
    payment_id_b = _unpaid_payment_id(client, b, cust_b)
    client.put(f"/api/payments/{payment_id_b}/mark_paid", headers=b, json={"action": "pay"})

    r = client.put(f"/api/payments/{payment_id_b}/revert", headers=a, json={"reason": "mistake"})
    assert r.status_code == 404


def test_revenue_report_accepts_iso_datetime_params(app, client):
    """Regression test: the frontend sends full ISO-8601 datetimes
    (Date.toISOString()), which used to blow up strptime('%Y-%m-%d') with an
    unhandled ValueError -> raw 500 -> white screen on the Enhanced Reports page."""
    a = make_tenant(client, "Biz A", "a_revenue1")
    plan = _make_plan(client, a, price=40)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})

    r = client.get("/api/reports/revenue", headers=a, query_string={
        "start_date": "2026-01-01T00:00:00.000Z",
        "end_date": "2026-12-31T23:59:59.000Z",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["total_revenue"] == 40
    assert body["payment_count"] == 1


def test_payments_filter_by_paid_date_range(app, client):
    a = make_tenant(client, "Biz A", "a_paiddate1")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    client.put(f"/api/payments/{payment_id}/mark_paid", headers=a, json={"action": "pay"})

    from datetime import datetime, timedelta
    today = datetime.utcnow().strftime('%Y-%m-%d')
    tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%d')
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')

    in_range = client.get("/api/payments", headers=a, query_string={
        "customer_id": cust_id, "paid_date_start": today, "paid_date_end": tomorrow,
    }).get_json()["payments"]
    assert any(p["id"] == payment_id for p in in_range)

    out_of_range = client.get("/api/payments", headers=a, query_string={
        "customer_id": cust_id, "paid_date_start": "2020-01-01", "paid_date_end": yesterday,
    }).get_json()["payments"]
    assert not any(p["id"] == payment_id for p in out_of_range)
