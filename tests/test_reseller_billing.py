from datetime import datetime
from dateutil.relativedelta import relativedelta

from app import db, Customer, Reseller, ResellerPayment, Payment, generate_missing_payments
from tests.conftest import make_tenant


def _make_plan(client, hdr, name="Basic", price=100):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": name, "price": price, "billing_cycle": "monthly"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["plan"]["id"]


def _make_reseller(client, hdr, name="Res1"):
    r = client.post("/api/resellers", headers=hdr, json={"name": name, "phone": "000", "type": "type1"})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["reseller"]["id"]


def test_reseller_customer_never_gets_a_payment_or_balance_owed(app, client):
    """Reseller-linked customers are billed by crediting the reseller's balance,
    never by creating a pending Payment or touching the customer's own balance --
    those are collected from the reseller directly, not via the payments view."""
    a = make_tenant(client, "Biz A", "a_resbill1")
    plan_id = _make_plan(client, a)
    reseller_id = _make_reseller(client, a)

    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        start = datetime.utcnow() - relativedelta(months=3)
        # subscription_expiry_date = start + 1 cycle mirrors what add_customer's
        # own back-dated-charge loop would have left behind: the start-date cycle
        # itself already billed, "paid through" one cycle past it.
        customer = Customer(tenant_id=reseller.tenant_id, name="Cust1", phone="111", address="addr",
                             subscription_plan_id=plan_id, subscription_start_date=start,
                             subscription_expiry_date=start + relativedelta(months=1),
                             is_subscription_active=True, balance=0.0, reseller_id=reseller.id)
        db.session.add(customer)
        db.session.commit()
        customer_id, tenant_id = customer.id, reseller.tenant_id

    with app.app_context():
        generate_missing_payments(tenant_id)

    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        customer = db.session.get(Customer, customer_id)
        assert reseller.balance == 300.0  # 3 more monthly cycles due since creation, charged to the reseller
        assert customer.balance == 0.0
        assert Payment.query.filter_by(customer_id=customer_id).count() == 0


def test_generate_missing_payments_is_idempotent_for_reseller_customer(app, client):
    """Re-running the daily scheduler (as happens on every app restart/reload)
    must not re-credit the reseller for cycles it already billed."""
    a = make_tenant(client, "Biz A", "a_resbill2")
    plan_id = _make_plan(client, a)
    reseller_id = _make_reseller(client, a)

    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        start = datetime.utcnow() - relativedelta(months=2)
        customer = Customer(tenant_id=reseller.tenant_id, name="Cust1", phone="111", address="addr",
                             subscription_plan_id=plan_id, subscription_start_date=start,
                             subscription_expiry_date=start + relativedelta(months=1),
                             is_subscription_active=True, balance=0.0, reseller_id=reseller.id)
        db.session.add(customer)
        db.session.commit()
        tenant_id = reseller.tenant_id

    for _ in range(3):
        with app.app_context():
            generate_missing_payments(tenant_id)

    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        assert reseller.balance == 200.0  # 2 cycles due, billed once each, not 3x


def test_renew_subscription_for_reseller_customer_charges_reseller_only(app, client):
    a = make_tenant(client, "Biz A", "a_resbill3")
    plan_id = _make_plan(client, a)
    reseller_id = _make_reseller(client, a)

    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        customer = Customer(tenant_id=reseller.tenant_id, name="Cust1", phone="111", address="addr",
                             subscription_plan_id=plan_id, subscription_start_date=datetime.utcnow(),
                             subscription_expiry_date=datetime.utcnow(), is_subscription_active=True,
                             balance=0.0, reseller_id=reseller.id)
        db.session.add(customer)
        db.session.commit()
        customer_id, tenant_id = customer.id, reseller.tenant_id

    r = client.post(f"/api/customers/{customer_id}/renew_subscription", headers=a)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["reseller_billed"] is True
    assert body["customer_new_balance"] == 0.0

    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        assert reseller.balance == 100.0
        assert Payment.query.filter_by(customer_id=customer_id).count() == 0

    # A scheduler run right after renewing must not double-bill the cycle just renewed.
    with app.app_context():
        generate_missing_payments(tenant_id)
        generate_missing_payments(tenant_id)

    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        assert reseller.balance == 100.0


def test_generate_missing_payments_does_not_rebill_legacy_null_customer_id_history(app, client):
    """Regression test for the bug where ResellerPayment rows created before
    customer_id was backfilled (see commit bb63f05) are invisible to a
    customer-scoped ledger query. That made the scheduler's cursor fall back to
    subscription_start_date and re-bill (re-credit the reseller for) the
    customer's ENTIRE history on every run. The cursor now comes from
    customer.subscription_expiry_date instead, which doesn't depend on the
    ledger at all, so legacy rows can't confuse it."""
    a = make_tenant(client, "Biz A", "a_resbill4")
    plan_id = _make_plan(client, a)
    reseller_id = _make_reseller(client, a)

    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        start = datetime.utcnow() - relativedelta(months=8)
        customer = Customer(tenant_id=reseller.tenant_id, name="LegacyCust", phone="222", address="addr2",
                             subscription_plan_id=plan_id, subscription_start_date=start,
                             subscription_expiry_date=datetime.utcnow() - relativedelta(days=2),
                             is_subscription_active=True, balance=0.0, reseller_id=reseller.id)
        db.session.add(customer)
        db.session.flush()
        customer_id, tenant_id = customer.id, reseller.tenant_id

        # 7 months already legitimately billed+collected under the old (pre-fix)
        # code, whose ResellerPayment rows never got a customer_id.
        d = start
        for _ in range(7):
            db.session.add(ResellerPayment(tenant_id=tenant_id, reseller_id=reseller.id, customer_id=None,
                                            amount=100.0, type='credit_added', date=d,
                                            description=f'Billing cycle charge for customer {customer.name}'))
            d = d + relativedelta(months=1)
        reseller.balance = 700.0
        db.session.commit()

    for _ in range(3):
        with app.app_context():
            generate_missing_payments(tenant_id)

    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        customer = db.session.get(Customer, customer_id)
        # Exactly the one genuinely-overdue cycle gets billed once -- not the
        # customer's entire 8-month history re-billed on top of what the legacy
        # rows already covered, and not repeated on subsequent scheduler runs.
        assert reseller.balance == 800.0
        assert customer.balance == 0.0
        assert Payment.query.filter_by(customer_id=customer_id).count() == 0
