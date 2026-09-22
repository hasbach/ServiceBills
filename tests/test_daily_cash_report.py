from datetime import datetime, timedelta

from tests.conftest import make_tenant
from app import app as flask_app, db, Payment, Customer


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


def _day_range(day_str):
    """start_date/end_date for one UTC calendar day, matching what the
    frontend's localDayRange() sends for a browser in the UTC zone."""
    start = f"{day_str}T00:00:00.000Z"
    end_dt = datetime.strptime(day_str, "%Y-%m-%d") + timedelta(days=1)
    end = end_dt.strftime("%Y-%m-%dT00:00:00.000Z")
    return start, end


def _today_range():
    return _day_range(datetime.utcnow().strftime("%Y-%m-%d"))


def test_daily_cash_groups_by_field_collector_and_totals_correctly(app, client):
    a = make_tenant(client, "Biz A", "a_cash1")
    plan = _make_plan(client, a, price=80)
    cust1 = _make_customer(client, a, plan, name="Cust1")
    cust2 = _make_customer(client, a, plan, name="Cust2")
    pay1 = _unpaid_payment_id(client, a, cust1)
    pay2 = _unpaid_payment_id(client, a, cust2)

    collector1 = _add_collector(client, a, "collector1_cash1")
    collector2 = _add_collector(client, a, "collector2_cash1")

    client.put(f"/api/payments/{pay1}/mark_paid", headers=collector1, json={"action": "collect"})
    client.put(f"/api/payments/{pay1}/mark_paid", headers=a, json={"action": "pay"})
    client.put(f"/api/payments/{pay2}/mark_paid", headers=collector2, json={"action": "collect"})
    client.put(f"/api/payments/{pay2}/mark_paid", headers=a, json={"action": "pay"})

    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=a,
                    query_string={"start_date": start, "end_date": end})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["grand_total"] == 160
    totals_by_name = {g["collector_name"]: g["total"] for g in body["groups"]}
    assert totals_by_name["collector1_cash1"] == 80
    assert totals_by_name["collector2_cash1"] == 80
    collector_group = next(g for g in body["groups"] if g["collector_name"] == "collector1_cash1")
    assert collector_group["is_office"] is False
    assert collector_group["payment_count"] == 1
    assert collector_group["payments"][0]["customer_name"] == "Cust1"


def test_daily_cash_office_direct_bucket_when_no_field_collector(app, client):
    a = make_tenant(client, "Biz A", "a_cash2")
    plan = _make_plan(client, a, price=45)
    cust = _make_customer(client, a, plan)
    pay = _unpaid_payment_id(client, a, cust)

    client.put(f"/api/payments/{pay}/mark_paid", headers=a, json={"action": "pay"})

    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=a,
                    query_string={"start_date": start, "end_date": end})
    body = r.get_json()
    assert len(body["groups"]) == 1
    group = body["groups"][0]
    assert group["is_office"] is True
    assert group["collector_id"] is None
    assert group["collector_name"] == "Office / Direct"
    assert group["total"] == 45


def test_daily_cash_excludes_whish_refund_gratis_and_reverted_payments(app, client):
    a = make_tenant(client, "Biz A", "a_cash3")
    plan = _make_plan(client, a, price=30)
    cust = _make_customer(client, a, plan)

    with flask_app.app_context():
        tenant_id = Customer.query.filter_by(id=cust).first().tenant_id
        now = datetime.utcnow()
        rows = [
            Payment(tenant_id=tenant_id, customer_id=cust, amount=30, currency='USD',
                    fx_rate_to_reporting=1, paid=True, paid_at=now, collected_at=now,
                    collected_via='whish'),
            Payment(tenant_id=tenant_id, customer_id=cust, amount=30, currency='USD',
                    fx_rate_to_reporting=1, paid=True, paid_at=now, collected_at=now,
                    is_refund=True),
            Payment(tenant_id=tenant_id, customer_id=cust, amount=30, currency='USD',
                    fx_rate_to_reporting=1, paid=True, paid_at=now, collected_at=now,
                    is_gratis=True),
            Payment(tenant_id=tenant_id, customer_id=cust, amount=30, currency='USD',
                    fx_rate_to_reporting=1, paid=True, paid_at=now, collected_at=now,
                    reverted_at=now),
        ]
        db.session.add_all(rows)
        db.session.commit()

    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=a,
                    query_string={"start_date": start, "end_date": end})
    body = r.get_json()
    assert body["grand_total"] == 0
    assert body["groups"] == []


def test_daily_cash_respects_the_day_boundary(app, client):
    a = make_tenant(client, "Biz A", "a_cash4")
    plan = _make_plan(client, a, price=20)
    cust = _make_customer(client, a, plan)
    pay = _unpaid_payment_id(client, a, cust)
    client.put(f"/api/payments/{pay}/mark_paid", headers=a, json={"action": "pay"})

    with flask_app.app_context():
        p = Payment.query.get(pay)
        p.collected_at = datetime(2026, 6, 15, 12, 0, 0)
        p.paid_at = datetime(2026, 6, 15, 12, 0, 0)
        db.session.commit()

    start, end = _day_range("2026-06-15")
    same_day = client.get("/api/reports/daily-cash", headers=a,
                          query_string={"start_date": start, "end_date": end}).get_json()
    assert same_day["grand_total"] == 20

    start_prev, end_prev = _day_range("2026-06-14")
    prev_day = client.get("/api/reports/daily-cash", headers=a,
                          query_string={"start_date": start_prev, "end_date": end_prev}).get_json()
    assert prev_day["grand_total"] == 0

    start_next, end_next = _day_range("2026-06-16")
    next_day = client.get("/api/reports/daily-cash", headers=a,
                          query_string={"start_date": start_next, "end_date": end_next}).get_json()
    assert next_day["grand_total"] == 0


def test_daily_cash_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_cash5")
    collector_hdr = _add_collector(client, a, "collector_cash5")
    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=collector_hdr,
                    query_string={"start_date": start, "end_date": end})
    assert r.status_code == 403


def test_daily_cash_is_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "a_cash6")
    b = make_tenant(client, "Biz B", "b_cash6")
    plan_b = _make_plan(client, b)
    cust_b = _make_customer(client, b, plan_b)
    pay_b = _unpaid_payment_id(client, b, cust_b)
    client.put(f"/api/payments/{pay_b}/mark_paid", headers=b, json={"action": "pay"})

    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=a,
                    query_string={"start_date": start, "end_date": end})
    assert r.get_json()["grand_total"] == 0


def test_daily_cash_requires_start_and_end_date(app, client):
    a = make_tenant(client, "Biz A", "a_cash7")
    r = client.get("/api/reports/daily-cash", headers=a, query_string={"start_date": "2026-06-15T00:00:00.000Z"})
    assert r.status_code == 400
