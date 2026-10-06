"""Day close (cash count) + closed-day lock. Spec: docs/superpowers/specs/2026-10-06-day-close-design.md"""
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.conftest import make_tenant, auth_headers
from tests.test_daily_cash_report import _make_plan, _make_customer, _unpaid_payment_id
from app import (app as flask_app, db, User, Payment, Customer, Expense, ExpenseCategory, Supplier,
                 SupplierPayment, Reseller, UpstreamProvider, Employee, CashEntry, AccountTransfer,
                 CustomerPaymentLink, BusinessSettings, DayClose, _local_day)

BEIRUT = ZoneInfo("Asia/Beirut")


def _today():
    return datetime.now(BEIRUT).date()


def _iso(d):
    return d.isoformat()


def _tid(username):
    return User.query.filter_by(username=username).first().tenant_id


def _flow(client, hdr, day):
    r = client.get("/api/reports/daily-cash", headers=hdr, query_string={"day": _iso(day)})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["cash_flow"]


def _set_opening(client, hdr, day, cash=100, whish=20):
    r = client.put("/api/reports/cash-opening", headers=hdr,
                   json={"date": _iso(day), "amount": cash, "whish_amount": whish})
    assert r.status_code == 200, r.get_data(as_text=True)


def _close(client, hdr, day, cash, whish, note=None):
    body = {"day": _iso(day), "counted_cash": cash, "counted_whish": whish}
    if note:
        body["note"] = note
    return client.post("/api/day-closes", headers=hdr, json=body)


def _cat(client, hdr, name="Office Supplies"):
    r = client.post("/api/expense_categories", headers=hdr, json={"name": name})
    assert r.status_code == 201, r.get_data(as_text=True)
    return name


def _blocked(r, day=None):
    assert r.status_code == 409, (r.status_code, r.get_data(as_text=True))
    body = r.get_json()
    assert body["code"] == "day_closed"
    assert "closed" in body["error"] and "reopen" in body["error"]
    if day:
        assert _iso(day) in body["error"]


# --------------------------------------------------------------------------- close rules

def test_close_rules(app, client):
    a = make_tenant(client, "Biz", "dc_rules")
    today = _today()
    # No opening balance yet.
    assert _close(client, a, today, 0, 0).status_code == 400
    _set_opening(client, a, today - timedelta(days=5))
    # Before the opening date, in the future, bad numbers, bad day.
    assert _close(client, a, today - timedelta(days=6), 0, 0).status_code == 400
    assert _close(client, a, today + timedelta(days=1), 0, 0).status_code == 400
    assert client.post("/api/day-closes", headers=a,
                       json={"day": _iso(today - timedelta(days=4)), "counted_cash": "x",
                             "counted_whish": 0}).status_code == 400
    assert client.post("/api/day-closes", headers=a, json={"day": "nope", "counted_cash": 1,
                                                           "counted_whish": 1}).status_code == 400

    d3 = today - timedelta(days=3)
    r = _close(client, a, d3, 90, 20, note="  evening count ")
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body["day"] == _iso(d3) and body["expected_cash"] == 100 and body["counted_cash"] == 90
    assert body["cash_diff"] == -10 and body["whish_diff"] == 0 and body["note"] == "evening count"
    assert body["closed_by"] == "dc_rules" and body["closed_at"].endswith("Z") and body["id"]
    # Duplicate day and anything on/before the latest close are refused (409).
    assert _close(client, a, d3, 1, 1).status_code == 409
    assert _close(client, a, d3 - timedelta(days=1), 1, 1).status_code == 409
    # Skipping ahead is fine; history is newest first.
    assert _close(client, a, today - timedelta(days=1), 80, 20).status_code == 201
    hist = client.get("/api/day-closes", headers=a).get_json()
    assert [h["day"] for h in hist] == [_iso(today - timedelta(days=1)), _iso(d3)]
    assert len(client.get("/api/day-closes", headers=a, query_string={"limit": 1}).get_json()) == 1


