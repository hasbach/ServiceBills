"""Finance roadmap 1A, fix 10: a refund is dated the day the money went back,
so it must not become the customer's billing anchor (that moved the cycle to
the refund day and silently skipped the bill in between)."""
from datetime import datetime

from dateutil.relativedelta import relativedelta

from app import db, Customer, Payment, Tenant, generate_missing_payments
from tests.conftest import make_tenant
from tests.test_reseller_billing import _make_plan


def test_refund_row_does_not_shift_the_next_bill(app, client):
    a = make_tenant(client, "Biz A", "anchor1")
    plan_id = _make_plan(client, a, price=100)
    now = datetime.utcnow()
    last_bill_date = now - relativedelta(months=1, days=1)  # next cycle fell due yesterday

    with app.app_context():
        tenant_id = Tenant.query.order_by(Tenant.id.desc()).first().id
        customer = Customer(tenant_id=tenant_id, name="Cust", phone="111", address="addr",
                            subscription_plan_id=plan_id,
                            subscription_start_date=last_bill_date,
                            subscription_expiry_date=last_bill_date + relativedelta(months=1),
                            is_subscription_active=True, balance=0.0)
        db.session.add(customer)
        db.session.flush()
        db.session.add(Payment(tenant_id=tenant_id, customer_id=customer.id, amount=100, paid=True,
                               paid_at=last_bill_date, date=last_bill_date, pre_payment=False))
        # Part of it refunded today.
        db.session.add(Payment(tenant_id=tenant_id, customer_id=customer.id, amount=20, paid=True,
                               paid_at=now, date=now, pre_payment=False, is_refund=True,
                               reason="Refund for payment #1"))
        db.session.commit()
        customer_id = customer.id

        generate_missing_payments(tenant_id)

        bills = Payment.query.filter_by(customer_id=customer_id, paid=False).all()
        assert len(bills) == 1, "the cycle that fell due yesterday must still be billed"
        assert bills[0].date.date() == (last_bill_date + relativedelta(months=1)).date()
