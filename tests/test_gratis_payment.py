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


def test_mark_gratis_forgives_the_debt_by_crediting_balance(app, client):
    """The unpaid charge already debited the balance when it was created (that's
    what makes it show as owed); waiving it must credit that back, the same as
    collecting cash would, or the balance permanently overstates what's owed."""
    a = make_tenant(client, "Biz A", "a_admin")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)
    payments_before = client.get("/api/payments", headers=a,
                          query_string={"customer_id": cust_id}).get_json()["payments"]
    amount = next(p for p in payments_before if p["id"] == payment_id)["amount"]

    balance_before = client.get(f"/api/customers/{cust_id}/balance", headers=a).get_json()["stored_balance"]

    r = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=a,
                   json={"note": "loyalty reward"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["paid"] is True
    assert body["is_gratis"] is True
    assert body["customer_new_balance"] == balance_before + amount

    balance_after = client.get(f"/api/customers/{cust_id}/balance", headers=a).get_json()["stored_balance"]
    assert balance_after == balance_before + amount  # debt forgiven -- no longer owed

    payments = client.get("/api/payments", headers=a,
                          query_string={"customer_id": cust_id}).get_json()["payments"]
    updated = next(p for p in payments if p["id"] == payment_id)
    assert updated["is_gratis"] is True
    assert updated["gratis_note"] == "loyalty reward"
    assert updated["amount"] > 0  # original amount preserved, not zeroed


def test_mark_gratis_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_admin2")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    collector_hdr = _add_collector(client, a, "collector1")
    r = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=collector_hdr, json={})
    assert r.status_code == 403


def test_mark_gratis_rejects_already_paid_payment(app, client):
    a = make_tenant(client, "Biz A", "a_admin3")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    ok = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=a, json={})
    assert ok.status_code == 200

    again = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=a, json={})
    assert again.status_code == 400


def test_mark_gratis_is_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "a_admin4")
    b = make_tenant(client, "Biz B", "b_admin4")
    plan_b = _make_plan(client, b)
    cust_b = _make_customer(client, b, plan_b)
    payment_id_b = _unpaid_payment_id(client, b, cust_b)

    r = client.put(f"/api/payments/{payment_id_b}/mark_gratis", headers=a, json={})
    assert r.status_code == 404


def test_gratis_payment_excluded_from_revenue_reports(app, client):
    a = make_tenant(client, "Biz A", "a_admin5")
    plan = _make_plan(client, a, price=75)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    r = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=a, json={})
    assert r.status_code == 200

    total_sales = client.get("/api/reports/total-sales", headers=a).get_json()
    assert sum(row["value"] for row in total_sales) == 0

    # No expenses/supplier/salary payments recorded in this fresh tenant, so
    # monthly-revenue's net `value` (sales - expenses) directly reflects sales.
    monthly_revenue = client.get("/api/reports/monthly-revenue", headers=a).get_json()
    assert sum(row["value"] for row in monthly_revenue) == 0

    revenue_detail = client.get("/api/reports/revenue", headers=a).get_json()
    assert revenue_detail["total_revenue"] == 0
    assert revenue_detail["payment_count"] == 0

    financial = client.get("/api/reports/financial", headers=a,
                           query_string={"start_date": "2026-01-01T00:00:00Z",
                                         "end_date": "2026-12-31T00:00:00Z"}).get_json()
    assert sum(row["income"] for row in financial["monthly_data"]) == 0

    # Still visible in the plain payments list (not hidden, just not revenue).
    payments = client.get("/api/payments", headers=a,
                          query_string={"customer_id": cust_id}).get_json()["payments"]
    assert any(p["id"] == payment_id and p["is_gratis"] for p in payments)