def test_close_permissions(app, client):
    a = make_tenant(client, "Biz", "dc_perm")
    fin = auth_headers(client, "dc_fin", role="finance")
    emp = auth_headers(client, "dc_emp", role="employee")
    today = _today()
    _set_opening(client, a, today - timedelta(days=3))
    assert _close(client, emp, today - timedelta(days=2), 100, 20).status_code == 403
    assert client.get("/api/day-closes", headers=emp).status_code == 403
    r = _close(client, fin, today - timedelta(days=2), 100, 20)
    assert r.status_code == 201
    cid = r.get_json()["id"]
    # Only admin reopens.
    assert client.delete(f"/api/day-closes/{cid}", headers=fin).status_code == 403
    assert client.delete(f"/api/day-closes/{cid}", headers=a).status_code == 200


def test_reopen_latest_only_and_adjustment_disappears(app, client):
    a = make_tenant(client, "Biz", "dc_reopen")
    today = _today()
    d1, d2 = today - timedelta(days=3), today - timedelta(days=2)
    _set_opening(client, a, d1)
    c1 = _close(client, a, d1, 90, 20).get_json()["id"]
    c2 = _close(client, a, d2, 95, 20).get_json()["id"]
    r = client.delete(f"/api/day-closes/{c1}", headers=a)
    assert r.status_code == 409 and r.get_json()["code"] == "not_latest_close"
    assert _flow(client, a, d2)["locked_through"] == _iso(d2)
    assert client.delete(f"/api/day-closes/{c2}", headers=a).status_code == 200
    f2 = _flow(client, a, d2)
    assert f2["close"] is None and f2["locked_through"] == _iso(d1)
    # d2's start is d1's counted amount; with the d2 adjustment gone its end equals its start.
    assert f2["cash_start"] == 90 and f2["cash_end"] == 90
    assert client.delete(f"/api/day-closes/{c2}", headers=a).status_code == 404
    assert client.delete(f"/api/day-closes/{c1}", headers=a).status_code == 200
    assert _flow(client, a, d1)["locked_through"] is None


def test_other_tenants_closes_are_invisible(app, client):
    a = make_tenant(client, "Biz A", "dc_iso_a")
    b = make_tenant(client, "Biz B", "dc_iso_b")
    today = _today()
    _set_opening(client, a, today - timedelta(days=3))
    cid = _close(client, a, today - timedelta(days=2), 100, 20).get_json()["id"]
    assert client.get("/api/day-closes", headers=b).get_json() == []
    assert client.delete(f"/api/day-closes/{cid}", headers=b).status_code == 404
    assert _flow(client, b, today)["locked_through"] is None


# --------------------------------------------------------------------------- adjustments / next-day start

def test_adjustment_entries_and_next_day_start_equals_counted(app, client):
    a = make_tenant(client, "Biz", "dc_adj")
    today = _today()
    d1, d2 = today - timedelta(days=4), today - timedelta(days=3)
    _set_opening(client, a, d1, cash=100, whish=20)
    assert client.post("/api/cash-entries", headers=a,
                       json={"account": "cash", "amount": 50, "reason": "top-up", "date": _iso(d1)}).status_code == 201
    before = _flow(client, a, d1)
    assert before["cash_end"] == 150 and before["closable"] is True and before["close"] is None

    # Cash short by 10, Whish over by 5.
    r = _close(client, a, d1, 140, 25, note="Counted at close")
    assert r.status_code == 201
    f1 = _flow(client, a, d1)
    assert f1["cash_end"] == 140 and f1["whish_end"] == 25
    cash_out = {i["category"]: i for i in f1["cash_out"]["items"]}
    assert cash_out["Over/short adjustment"]["total"] == 10
    assert cash_out["Over/short adjustment"]["entries"][0]["description"] == "Counted at close"
    whish_in = {i["category"]: i for i in f1["whish_in"]["items"]}
    assert whish_in["Over/short adjustment"]["total"] == 5
    assert whish_in["Over/short adjustment"]["entries"][0]["description"] == "Counted at close"
    # The adjustment is real money: it shows in the combined totals.
    assert f1["total_in"] == 55 and f1["total_out"] == 10 and f1["total_net"] == 45
    close = f1["close"]
    assert close["expected_cash"] == 150 and close["counted_cash"] == 140 and close["cash_diff"] == -10
    assert close["expected_whish"] == 20 and close["counted_whish"] == 25 and close["whish_diff"] == 5
    assert close["late"] == {"cash": 0, "whish": 0, "count": 0}
    assert f1["closable"] is False and f1["locked_through"] == _iso(d1)
    # Next day starts from what was counted.
    f2 = _flow(client, a, d2)
    assert f2["cash_start"] == 140 and f2["whish_start"] == 25 and f2["total_start"] == 165
    assert f2["closable"] is True and f2["close"] is None and f2["locked_through"] == _iso(d1)
    # Default note.
    assert _close(client, a, d2, 140, 25).status_code == 201  # zero diff -> no adjustment rows
    assert _flow(client, a, d2)["cash_in"]["items"] == []
    assert _flow(client, a, d2)["cash_out"]["items"] == []


