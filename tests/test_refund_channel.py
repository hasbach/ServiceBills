"""Refunds carry the account they were paid out of (cash or Whish) and the
original payment's currency/fx, and the daily cash flow books them there.
Reverting a refund puts the money back on the customer's balance."""
from datetime import datetime

from tests.conftest import make_tenant
from app import app as flask_app, db, Payment, Customer
from tests.test_daily_cash_report import _make_plan, _make_customer, _unpaid_payment_id
from tests.test_daily_cash_flow import _tenant_id, _totals
from tests.test_day_close import _flow as _day_flow, _today as _business_today


def _todays_flow(client, hdr):
    """Cash flow for the business's own today (Asia/Beirut), the day a refund
    issued now lands on -- not the UTC date, which differs after local midnight."""
    return _day_flow(client, hdr, _business_today())


def _paid_row(tid, cust, amount, **kw):
    with flask_app.app_context():
        kw.setdefault("currency", "USD")
        kw.setdefault("fx_rate_to_reporting", 1)
        p = Payment(tenant_id=tid, customer_id=cust, amount=amount, paid=True,
                    paid_at=datetime.utcnow(), **kw)
        db.session.add(p)
        db.session.commit()
        return p.id


def _refund(client, hdr, pid, **body):
    body.setdefault("reason", "test refund")
    r = client.post(f"/api/payments/{pid}/refund", headers=hdr, json=body)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


def _balance(cust):
    with flask_app.app_context():
        return float(db.session.get(Customer, cust).balance)


def test_cash_refund_goes_out_of_cash(app, client):
    a = make_tenant(client, "Biz A", "rf1")
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    pid = _unpaid_payment_id(client, a, cust)
    client.put(f"/api/payments/{pid}/mark_paid", headers=a, json={"action": "pay"})

    body = _refund(client, a, pid, amount=15)
    assert body["paid_via"] == "cash" and body["currency"] == "USD"

    flow = _todays_flow(client, a)
    assert _totals(flow["cash_out"]).get("Customer refunds") == 15
    assert "Customer refunds" not in _totals(flow["whish_out"])


def test_whish_payment_refund_defaults_to_whish(app, client):
    a = make_tenant(client, "Biz A", "rf2")
    tid = _tenant_id("rf2")
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    for via in ("whish", "whish_transfer"):
        pid = _paid_row(tid, cust, 20, collected_via=via)
        assert _refund(client, a, pid)["paid_via"] == "whish"

    flow = _todays_flow(client, a)
    assert _totals(flow["whish_out"]).get("Customer refunds") == 40
    assert "Customer refunds" not in _totals(flow["cash_out"])

    # A Whish refund is money out -- not listed as an incoming Whish transfer.
    r = client.get("/api/reports/customer-whish-payments", headers=a)
    assert r.status_code == 200, r.get_data(as_text=True)
    links = r.get_json()["links"]
    assert any(row.get("source") == "manual_transfer" for row in links)  # the original transfer is listed
    with flask_app.app_context():
        refund_ids = {p.id for p in Payment.query.filter_by(tenant_id=tid, is_refund=True)}
    assert not refund_ids & {r.get("payment_id") for r in links}


def test_refund_channel_can_be_overridden_and_is_validated(app, client):
    a = make_tenant(client, "Biz A", "rf3")
    tid = _tenant_id("rf3")
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    whish_pid = _paid_row(tid, cust, 20, collected_via="whish_transfer")
    cash_pid = _paid_row(tid, cust, 30, pre_payment=True)

    assert _refund(client, a, whish_pid, paid_via="cash")["paid_via"] == "cash"
    assert _refund(client, a, cash_pid, paid_via="whish")["paid_via"] == "whish"
    r = client.post(f"/api/payments/{cash_pid}/refund", headers=a, json={"reason": "x", "paid_via": "card"})
    assert r.status_code == 400

    flow = _todays_flow(client, a)
    assert _totals(flow["cash_out"]).get("Customer refunds") == 20
    assert _totals(flow["whish_out"]).get("Customer refunds") == 30


