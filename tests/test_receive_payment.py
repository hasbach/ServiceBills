"""Customer-level money handling and the fixed credit settlement -- see
docs/superpowers/specs/2026-10-05-receive-payment-and-statement-design.md."""
from datetime import datetime

from tests.conftest import make_tenant, auth_headers
from tests.test_daily_cash_report import _make_plan, _today_range
from app import app as flask_app, db, Payment, Customer, User, apply_customer_balance_to_unpaid_payments


def _customer(client, hdr, name="Cust"):
    """A customer with no bills yet (starts today, nothing billed)."""
    plan = _make_plan(client, hdr, price=25)
    r = client.post("/api/customers", headers=hdr, json={
        "name": name, "phone": "111", "address": "addr", "subscription_plan_id": plan,
        "subscription_start_date": datetime.utcnow().strftime("%Y-%m-%d")})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    cid = r.get_json()["customer_id"]
    with flask_app.app_context():
        # Creation bills the first cycle; start every test from a clean slate.
        Payment.query.filter_by(customer_id=cid).delete()
        db.session.get(Customer, cid).balance = 0
        db.session.commit()
    return cid


def _bill(client, hdr, cid, amount=25, date="2026-09-01"):
    """A charge through the real path (debits the balance)."""
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": cid, "amount": amount, "reason": "Subscription", "date": date})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["payment"]["id"]


def _prepay(client, hdr, cid, amount):
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": cid, "amount": amount, "reason": "Advance", "pre_payment": True})
    assert r.status_code == 201, r.get_data(as_text=True)


def _state(cid):
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        bills = Payment.query.filter_by(customer_id=cid).filter(Payment.pre_payment.isnot(True)).order_by(
            Payment.date, Payment.id).all()
        return round(float(c.balance), 2), [(round(float(b.amount), 2), bool(b.paid)) for b in bills]


def _settle(cid):
    with flask_app.app_context():
        apply_customer_balance_to_unpaid_payments(db.session.get(Customer, cid))
        db.session.commit()


# --- the fixed settlement -----------------------------------------------------

def test_credit_equal_to_new_bill_settles_it(app, client):
    """The DeltaNet case: +25 credit, then a 25 bill -> balance 0 AND bill paid."""
    a = make_tenant(client, "B", "s1")
    cid = _customer(client, a)
    _prepay(client, a, cid, 25)
    _bill(client, a, cid, 25)
    assert _state(cid) == (0, [(25, True)])
    with flask_app.app_context():
        assert Payment.query.filter_by(customer_id=cid, pre_payment=False).one().settled_from_credit is True


def test_credit_above_bill_keeps_the_rest(app, client):
    a = make_tenant(client, "B", "s2")
    cid = _customer(client, a)
    _prepay(client, a, cid, 50)
    _bill(client, a, cid, 25)
    assert _state(cid) == (25, [(25, True)])


def test_credit_covers_two_bills_and_leaves_change(app, client):
    a = make_tenant(client, "B", "s3")
    cid = _customer(client, a)
    _bill(client, a, cid, 25, "2026-08-01")
    _bill(client, a, cid, 25, "2026-09-01")
    _prepay(client, a, cid, 60)
    assert _state(cid) == (10, [(25, True), (25, True)])


def test_partial_credit_splits_the_oldest_uncovered_bill(app, client):
    a = make_tenant(client, "B", "s4")
    cid = _customer(client, a)
    _bill(client, a, cid, 25, "2026-08-01")
    _bill(client, a, cid, 25, "2026-09-01")
    _prepay(client, a, cid, 30)
    balance, bills = _state(cid)
    assert balance == -20
    assert sorted(bills) == sorted([(25, True), (5, True), (20, False)])


def test_settlement_is_idempotent_and_skips_collected_bills(app, client):
    a = make_tenant(client, "B", "s5")
    cid = _customer(client, a)
    pid = _bill(client, a, cid, 25)
    client.put(f"/api/payments/{pid}/mark_paid", headers=a, json={"action": "collect"})
    _prepay(client, a, cid, 25)  # balance 0, but the bill is in a collector's hands
    _settle(cid)
    _settle(cid)
    assert _state(cid) == (0, [(25, False)])


# --- receive payment ------------------------------------------------------------