def test_default_adjustment_description_is_cash_count(app, client):
    a = make_tenant(client, "Biz", "dc_adjdesc")
    d = _today() - timedelta(days=2)
    _set_opening(client, a, d)
    _close(client, a, d, 110, 20)
    f = _flow(client, a, d)
    assert f["cash_in"]["items"][0]["entries"][0]["description"] == "Cash count"


# --------------------------------------------------------------------------- lock: one test, every guarded path

def test_every_guarded_path_returns_409_on_a_closed_day(app, client):
    a = make_tenant(client, "Biz", "dc_lock")
    tid = _tid("dc_lock")
    today = _today()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    day_midnight = datetime.combine(today, datetime.min.time())
    _set_opening(client, a, today - timedelta(days=3))
    category = _cat(client, a)
    plan = _make_plan(client, a, price=30)
    cust = _make_customer(client, a, plan, name="LockCust")
    cust2 = _make_customer(client, a, plan, name="LockCust2")
    cust_del = _make_customer(client, a, plan, name="LockCustDel")
    unpaid_a = _unpaid_payment_id(client, a, cust)
    unpaid_b = _unpaid_payment_id(client, a, cust2)

    now = day_midnight + timedelta(hours=9)  # noon in Beirut, whatever the UTC clock says
    with flask_app.app_context():
        cat_row = ExpenseCategory.query.filter_by(tenant_id=tid, name=category).first()
        exp = Expense(tenant_id=tid, category_id=cat_row.id, amount=7, description="x", date=day_midnight)
        sup = Supplier(tenant_id=tid, name="Sup")
        emp = Employee(tenant_id=tid, name="Emp", monthly_salary=100)
        res = Reseller(tenant_id=tid, name="Res", phone="1", type="type1")
        prov = UpstreamProvider(tenant_id=tid, name="Prov")
        db.session.add_all([exp, sup, emp, res, prov])
        db.session.flush()
        sp = SupplierPayment(tenant_id=tid, supplier_id=sup.id, amount=3, payment_date=day_midnight)
        ce = CashEntry(tenant_id=tid, account="cash", amount=4, reason="r", date=day_midnight)
        tr = AccountTransfer(tenant_id=tid, from_account="cash", to_account="whish", amount=2, date=day_midnight)
        # A paid payment collected today (for revert / method / delete / refund / bulk delete).
        paid1 = Payment(tenant_id=tid, customer_id=cust2, amount=11, currency='USD', fx_rate_to_reporting=1,
                        paid=True, paid_at=now, pre_payment=True)
        paid2 = Payment(tenant_id=tid, customer_id=cust2, amount=12, currency='USD', fx_rate_to_reporting=1,
                        paid=True, paid_at=now, pre_payment=True)
        paid3 = Payment(tenant_id=tid, customer_id=cust_del, amount=13, currency='USD', fx_rate_to_reporting=1,
                        paid=True, paid_at=now, pre_payment=True)
        paid4 = Payment(tenant_id=tid, customer_id=cust2, amount=14, currency='USD', fx_rate_to_reporting=1,
                        paid=True, paid_at=now, pre_payment=True)
        db.session.add_all([sp, ce, tr, paid1, paid2, paid3, paid4])
        db.session.commit()
        ids = dict(exp=exp.id, sup=sup.id, emp=emp.id, res=res.id, prov=prov.id, sp=sp.id, ce=ce.id,
                   tr=tr.id, p1=paid1.id, p2=paid2.id, p3=paid3.id, p4=paid4.id)

    # Everything below works while the day is open.
    assert client.post("/api/expenses", headers=a, json={
        "category": category, "amount": 1, "description": "ok", "date": _iso(today)}).status_code == 201

    r = _close(client, a, today, 0, 0)
    assert r.status_code == 201, r.get_data(as_text=True)

    # Expenses
    _blocked(client.post("/api/expenses", headers=a, json={
        "category": category, "amount": 5, "date": _iso(today)}), today)
    _blocked(client.post("/api/expenses", headers=a, json={
        "category": category, "amount": 5, "date": _iso(yesterday)}), yesterday)
    _blocked(client.put(f"/api/expenses/{ids['exp']}", headers=a, json={"amount": 9}), today)
    _blocked(client.delete(f"/api/expenses/{ids['exp']}", headers=a), today)
    _blocked(client.post(f"/api/employees/{ids['emp']}/payments", headers=a, json={"amount": 5}), today)
    # Moving an open-day expense INTO a closed day is blocked too; open days still work.
    ok = client.post("/api/expenses", headers=a, json={
        "category": category, "amount": 5, "date": _iso(tomorrow)})
    assert ok.status_code == 201
    _blocked(client.put(f"/api/expenses/{ok.get_json()['id']}", headers=a, json={"date": _iso(today)}), today)
    assert client.put(f"/api/expenses/{ok.get_json()['id']}", headers=a, json={"amount": 6}).status_code == 200

    # Supplier payments
    _blocked(client.post(f"/api/suppliers/{ids['sup']}/payments", headers=a, json={"amount": 5}), today)
    _blocked(client.put(f"/api/suppliers/{ids['sup']}/payments/{ids['sp']}", headers=a, json={"amount": 9}), today)
    _blocked(client.delete(f"/api/suppliers/{ids['sup']}/payments/{ids['sp']}", headers=a), today)
    assert client.post(f"/api/suppliers/{ids['sup']}/payments", headers=a,
                       json={"amount": 5, "payment_date": _iso(tomorrow)}).status_code == 201

    # Reseller collection / upstream top-up (dated now)
    _blocked(client.post(f"/api/resellers/{ids['res']}/collect_payment", headers=a, json={"amount": 5}), today)
    _blocked(client.post(f"/api/upstream-providers/{ids['prov']}/topup", headers=a, json={"amount": 5}), today)

    # Cash entries / transfers
    _blocked(client.post("/api/cash-entries", headers=a, json={
        "account": "cash", "amount": 5, "reason": "r", "date": _iso(today)}), today)
    _blocked(client.post("/api/cash-entries", headers=a, json={
        "account": "cash", "amount": 5, "reason": "r"}), today)  # blank date = tenant-zone today
    _blocked(client.delete(f"/api/cash-entries/{ids['ce']}", headers=a), today)
    _blocked(client.post("/api/account-transfers", headers=a, json={
        "from_account": "cash", "to_account": "whish", "amount": 5, "date": _iso(today)}), today)
    _blocked(client.delete(f"/api/account-transfers/{ids['tr']}", headers=a), today)
    assert client.post("/api/cash-entries", headers=a, json={
        "account": "cash", "amount": 5, "reason": "r", "date": _iso(tomorrow)}).status_code == 201

    # Customer payments
    _blocked(client.put(f"/api/payments/{unpaid_a}/mark_paid", headers=a, json={"action": "pay"}), today)
    _blocked(client.put(f"/api/payments/{unpaid_a}/mark_paid", headers=a, json={"action": "collect"}), today)
    _blocked(client.post("/api/payments/bulk_mark_paid", headers=a, json={"payment_ids": [unpaid_a]}), today)
    _blocked(client.post(f"/api/customers/{cust}/receive-payment", headers=a,
                         json={"amount": 5, "action": "pay"}), today)
    _blocked(client.post(f"/api/customers/{cust}/receive-payment", headers=a,
                         json={"amount": 5, "action": "collect"}), today)
    _blocked(client.post("/api/payments", headers=a, json={
        "customer_id": cust, "amount": 5, "reason": "prepay", "pre_payment": True}), today)
    _blocked(client.post(f"/api/payments/{ids['p1']}/refund", headers=a, json={"reason": "oops"}), today)
    _blocked(client.put(f"/api/payments/{ids['p1']}/revert", headers=a, json={"reason": "oops"}), today)
    _blocked(client.put(f"/api/payments/{ids['p1']}/method", headers=a, json={"method": "whish_transfer"}), today)
    _blocked(client.delete(f"/api/payments/{ids['p1']}", headers=a), today)
    _blocked(client.post("/api/payments/bulk_delete", headers=a, json={"payment_ids": [ids['p2'], unpaid_b]}), today)
    # Deleting customers with a paid payment on a locked day.
    _blocked(client.delete(f"/api/customers/{cust_del}", headers=a), today)
    _blocked(client.post("/api/customers/bulk_delete", headers=a, json={"customer_ids": [cust_del]}), today)

    # Nothing above changed anything.
    with flask_app.app_context():
        assert Payment.query.get(ids['p1']).paid is True and Payment.query.get(ids['p2']) is not None
        assert Payment.query.get(unpaid_a).paid is False and Customer.query.get(cust_del) is not None
        assert Expense.query.get(ids['exp']).amount == 7
        assert SupplierPayment.query.get(ids['sp']) is not None and CashEntry.query.get(ids['ce']) is not None

    # A customer with no paid payment on a locked day can still be deleted; unpaid rows are not cash.
    assert client.delete(f"/api/customers/{cust}", headers=a).status_code == 200

    # Reopen unlocks everything again.
    close_id = client.get("/api/day-closes", headers=a).get_json()[0]["id"]
    assert client.delete(f"/api/day-closes/{close_id}", headers=a).status_code == 200
    assert client.delete(f"/api/payments/{ids['p4']}", headers=a).status_code == 200
    assert client.delete(f"/api/customers/{cust_del}", headers=a).status_code == 200
    assert client.delete(f"/api/expenses/{ids['exp']}", headers=a).status_code == 200