def test_refund_copies_currency_and_fx(app, client):
    a = make_tenant(client, "Biz A", "rf4")
    tid = _tenant_id("rf4")
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    pid = _paid_row(tid, cust, 900000, currency="LBP", fx_rate_to_reporting=0.00001, pre_payment=True)

    body = _refund(client, a, pid, amount=450000)
    assert body["currency"] == "LBP"
    with flask_app.app_context():
        row = db.session.get(Payment, body["refund_payment_id"])
        assert row.currency == "LBP"
        assert float(row.fx_rate_to_reporting) == 0.00001

    flow = _todays_flow(client, a)
    assert _totals(flow["cash_out"])["Customer refunds"] == 4.5


def test_reverting_a_refund_credits_the_balance_back(app, client):
    a = make_tenant(client, "Biz A", "rf5")
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    pid = _unpaid_payment_id(client, a, cust)
    client.put(f"/api/payments/{pid}/mark_paid", headers=a, json={"action": "pay"})
    before = _balance(cust)

    refund_id = _refund(client, a, pid, amount=25)["refund_payment_id"]
    assert _balance(cust) == before - 25

    r = client.put(f"/api/payments/{refund_id}/revert", headers=a, json={"reason": "issued by mistake"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["customer_new_balance"] == before
    assert _balance(cust) == before

    # A voided refund can't be re-settled through mark_paid (that would
    # credit the balance), and deleting it doesn't move the balance again.
    assert client.put(f"/api/payments/{refund_id}/mark_paid", headers=a,
                      json={"action": "pay"}).status_code == 400
    bulk = client.post("/api/payments/bulk_mark_paid", headers=a, json={"payment_ids": [refund_id]}).get_json()
    assert bulk["failed"] and bulk["failed"][0]["id"] == refund_id
    assert client.delete(f"/api/payments/{refund_id}", headers=a).status_code == 200
    assert _balance(cust) == before

    flow = _todays_flow(client, a)
    assert "Customer refunds" not in _totals(flow["cash_out"])


def test_reverting_a_refund_on_a_closed_day_is_blocked(app, client):
    from datetime import timedelta
    from tests.test_day_close import _today as _local_today, _set_opening, _close, _blocked
    a = make_tenant(client, "Biz A", "rf6")
    tid = _tenant_id("rf6")
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    day = _local_today() - timedelta(days=2)
    with flask_app.app_context():
        noon = datetime.combine(day, datetime.min.time()) + timedelta(hours=9)  # midday Beirut, in UTC
        p = Payment(tenant_id=tid, customer_id=cust, amount=10, currency="USD", fx_rate_to_reporting=1,
                    paid=True, paid_at=noon, is_refund=True, collected_via="whish_transfer")
        db.session.add(p)
        db.session.commit()
        refund_id = p.id
    _set_opening(client, a, day - timedelta(days=1))
    assert _close(client, a, day, 100, 10).status_code == 201
    before = _balance(cust)

    _blocked(client.put(f"/api/payments/{refund_id}/revert", headers=a, json={"reason": "late"}), day)
    assert _balance(cust) == before


def test_payments_list_exposes_refund_fields_for_the_ui(app, client):
    a = make_tenant(client, "Biz A", "rf7")
    tid = _tenant_id("rf7")
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    pid = _paid_row(tid, cust, 900000, currency="LBP", fx_rate_to_reporting=0.00001, collected_via="whish")
    refund_id = _refund(client, a, pid, amount=1000, reason="dup charge")["refund_payment_id"]

    rows = client.get("/api/payments", headers=a, query_string={"customer_id": cust}).get_json()["payments"]
    row = next(p for p in rows if p["id"] == refund_id)
    assert (row["currency"], row["refund_reason"], row["collected_via"], row["is_refund"]) == \
        ("LBP", "dup charge", "whish_transfer", True)
