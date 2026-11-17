"""Finance roadmap 1A, fixes 4 and 5 (owner rules, 2026-11-16):
- leaving a reseller moves no money -- past charges stay with that reseller;
- joining a reseller hands the customer's unpaid bills to the reseller instead
  of marking them paid (no money came in, so it is not revenue)."""
from datetime import datetime

from dateutil.relativedelta import relativedelta

from app import db, Customer, Reseller, ResellerPayment, Payment, generate_missing_payments
from tests.conftest import make_tenant
from tests.test_receive_payment import _customer, _bill, _prepay
from tests.test_reseller_billing import _make_plan, _make_reseller


def _reseller_customer(app, plan_id, reseller_id, months_ago=3):
    """A customer under a reseller, billed for `months_ago` cycles by the scheduler."""
    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        start = datetime.utcnow() - relativedelta(months=months_ago)
        customer = Customer(tenant_id=reseller.tenant_id, name="Cust1", phone="111", address="addr",
                            subscription_plan_id=plan_id, subscription_start_date=start,
                            subscription_expiry_date=start + relativedelta(months=1),
                            is_subscription_active=True, balance=0.0, reseller_id=reseller.id)
        db.session.add(customer)
        db.session.commit()
        generate_missing_payments(reseller.tenant_id)
        return customer.id, reseller.tenant_id


def _move(client, hdr, customer_id, reseller_id):
    r = client.put(f"/api/customers/{customer_id}", headers=hdr, json={"reseller_id": reseller_id})
    assert r.status_code == 200, r.get_data(as_text=True)


def _reseller_balance(app, reseller_id):
    with app.app_context():
        return float(db.session.get(Reseller, reseller_id).balance)


def test_leaving_a_reseller_for_independent_moves_no_money(app, client):
    a = make_tenant(client, "Biz A", "rm1")
    plan = _make_plan(client, a)
    res = _make_reseller(client, a)
    cid, tid = _reseller_customer(app, plan, res)
    assert _reseller_balance(app, res) == 300
    # The reseller has paid for everything.
    assert client.post(f"/api/resellers/{res}/collect_payment", headers=a, json={"amount": 300}).status_code == 200

    _move(client, a, cid, "")

    assert _reseller_balance(app, res) == 0          # was -300 before the fix
    with app.app_context():
        customer = db.session.get(Customer, cid)
        assert customer.reseller_id is None and float(customer.balance) == 0
        assert Payment.query.filter_by(customer_id=cid).count() == 0   # was a 300 "assumed debt" bill
        assert ResellerPayment.query.filter_by(reseller_id=res, customer_id=cid).count() == 3  # history untouched


def test_cycles_billed_to_the_reseller_are_not_billed_again_to_the_customer(app, client):
    a = make_tenant(client, "Biz A", "rm2")
    plan = _make_plan(client, a)
    res = _make_reseller(client, a)
    cid, tid = _reseller_customer(app, plan, res)
    _move(client, a, cid, "")

    with app.app_context():
        generate_missing_payments(tid)
        assert Payment.query.filter_by(customer_id=cid).count() == 0
        # ...and the next cycle, once due, is billed to the customer exactly once.
        customer = db.session.get(Customer, cid)
        customer.subscription_expiry_date = datetime.utcnow() - relativedelta(days=1)
        db.session.commit()
        generate_missing_payments(tid)
        generate_missing_payments(tid)
        bills = Payment.query.filter_by(customer_id=cid).all()
        assert [float(b.amount) for b in bills] == [100.0]
        assert float(db.session.get(Customer, cid).balance) == -100
    assert _reseller_balance(app, res) == 300


def test_moving_between_resellers_moves_no_money(app, client):
    a = make_tenant(client, "Biz A", "rm3")
    plan = _make_plan(client, a)
    res1, res2 = _make_reseller(client, a, "Res1"), _make_reseller(client, a, "Res2")
    cid, tid = _reseller_customer(app, plan, res1)

    _move(client, a, cid, res2)

    assert _reseller_balance(app, res1) == 300
    assert _reseller_balance(app, res2) == 0
    with app.app_context():
        assert ResellerPayment.query.filter_by(reseller_id=res2).count() == 0


def test_joining_a_reseller_hands_over_unpaid_bills_without_creating_revenue(app, client):
    a = make_tenant(client, "Biz A", "rm4")
    res = _make_reseller(client, a)
    cid = _customer(client, a)
    _bill(client, a, cid, 25, "2026-08-01")
    _bill(client, a, cid, 25, "2026-09-01")

    _move(client, a, cid, res)

    assert _reseller_balance(app, res) == 50
    with app.app_context():
        customer = db.session.get(Customer, cid)
        assert customer.reseller_id == res and float(customer.balance) == 0
        assert Payment.query.filter_by(customer_id=cid).count() == 0   # not left behind as "paid"
        row = ResellerPayment.query.filter_by(reseller_id=res, customer_id=cid).one()
        assert row.type == "credit_added" and float(row.amount) == 50
        assert "2 unpaid bills" in row.description
    d = client.get("/api/dashboard", headers=a).get_json()
    assert d["totalRevenue"] == 0                       # was 50 of phantom revenue


def test_joining_a_reseller_leaves_paid_bills_and_customer_credit_alone(app, client):
    a = make_tenant(client, "Biz A", "rm5")
    res = _make_reseller(client, a)
    cid = _customer(client, a)
    _prepay(client, a, cid, 60)
    _bill(client, a, cid, 25)            # settled from the credit

    _move(client, a, cid, res)

    assert _reseller_balance(app, res) == 0
    with app.app_context():
        assert float(db.session.get(Customer, cid).balance) == 35
        assert Payment.query.filter_by(customer_id=cid).count() == 2