# --------------------------------------------------------------------------- late confirm, Whish callback

def _collected_unpaid(tid, customer_id, amount, collected_at, collector_id):
    p = Payment(tenant_id=tid, customer_id=customer_id, amount=amount, currency='USD',
                fx_rate_to_reporting=1, paid=False, collected=True, collected_amount=amount,
                collected_at=collected_at, collected_by_id=collector_id)
    db.session.add(p)
    db.session.flush()
    return p


def test_late_confirm_is_allowed_and_flagged(app, client):
    a = make_tenant(client, "Biz", "dc_late")
    tid = _tid("dc_late")
    today = _today()
    day, nxt = today - timedelta(days=3), today - timedelta(days=2)
    _set_opening(client, a, day, cash=100, whish=0)
    plan = _make_plan(client, a, price=45)
    cust = _make_customer(client, a, plan, name="LateCust")
    with flask_app.app_context():
        collector = User.query.filter_by(username="dc_late").first().id
        noon = datetime.combine(day, datetime.min.time()) + timedelta(hours=9)  # noon in Beirut
        p1 = _collected_unpaid(tid, cust, 45, noon, collector)
        p2 = _collected_unpaid(tid, cust, 25, noon, collector)
        db.session.commit()
        p1_id, p2_id = p1.id, p2.id

    # Collected-but-unconfirmed money is not in the register yet, so expected = opening.
    assert _flow(client, a, day)["cash_end"] == 100
    assert _close(client, a, day, 100, 0).status_code == 201

    # The collection day is closed, but confirming the cash later is allowed...
    r = client.put(f"/api/payments/{p1_id}/mark_paid", headers=a, json={"action": "pay"})
    assert r.status_code == 200, r.get_data(as_text=True)
    r = client.post("/api/payments/bulk_mark_paid", headers=a, json={"payment_ids": [p2_id]})
    assert r.status_code == 200 and r.get_json()["succeeded"] == [p2_id], r.get_data(as_text=True)

    # ...and shows up on the closed day, flagged as changed after close.
    f = _flow(client, a, day)
    assert f["cash_end"] == 170
    assert f["close"]["late"] == {"cash": 70, "whish": 0, "count": 2}
    assert f["close"]["counted_cash"] == 100 and f["close"]["expected_cash"] == 100
    # Next day starts from counted + the late movement.
    assert _flow(client, a, nxt)["cash_start"] == 170