def test_receive_30_against_two_25_bills(app, client):
    a = make_tenant(client, "B", "r1")
    cid = _customer(client, a)
    _bill(client, a, cid, 25, "2026-08-01")
    _bill(client, a, cid, 25, "2026-09-01")
    r = client.post(f"/api/customers/{cid}/receive-payment", headers=a, json={"amount": 30})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["bills"] == 2
    balance, bills = _state(cid)
    assert balance == -20
    assert sorted(bills) == sorted([(25, True), (5, True), (20, False)])
    with flask_app.app_context():
        paid = Payment.query.filter_by(customer_id=cid, paid=True).all()
        assert all(p.received_by_id for p in paid)

    log = client.get(f"/api/customers/{cid}/balance-log", headers=a).get_json()
    assert log[0]["reason"] == "Payment received $30.00"
    assert (log[0]["balance_before"], log[0]["balance_after"]) == (-50, -20)


def test_receive_more_than_owed_keeps_credit(app, client):
    a = make_tenant(client, "B", "r2")
    cid = _customer(client, a)
    _bill(client, a, cid, 25, "2026-08-01")
    _bill(client, a, cid, 25, "2026-09-01")
    r = client.post(f"/api/customers/{cid}/receive-payment", headers=a, json={"amount": 60})
    assert r.get_json()["credit_added"] == 10
    assert _state(cid) == (10, [(25, True), (25, True)])
    # One statement row for the whole receipt, credit included.
    newest = client.get(f"/api/customers/{cid}/balance-log", headers=a).get_json()[0]
    assert (newest["balance_before"], newest["balance_after"], newest["reason"]) == (-50, 10, "Payment received $60.00")


def test_receive_starts_with_the_clicked_bill(app, client):
    a = make_tenant(client, "B", "r3")
    cid = _customer(client, a)
    old = _bill(client, a, cid, 25, "2026-08-01")
    new = _bill(client, a, cid, 25, "2026-09-01")
    client.post(f"/api/customers/{cid}/receive-payment", headers=a, json={"amount": 25, "first_payment_id": new})
    with flask_app.app_context():
        assert db.session.get(Payment, new).paid is True
        assert db.session.get(Payment, old).paid is False


def test_receive_counts_as_cash_unless_whish_transfer(app, client):
    a = make_tenant(client, "B", "r4")
    cid = _customer(client, a)
    _bill(client, a, cid, 25, "2026-08-01")
    _bill(client, a, cid, 25, "2026-09-01")
    client.post(f"/api/customers/{cid}/receive-payment", headers=a, json={"amount": 25})
    client.post(f"/api/customers/{cid}/receive-payment", headers=a,
                json={"amount": 25, "method": "whish_transfer", "reference": "TX1"})
    start, end = _today_range()
    body = client.get("/api/reports/daily-cash", headers=a,
                      query_string={"start_date": start, "end_date": end}).get_json()
    assert body["grand_total"] == 25
    assert body["cash_flow"]["cash_in"]["total"] == 25


def test_receive_validation_and_roles(app, client):
    a = make_tenant(client, "B", "r5")
    cid = _customer(client, a)
    _bill(client, a, cid, 25)
    assert client.post(f"/api/customers/{cid}/receive-payment", headers=a, json={"amount": 0}).status_code == 400
    assert client.post(f"/api/customers/{cid}/receive-payment", headers=a,
                       json={"amount": 5, "method": "bitcoin"}).status_code == 400
    cashier = auth_headers(client, "cash_r5", role="cashier")
    assert client.post(f"/api/customers/{cid}/receive-payment", headers=cashier,
                       json={"amount": 5, "action": "pay"}).status_code == 403
    b = make_tenant(client, "Other", "r5b")
    assert client.post(f"/api/customers/{cid}/receive-payment", headers=b, json={"amount": 5}).status_code == 404


# --- collect, then confirm all at once -----------------------------------------

