from datetime import datetime, timedelta

from tests.conftest import make_tenant
from app import (app as flask_app, db, User, Payment, Customer, Expense, ExpenseCategory,
                 SupplierPayment, Supplier, UpstreamProvider, UpstreamProviderPayment)
from tests.test_daily_cash_report import _make_plan, _make_customer, _unpaid_payment_id

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
    flow = _flow(client, a, _utc_range(datetime.utcnow().strftime("%Y-%m-%d")))
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
    assert flow["opening"] == {"date": "2026-10-04", "amount": 100}
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