def test_whish_callback_is_allowed_on_a_closed_day(app, client):
    a = make_tenant(client, "Biz", "dc_whish")
    tid = _tid("dc_whish")
    today = _today()
    _set_opening(client, a, today - timedelta(days=2), cash=0, whish=0)
    plan = _make_plan(client, a, price=40)
    cust = _make_customer(client, a, plan, name="WhishCust")
    pay_id = _unpaid_payment_id(client, a, cust)
    token = secrets.token_hex(8)
    ext = "ord-" + secrets.token_hex(6)
    with flask_app.app_context():
        db.session.add(CustomerPaymentLink(
            tenant_id=tid, customer_id=cust, payment_id=pay_id, amount=40, currency='USD',
            view_token=secrets.token_hex(8), callback_token=token, whish_external_id=ext,
            status='pending', expires_at=datetime.utcnow() + timedelta(hours=1)))
        db.session.commit()
    assert _close(client, a, today, 0, 0).status_code == 201

    r = client.get(f"/api/customer-whish/success?order={ext}&token={token}", follow_redirects=False)
    assert r.status_code in (301, 302, 303), r.get_data(as_text=True)
    assert "status=success" in r.headers["Location"]
    with flask_app.app_context():
        assert Payment.query.get(pay_id).paid is True
    f = _flow(client, a, today)
    assert f["close"]["late"]["whish"] == 40 and f["close"]["late"]["count"] == 1
    assert f["whish_end"] == 40


