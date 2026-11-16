"""Finance roadmap step 0: the read-only reconciliation check. It must stay
silent while balances agree with their history, flag one that doesn't, and
never change any data."""
from tests.conftest import make_tenant, auth_headers
from tests.test_daily_cash_report import _make_plan, _make_customer, _unpaid_payment_id
from tests.test_money_out_amount_validation import _supplier, _employee
from tests.test_profit_includes_resellers_and_upstream import _reseller
from tests.test_receive_payment import _customer, _bill, _prepay
from tests.test_day_close import _cat
from app import app as flask_app, db, Customer, Payment


def _report(client, hdr):
    r = client.get("/api/reports/reconciliation", headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_clean_history_reports_nothing(app, client):
    a = make_tenant(client, "Biz", "rec1")

    # Customer: every kind of row, through the real routes.
    cid = _customer(client, a)
    _prepay(client, a, cid, 50)
    _bill(client, a, cid, 25)                      # settled from credit
    open_bill = _bill(client, a, cid, 40)          # 25 from credit, 15 left open
    cust2 = _make_customer(client, a, _make_plan(client, a, price=40))
    pid = _unpaid_payment_id(client, a, cust2)
    client.put(f"/api/payments/{pid}/mark_paid", headers=a, json={"action": "pay"})
    client.post(f"/api/payments/{pid}/refund", headers=a, json={"reason": "r", "amount": 10})
    assert open_bill

    # Reseller, supplier, employee.
    res = _reseller(client, a)
    client.post(f"/api/resellers/{res}/add_credit", headers=a, json={"amount": 300})
    client.post(f"/api/resellers/{res}/apply_discount", headers=a, json={"amount": 20})
    client.post(f"/api/resellers/{res}/collect_payment", headers=a, json={"amount": 100})
    sup = _supplier(client, a)
    cat = _cat(client, a)
    r = client.post("/api/expenses", headers=a, json={
        "amount": 90, "category": cat, "description": "router", "date": "2026-11-01",
        "is_credit": True, "supplier_id": sup})
    assert r.status_code == 201, r.get_data(as_text=True)
    client.post(f"/api/suppliers/{sup}/payments", headers=a, json={"amount": 30})
    emp = _employee(client, a)
    client.post(f"/api/employees/{emp}/charges", headers=a, json={"amount": 100, "type": "salary"})
    client.post(f"/api/employees/{emp}/charges", headers=a, json={"amount": 10, "type": "deduction"})
    client.post(f"/api/employees/{emp}/payments", headers=a, json={"amount": 40})

    report = _report(client, a)
    assert report["summary"] == {"customers": 0, "resellers": 0, "suppliers": 0, "employees": 0,
                                 "over_refunded_payments": 0, "paid_without_money": 0}, report


def test_flags_a_balance_its_history_does_not_explain_and_changes_nothing(app, client):
    a = make_tenant(client, "Biz", "rec2")
    cid = _customer(client, a)
    _bill(client, a, cid, 25)
    with flask_app.app_context():
        # Damage of the kind the old bugs left: credit with no payment behind it,
        # and a bill marked paid with no money (bypassing the balance log).
        db.session.execute(db.update(Customer).where(Customer.id == cid).values(balance=10))
        tid = db.session.get(Customer, cid).tenant_id
        db.session.add(Payment(tenant_id=tid, customer_id=cid, amount=30, paid=True, collected=True,
                               collected_amount=0, pre_payment=False))
        db.session.commit()

    report = _report(client, a)
    assert report["summary"]["customers"] == 1
    row = report["customers"][0]
    assert row["id"] == cid and row["stored_balance"] == 10
    assert row["balance_from_history"] == -25 and row["difference"] == 35
    # (_customer() itself zeroes the balance directly, which the log records as a manual edit.)
    assert row["unexplained"] == 35 - row["manual_edits_logged"] != 0
    assert [p["amount"] for p in report["paid_without_money"]] == [30]

    assert _report(client, a) == report  # idempotent
    with flask_app.app_context():
        assert float(db.session.get(Customer, cid).balance) == 10


def test_manual_edit_is_shown_as_explained(app, client):
    a = make_tenant(client, "Biz", "rec3")
    cid = _make_customer(client, a, _make_plan(client, a, price=40))
    assert _report(client, a)["summary"]["customers"] == 0
    with flask_app.app_context():
        balance = float(db.session.get(Customer, cid).balance)
    r = client.put(f"/api/customers/{cid}", headers=a, json={"balance": balance + 40})
    assert r.status_code == 200, r.get_data(as_text=True)

    row = _report(client, a)["customers"][0]
    assert row["difference"] == 40 and row["manual_edits_logged"] == 40 and row["unexplained"] == 0


def test_reconciliation_is_admin_only(app, client):
    make_tenant(client, "Biz", "rec4")
    finance = auth_headers(client, "rec4_fin", role="finance")
    assert client.get("/api/reports/reconciliation", headers=finance).status_code == 403