def test_collect_lump_sum_then_confirm_once(app, client):
    a = make_tenant(client, "B", "c1")
    cid = _customer(client, a)
    _bill(client, a, cid, 25, "2026-08-01")
    _bill(client, a, cid, 25, "2026-09-01")
    cashier = auth_headers(client, "cash_c1", role="cashier")
    r = client.post(f"/api/customers/{cid}/receive-payment", headers=cashier,
                    json={"amount": 30, "action": "collect"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _state(cid) == (-50, [(25, False), (25, False)])  # nothing confirmed yet
    with flask_app.app_context():
        collected = sorted(float(p.collected_amount) for p in Payment.query.filter_by(customer_id=cid, collected=True))
        assert collected == [5, 25]

    assert client.post(f"/api/customers/{cid}/receive-payment", headers=cashier,
                       json={"amount": 100, "action": "collect"}).status_code == 400  # more than owed

    assert client.post(f"/api/customers/{cid}/confirm-collected", headers=cashier).status_code == 403
    r = client.post(f"/api/customers/{cid}/confirm-collected", headers=a)
    assert r.status_code == 200, r.get_data(as_text=True)
    balance, bills = _state(cid)
    assert balance == -20
    assert sorted(bills) == sorted([(25, True), (5, True), (20, False)])
    assert client.post(f"/api/customers/{cid}/confirm-collected", headers=a).status_code == 400


# --- lost-credit review -----------------------------------------------------------

def _legacy_auto_settled(cid, amount=25):
    """A row as the OLD settlement left it: paid, nobody behind it."""
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        db.session.add(Payment(tenant_id=c.tenant_id, customer_id=cid, amount=amount, paid=True,
                               paid_at=datetime.utcnow(), pre_payment=False, date=datetime(2026, 7, 1)))
        db.session.commit()


def test_credit_review_lists_and_restores(app, client):
    a = make_tenant(client, "B", "v1")
    cid = _customer(client, a, name="Hit")
    other = _customer(client, a, name="Clean")
    _legacy_auto_settled(cid)
    pid = _bill(client, a, other, 25)
    client.put(f"/api/payments/{pid}/mark_paid", headers=a, json={"action": "pay"})  # staff-paid: not listed

    body = client.get("/api/customers/credit-review", headers=a).get_json()
    assert body["count"] == 1
    entry = body["customers"][0]
    assert (entry["customer_name"], entry["suggested_credit"]) == ("Hit", 25)

    r = client.post(f"/api/customers/{cid}/credit-review", headers=a, json={"action": "restore"})
    assert r.status_code == 200
    assert _state(cid)[0] == 25
    assert client.get("/api/customers/credit-review", headers=a).get_json()["count"] == 0
    assert client.post(f"/api/customers/{cid}/credit-review", headers=a,
                       json={"action": "restore"}).status_code == 400  # never twice
    log = client.get(f"/api/customers/{cid}/balance-log", headers=a).get_json()
    assert log[0]["reason"].startswith("Correction: credit lost to auto-settlement restored")


def test_credit_review_dismiss_changes_nothing(app, client):
    a = make_tenant(client, "B", "v2")
    cid = _customer(client, a)
    _legacy_auto_settled(cid)
    client.post(f"/api/customers/{cid}/credit-review", headers=a, json={"action": "dismiss"})
    assert _state(cid)[0] == 0
    assert client.get("/api/customers/credit-review", headers=a).get_json()["count"] == 0


def test_reseller_transfer_closures_are_not_flagged_for_review(app, client):
    """Moving a customer under a reseller closes their bills (collected,
    collected_amount 0, nobody recorded) -- a debt transfer, not lost credit."""
    a = make_tenant(client, "B", "v4")
    cid = _customer(client, a)
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        db.session.add(Payment(tenant_id=c.tenant_id, customer_id=cid, amount=25, paid=True, collected=True,
                               collected_amount=0, paid_at=None, pre_payment=False, date=datetime(2026, 7, 1)))
        db.session.commit()
    assert client.get("/api/customers/credit-review", headers=a).get_json()["count"] == 0


def test_fixed_settlement_rows_are_not_flagged_for_review(app, client):
    a = make_tenant(client, "B", "v3")
    cid = _customer(client, a)
    _prepay(client, a, cid, 25)
    _bill(client, a, cid, 25)  # settled from credit by the fixed code
    assert client.get("/api/customers/credit-review", headers=a).get_json()["count"] == 0


# --- statement --------------------------------------------------------------------

def test_statement_reasons(app, client):
    a = make_tenant(client, "B", "st1")
    cid = _customer(client, a)
    pid = _bill(client, a, cid, 25, "2026-09-01")
    client.put(f"/api/payments/{pid}/mark_paid", headers=a, json={"action": "pay"})
    paid, billed = client.get(f"/api/customers/{cid}/balance-log", headers=a).get_json()[:2]
    assert billed["reason"] == "Bill: Subscription (2026-09-01)"
    assert (billed["balance_before"], billed["balance_after"]) == (0, -25)
    assert paid["reason"] == "Payment received for bill of 2026-09-01"
    assert paid["changed_by"] == "st1"
    b = make_tenant(client, "Other", "st1b")
    assert client.get(f"/api/customers/{cid}/balance-log", headers=b).status_code == 404