def test_opening_edit_refused_while_a_close_exists(app, client):
    a = make_tenant(client, "Biz", "dc_open")
    today = _today()
    _set_opening(client, a, today - timedelta(days=3))
    cid = _close(client, a, today - timedelta(days=2), 100, 20).get_json()["id"]
    r = client.put("/api/reports/cash-opening", headers=a, json={"date": _iso(today), "amount": 5})
    assert r.status_code == 409 and "opening" in r.get_json()["error"].lower()
    assert client.put("/api/reports/cash-opening", headers=a, json={"date": None}).status_code == 409
    client.delete(f"/api/day-closes/{cid}", headers=a)
    assert client.put("/api/reports/cash-opening", headers=a,
                      json={"date": _iso(today), "amount": 5}).status_code == 200


# --------------------------------------------------------------------------- timezone

def test_timezone_setting_roundtrip_and_validation(app, client):
    a = make_tenant(client, "Biz", "dc_tz")
    assert client.get("/api/business-settings", headers=a).get_json()["settings"]["timezone"] == "Asia/Beirut"
    r = client.post("/api/business-settings", headers=a, data={"business_name": "Biz", "timezone": "Mars/Olympus"})
    assert r.status_code == 400 and "timezone" in r.get_json()["error"].lower()
    r = client.post("/api/business-settings", headers=a, data={"business_name": "Biz", "timezone": "Europe/Paris"})
    assert r.status_code == 200 and r.get_json()["settings"]["timezone"] == "Europe/Paris"
    assert client.get("/api/business-settings", headers=a).get_json()["settings"]["timezone"] == "Europe/Paris"
    # Saving other fields leaves the zone alone.
    client.post("/api/business-settings", headers=a, data={"business_name": "Biz 2"})
    assert client.get("/api/business-settings", headers=a).get_json()["settings"]["timezone"] == "Europe/Paris"


