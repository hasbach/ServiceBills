from datetime import datetime, timedelta

from tests.conftest import make_tenant
from app import (app as flask_app, db, User, Payment, Customer, Expense, ExpenseCategory,
                 SupplierPayment, Supplier, UpstreamProvider, UpstreamProviderPayment)
from tests.test_daily_cash_report import _make_plan, _make_customer, _unpaid_payment_id, _today_range

DAY = "2026-10-05"
D = datetime(2026, 10, 5)


def _utc_range(day_str):
    end = (datetime.strptime(day_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"{day_str}T00:00:00.000Z", f"{end}T00:00:00.000Z"


def _beirut_range(day_str):
    """What localDayRange() sends from a UTC+3 browser."""
    d = datetime.strptime(day_str, "%Y-%m-%d")
    return ((d - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            (d + timedelta(hours=21)).strftime("%Y-%m-%dT%H:%M:%S.000Z"))


def _flow(client, hdr, rng):
    start, end = rng
    r = client.get("/api/reports/daily-cash", headers=hdr, query_string={"start_date": start, "end_date": end})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["cash_flow"]


def _tenant_id(username):
    return User.query.filter_by(username=username).first().tenant_id


def _cat(tid, name="Rent2"):
    c = ExpenseCategory(tenant_id=tid, name=name)
    db.session.add(c)
    db.session.flush()
    return c


def _totals(section):
    return {i["category"]: i["total"] for i in section["items"]}


def test_day_in_out_and_net(app, client):
    a = make_tenant(client, "Biz A", "flow1")
    tid = _tenant_id("flow1")
    plan = _make_plan(client, a, price=45)
    cust = _make_customer(client, a, plan)
    with flask_app.app_context():
        cat = _cat(tid)
        sup = Supplier(tenant_id=tid, name="Sup")
        prov = UpstreamProvider(tenant_id=tid, name="Up")
        db.session.add_all([sup, prov])
        db.session.flush()
        noon = D + timedelta(hours=12)
        db.session.add_all([
            Payment(tenant_id=tid, customer_id=cust, amount=45, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=noon, pre_payment=True),
            Payment(tenant_id=tid, customer_id=cust, amount=30, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=noon, is_refund=True),
            # Not cash: auto-settled from credit, and Whish.
            Payment(tenant_id=tid, customer_id=cust, amount=99, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=noon),
            Payment(tenant_id=tid, customer_id=cust, amount=99, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=noon, pre_payment=True, collected_via='whish'),
            Expense(tenant_id=tid, category_id=cat.id, amount=15, description="Rent", date=D),
            Expense(tenant_id=tid, category_id=cat.id, amount=500, description="On credit", date=D,
                    is_credit=True, supplier_id=sup.id),
            SupplierPayment(tenant_id=tid, supplier_id=sup.id, amount=10, payment_date=D),
            UpstreamProviderPayment(tenant_id=tid, upstream_provider_id=prov.id, amount=5,
                                    type='balance_topup', date=noon),
            UpstreamProviderPayment(tenant_id=tid, upstream_provider_id=prov.id, amount=77,
                                    type='renewal_cost', date=noon),
        ])
        db.session.commit()

    flow = _flow(client, a, _utc_range(DAY))
    assert _totals(flow["cash_in"]) == {"Customer payments": 45}
    assert _totals(flow["cash_out"]) == {"Expenses": 15, "Supplier payments": 10,
                                         "Upstream top-ups": 5, "Customer refunds": 30}
    assert flow["cash_in"]["total"] == 45
    assert flow["cash_out"]["total"] == 60
    assert flow["net"] == -15
    assert flow["opening"] is None and flow["cash_start"] is None and flow["cash_end"] is None


def test_reseller_collection_is_cash_in(app, client):
    a = make_tenant(client, "Biz A", "flow2")
    rid = client.post("/api/resellers", headers=a, json={"name": "R", "phone": "1", "type": "type1",
                                                         "balance": 100}).get_json()["reseller"]["id"]
    client.post(f"/api/resellers/{rid}/collect_payment", headers=a, json={"amount": 20})
    client.post(f"/api/resellers/{rid}/add_credit", headers=a, json={"amount": 7})  # not cash
    flow = _flow(client, a, _today_range())
    assert _totals(flow["cash_in"]) == {"Reseller collections": 20}


def test_running_cash_on_hand(app, client):
    a = make_tenant(client, "Biz A", "flow3")
    tid = _tenant_id("flow3")
    plan = _make_plan(client, a, price=45)
    cust = _make_customer(client, a, plan)
    with flask_app.app_context():
        cat = _cat(tid)
        db.session.add_all([
            Expense(tenant_id=tid, category_id=cat.id, amount=30, description="Yesterday", date=D - timedelta(days=1)),
            Expense(tenant_id=tid, category_id=cat.id, amount=999, description="Before opening", date=D - timedelta(days=5)),
            Payment(tenant_id=tid, customer_id=cust, amount=45, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=D + timedelta(hours=9), pre_payment=True),
        ])
        db.session.commit()
    r = client.put("/api/reports/cash-opening", headers=a, json={"date": "2026-10-04", "amount": 100})
    assert r.status_code == 200

    flow = _flow(client, a, _utc_range(DAY))
    assert flow["opening"] == {"date": "2026-10-04", "amount": 100, "whish_amount": 0}
    assert flow["cash_start"] == 70   # 100 - 30 spent on the 4th
    assert flow["cash_end"] == 115    # + 45 in today
    on_opening_day = _flow(client, a, _utc_range("2026-10-04"))
    assert (on_opening_day["cash_start"], on_opening_day["cash_end"]) == (100, 70)
    before_opening = _flow(client, a, _utc_range("2026-10-03"))
    assert before_opening["cash_start"] is None

    client.put("/api/reports/cash-opening", headers=a, json={"date": None})
    assert _flow(client, a, _utc_range(DAY))["opening"] is None


def test_local_day_boundaries_utc_plus_3(app, client):
    """Typed dates count on the day they name; real instants count on the
    viewer's local day."""
    a = make_tenant(client, "Biz A", "flow4")
    tid = _tenant_id("flow4")
    plan = _make_plan(client, a, price=45)
    cust = _make_customer(client, a, plan)
    with flask_app.app_context():
        cat = _cat(tid)
        db.session.add_all([
            Expense(tenant_id=tid, category_id=cat.id, amount=15, description="Typed date", date=D),
            # 22:00 UTC on the 4th = 01:00 on the 5th in Beirut.
            Payment(tenant_id=tid, customer_id=cust, amount=40, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=D - timedelta(hours=2), pre_payment=True),
            # 21:30 UTC on the 5th = 00:30 on the 6th in Beirut.
            Payment(tenant_id=tid, customer_id=cust, amount=99, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=D + timedelta(hours=21, minutes=30), pre_payment=True),
        ])
        db.session.commit()
    flow = _flow(client, a, _beirut_range(DAY))
    assert flow["day"] == DAY
    assert flow["cash_in"]["total"] == 40
    assert flow["cash_out"]["total"] == 15
    entry = flow["cash_in"]["items"][0]["entries"][0]
    assert entry["time"] == "2026-10-05 01:00"


def test_cash_opening_validation_and_roles(app, client):
    a = make_tenant(client, "Biz A", "flow5")
    assert client.put("/api/reports/cash-opening", headers=a,
                      json={"date": "05/10/2026", "amount": 1}).status_code == 400
    assert client.put("/api/reports/cash-opening", headers=a,
                      json={"date": "2026-10-05", "amount": "abc"}).status_code == 400
    client.post("/api/users", headers=a, json={"username": "coll5", "password": "pw", "role": "collector"})
    tok = client.post("/api/login", json={"username": "coll5", "password": "pw"}).get_json()["access_token"]
    assert client.put("/api/reports/cash-opening", headers={"Authorization": f"Bearer {tok}"},
                      json={"date": "2026-10-05", "amount": 1}).status_code == 403


def test_whish_channel_and_totals(app, client):
    """Whish money is reported next to cash, never mixed into it, and the
    totals and running balances combine both."""
    a = make_tenant(client, "Biz A", "flow6")
    tid = _tenant_id("flow6")
    plan = _make_plan(client, a, price=45)
    cust = _make_customer(client, a, plan)
    with flask_app.app_context():
        cat = _cat(tid)
        sup = Supplier(tenant_id=tid, name="Sup")
        prov = UpstreamProvider(tenant_id=tid, name="Up")
        db.session.add_all([sup, prov])
        db.session.flush()
        noon = D + timedelta(hours=12)
        db.session.add_all([
            # in: cash 45, Whish link 30, Whish transfer 20
            Payment(tenant_id=tid, customer_id=cust, amount=45, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=noon, pre_payment=True),
            Payment(tenant_id=tid, customer_id=cust, amount=30, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=noon, collected_via='whish'),
            Payment(tenant_id=tid, customer_id=cust, amount=20, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=noon, collected_via='whish_transfer', received_by_id=None),
            # a bill settled from credit moves no money in either channel
            Payment(tenant_id=tid, customer_id=cust, amount=99, currency='USD', fx_rate_to_reporting=1,
                    paid=True, paid_at=noon, settled_from_credit=True),
            # out: cash expense 15, Whish expense 7, Whish supplier payment 10, Whish top-up 5
            Expense(tenant_id=tid, category_id=cat.id, amount=15, description="Rent", date=D),
            Expense(tenant_id=tid, category_id=cat.id, amount=7, description="Online", date=D, paid_via='whish'),
            SupplierPayment(tenant_id=tid, supplier_id=sup.id, amount=10, payment_date=D, paid_via='whish'),
            UpstreamProviderPayment(tenant_id=tid, upstream_provider_id=prov.id, amount=5,
                                    type='balance_topup', date=noon, paid_via='whish'),
        ])
        db.session.commit()
    client.put("/api/reports/cash-opening", headers=a, json={"date": DAY, "amount": 100, "whish_amount": 40})

    flow = _flow(client, a, _utc_range(DAY))
    assert flow["cash_in"]["total"] == 45 and flow["cash_out"]["total"] == 15
    assert flow["whish_in"]["total"] == 50 and flow["whish_out"]["total"] == 22
    assert _totals(flow["whish_out"]) == {"Expenses": 7, "Supplier payments": 10, "Upstream top-ups": 5}
    assert (flow["total_in"], flow["total_out"], flow["total_net"]) == (95, 37, 58)
    assert (flow["cash_start"], flow["cash_end"]) == (100, 130)
    assert (flow["whish_start"], flow["whish_end"]) == (40, 68)
    assert (flow["total_start"], flow["total_end"]) == (140, 198)
    assert flow["opening"]["whish_amount"] == 40


def test_paid_via_is_accepted_and_validated(app, client):
    a = make_tenant(client, "Biz A", "flow7")
    client.post("/api/expense_categories", headers=a, json={"name": "Stock"})
    r = client.post("/api/expenses", headers=a, json={"category": "Stock", "amount": 9, "description": "x",
                                                      "date": DAY, "paid_via": "whish"})
    assert r.status_code == 201 and r.get_json()["paid_via"] == "whish"
    eid = r.get_json()["id"]
    assert client.put(f"/api/expenses/{eid}", headers=a, json={"paid_via": "cash"}).get_json()["paid_via"] == "cash"
    assert client.post("/api/expenses", headers=a, json={"category": "Stock", "amount": 9, "description": "x",
                                                         "date": DAY, "paid_via": "bitcoin"}).status_code == 400
    sid = client.post("/api/suppliers", headers=a, json={"name": "S"}).get_json()["id"]
    r = client.post(f"/api/suppliers/{sid}/payments", headers=a, json={"amount": 5, "paid_via": "whish"})
    assert r.get_json()["payment"]["paid_via"] == "whish"
    rid = client.post("/api/resellers", headers=a, json={"name": "R", "phone": "1", "type": "type1",
                                                         "balance": 50}).get_json()["reseller"]["id"]
    client.post(f"/api/resellers/{rid}/collect_payment", headers=a, json={"amount": 20, "paid_via": "whish"})
    flow = _flow(client, a, _today_range())
    assert _totals(flow["whish_in"]) == {"Reseller collections": 20}


def _pay_revert_repay(client, hdr, pay):
    assert client.put(f"/api/payments/{pay}/mark_paid", headers=hdr, json={"action": "pay"}).status_code == 200
    assert client.put(f"/api/payments/{pay}/revert", headers=hdr, json={"reason": "wrong customer"}).status_code == 200
    assert client.put(f"/api/payments/{pay}/mark_paid", headers=hdr, json={"action": "pay"}).status_code == 200


def test_reverted_then_repaid_payment_counts_on_its_new_paid_day(app, client):
    """A revert is undone by paying the bill again: the money was received,
    so it shows up on the day of the new payment -- not on the reverted day."""
    a = make_tenant(client, "Biz A", "flow8")
    plan = _make_plan(client, a, price=45)
    cust = _make_customer(client, a, plan)
    pay = _unpaid_payment_id(client, a, cust)
    _pay_revert_repay(client, a, pay)
    with flask_app.app_context():
        p = Payment.query.get(pay)
        assert p.paid and p.reverted_at is not None  # revert audit trail is kept
        p.reverted_at = D + timedelta(hours=10)
        p.paid_at = D + timedelta(days=1, hours=12)
        p.collected_at = None
        db.session.commit()

    nxt = (D + timedelta(days=1)).strftime("%Y-%m-%d")
    assert _flow(client, a, _utc_range(DAY))["cash_in"]["total"] == 0
    assert _flow(client, a, _utc_range(nxt))["cash_in"]["total"] == 45
    start, end = _utc_range(nxt)
    report = client.get("/api/reports/daily-cash", headers=a,
                        query_string={"start_date": start, "end_date": end}).get_json()
    assert report["grand_total"] == 45


def test_reverted_then_repaid_today_shows_in_todays_reports(app, client):
    a = make_tenant(client, "Biz A", "flow9")
    plan = _make_plan(client, a, price=30)
    cust = _make_customer(client, a, plan)
    pay = _unpaid_payment_id(client, a, cust)
    _pay_revert_repay(client, a, pay)

    rng = _today_range()
    assert _flow(client, a, rng)["cash_in"]["total"] == 30
    report = client.get("/api/reports/daily-cash", headers=a,
                        query_string={"start_date": rng[0], "end_date": rng[1]}).get_json()
    assert report["grand_total"] == 30


def test_reverted_and_not_repaid_stays_out(app, client):
    a = make_tenant(client, "Biz A", "flow10")
    plan = _make_plan(client, a, price=30)
    cust = _make_customer(client, a, plan)
    pay = _unpaid_payment_id(client, a, cust)
    client.put(f"/api/payments/{pay}/mark_paid", headers=a, json={"action": "pay"})
    client.put(f"/api/payments/{pay}/revert", headers=a, json={"reason": "oops"})

    rng = _today_range()
    assert _flow(client, a, rng)["cash_in"]["total"] == 0