def test_beirut_late_evening_lands_on_the_right_day(app, client):
    a = make_tenant(client, "Biz", "dc_tzb")
    tid = _tid("dc_tzb")
    plan = _make_plan(client, a, price=10)
    cust = _make_customer(client, a, plan)
    with flask_app.app_context():
        # 20:30 UTC = 23:30 Beirut (UTC+3 in October, before DST ends on 25 Oct) -> still 5 Oct.
        # 21:30 UTC = 00:30 Beirut -> 6 Oct.
        for amt, when in ((5, datetime(2026, 10, 5, 20, 30)), (7, datetime(2026, 10, 5, 21, 30))):
            db.session.add(Payment(tenant_id=tid, customer_id=cust, amount=amt, currency='USD',
                                   fx_rate_to_reporting=1, paid=True, paid_at=when, pre_payment=True))
        db.session.commit()
    from datetime import date
    f5 = _flow(client, a, date(2026, 10, 5))
    f6 = _flow(client, a, date(2026, 10, 6))
    assert f5["cash_in"]["total"] == 5 and f6["cash_in"]["total"] == 7
    # The same answer for every viewer: legacy start/end params resolve to the same day.
    r = client.get("/api/reports/daily-cash", headers=a, query_string={
        "start_date": "2026-10-04T21:00:00.000Z", "end_date": "2026-10-05T21:00:00.000Z"})
    assert r.get_json()["cash_flow"]["day"] == "2026-10-05" and r.get_json()["cash_flow"]["cash_in"]["total"] == 5
    assert client.get("/api/reports/daily-cash", headers=a, query_string={"day": "bad"}).status_code == 400


def test_dst_changes_the_local_day_boundary(app, client):
    a = make_tenant(client, "Biz", "dc_dst")
    tid = _tid("dc_dst")
    plan = _make_plan(client, a, price=10)
    cust = _make_customer(client, a, plan)
    from datetime import date
    with flask_app.app_context():
        # 22:30 UTC: winter (UTC+2) is already 00:30 the next day; summer (UTC+3) is 01:30 the next day.
        # 21:30 UTC: winter is 23:30 the same day; summer is 00:30 the next day.
        rows = [(3, datetime(2026, 12, 10, 21, 30)),   # winter, 21:30Z -> 23:30 on 10 Dec
                (4, datetime(2026, 7, 10, 21, 30))]    # summer, 21:30Z -> 00:30 on 11 Jul
        for amt, when in rows:
            db.session.add(Payment(tenant_id=tid, customer_id=cust, amount=amt, currency='USD',
                                   fx_rate_to_reporting=1, paid=True, paid_at=when, pre_payment=True))
        db.session.commit()
    assert _flow(client, a, date(2026, 12, 10))["cash_in"]["total"] == 3
    assert _flow(client, a, date(2026, 12, 11))["cash_in"]["total"] == 0
    assert _flow(client, a, date(2026, 7, 10))["cash_in"]["total"] == 0
    assert _flow(client, a, date(2026, 7, 11))["cash_in"]["total"] == 4
    # Unit-level: a calendar value is the typed date, an instant is converted.
    with flask_app.app_context():
        s = BusinessSettings(tenant_id=tid, business_name="x", address="", mobile="", timezone="Asia/Beirut")
        assert _local_day(s, datetime(2026, 12, 10), True) == date(2026, 12, 10)
        assert _local_day(s, datetime(2026, 12, 10), False) == date(2026, 12, 10)
        assert _local_day(s, datetime(2026, 12, 10, 22, 30), False) == date(2026, 12, 11)
        assert _local_day(s, datetime(2026, 7, 10, 22, 30), False) == date(2026, 7, 11)
        assert _local_day(s, datetime(2026, 12, 10, 1, 30), True) == date(2026, 12, 10)


def test_tenant_timezone_utc_changes_days(app, client):
    a = make_tenant(client, "Biz", "dc_tzu")
    tid = _tid("dc_tzu")
    plan = _make_plan(client, a, price=10)
    cust = _make_customer(client, a, plan)
    from datetime import date
    with flask_app.app_context():
        db.session.add(Payment(tenant_id=tid, customer_id=cust, amount=9, currency='USD', fx_rate_to_reporting=1,
                               paid=True, paid_at=datetime(2026, 10, 5, 22, 30), pre_payment=True))
        db.session.commit()
    assert _flow(client, a, date(2026, 10, 6))["cash_in"]["total"] == 9  # 01:30 Beirut
    client.post("/api/business-settings", headers=a, data={"business_name": "Biz", "timezone": "UTC"})
    assert _flow(client, a, date(2026, 10, 6))["cash_in"]["total"] == 0
    assert _flow(client, a, date(2026, 10, 5))["cash_in"]["total"] == 9


def test_default_dates_use_the_tenant_zone_today(app, client):
    a = make_tenant(client, "Biz", "dc_def")
    r = client.post("/api/cash-entries", headers=a, json={"account": "cash", "amount": 5, "reason": "r"})
    assert r.status_code == 201
    assert r.get_json()["date"] == _iso(_today())


def test_late_does_not_cascade_to_the_next_close(app, client):
    a = make_tenant(client, "Biz", "dc_casc")
    tid = _tid("dc_casc")
    today = _today()
    d1, d2 = today - timedelta(days=3), today - timedelta(days=2)
    _set_opening(client, a, d1, cash=100, whish=0)
    plan = _make_plan(client, a, price=45)
    cust = _make_customer(client, a, plan, name="CascCust")
    with flask_app.app_context():
        collector = User.query.filter_by(username="dc_casc").first().id
        noon = datetime.combine(d1, datetime.min.time()) + timedelta(hours=9)
        p1 = _collected_unpaid(tid, cust, 45, noon, collector)
        db.session.commit()
        p1_id = p1.id
    assert _close(client, a, d1, 100, 0).status_code == 201
    assert _close(client, a, d2, 100, 0).status_code == 201
    assert client.put(f"/api/payments/{p1_id}/mark_paid", headers=a, json={"action": "pay"}).status_code == 200
    f1, f2 = _flow(client, a, d1), _flow(client, a, d2)
    assert f1["close"]["late"] == {"cash": 45, "whish": 0, "count": 1}
    assert f2["close"]["late"] == {"cash": 0, "whish": 0, "count": 0}
    # The money is still in the box from day 2 on (start = counted + the late amount).
    assert f2["cash_start"] == 145


def test_timezone_change_refused_while_closes_exist(app, client):
    a = make_tenant(client, "Biz", "dc_tzlock")
    today = _today()
    _set_opening(client, a, today - timedelta(days=3))
    cid = _close(client, a, today - timedelta(days=2), 100, 20).get_json()["id"]
    r = client.post("/api/business-settings", headers=a, data={"business_name": "Biz", "timezone": "UTC"})
    assert r.status_code == 409 and r.get_json()["code"] == "timezone_locked"
    assert "Reopen" in r.get_json()["error"]
    # Re-sending the same value is fine, and so is any other field.
    assert client.post("/api/business-settings", headers=a,
                       data={"business_name": "Biz", "timezone": "Asia/Beirut"}).status_code == 200
    client.delete(f"/api/day-closes/{cid}", headers=a)
    assert client.post("/api/business-settings", headers=a,
                       data={"business_name": "Biz", "timezone": "UTC"}).status_code == 200
