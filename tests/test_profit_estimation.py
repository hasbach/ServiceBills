from datetime import datetime
import app as appmod
from tests.conftest import make_tenant


def test_plan_cost_customer_override_and_monthly_estimate_defaults(app):
    with app.app_context():
        tenant = appmod.Tenant(name="Biz", slug="biz")
        appmod.db.session.add(tenant)
        appmod.db.session.flush()

        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Fiber 50", price=25.0,
            billing_cycle="monthly", cost=15.0
        )
        appmod.db.session.add(plan)
        appmod.db.session.flush()
        assert plan.to_dict()["cost"] == 15.0

        customer = appmod.Customer(
            tenant_id=tenant.id, name="Cust", phone="1", address="a",
            subscription_plan_id=plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2026, 2, 1),
            cost_override=18.0
        )
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        assert customer.cost_override == 18.0

        estimate = appmod.MonthlyProfitEstimate(
            tenant_id=tenant.id, month="2026-01",
            estimated_income=25.0, estimated_cost=18.0, estimated_profit=7.0
        )
        appmod.db.session.add(estimate)
        appmod.db.session.commit()
        d = estimate.to_dict()
        assert d["month"] == "2026-01"
        assert d["estimated_income"] == 25.0
        assert d["estimated_cost"] == 18.0
        assert d["estimated_profit"] == 7.0


def test_recalculate_estimated_profit_amortizes_and_overrides(app):
    with app.app_context():
        tenant = appmod.Tenant(name="Biz", slug="biz")
        appmod.db.session.add(tenant)
        appmod.db.session.flush()

        monthly_plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Monthly50", price=25.0,
            billing_cycle="monthly", cost=15.0
        )
        yearly_plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Yearly200", price=240.0,
            billing_cycle="yearly", cost=120.0
        )
        appmod.db.session.add_all([monthly_plan, yearly_plan])
        appmod.db.session.flush()

        # Uses the plan's default cost (15.0).
        cust_default_cost = appmod.Customer(
            tenant_id=tenant.id, name="A", phone="1", address="a",
            subscription_plan_id=monthly_plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2026, 2, 1),
            is_subscription_active=True,
        )
        # Overrides cost to 18.0 despite being on the same plan as A.
        cust_override_cost = appmod.Customer(
            tenant_id=tenant.id, name="B", phone="2", address="b",
            subscription_plan_id=monthly_plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2026, 2, 1),
            is_subscription_active=True,
            cost_override=18.0,
        )
        # Yearly plan: price/cost amortized over 12 months.
        cust_yearly = appmod.Customer(
            tenant_id=tenant.id, name="C", phone="3", address="c",
            subscription_plan_id=yearly_plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2027, 1, 1),
            is_subscription_active=True,
        )
        # Inactive: must not contribute.
        cust_inactive = appmod.Customer(
            tenant_id=tenant.id, name="D", phone="4", address="d",
            subscription_plan_id=monthly_plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2026, 2, 1),
            is_subscription_active=False,
        )
        appmod.db.session.add_all([cust_default_cost, cust_override_cost, cust_yearly, cust_inactive])
        appmod.db.session.commit()

        appmod.recalculate_estimated_profit(tenant.id)

        month = appmod.datetime.utcnow().strftime('%Y-%m')
        estimate = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant.id, month=month).first()
        assert estimate is not None

        # income: 25 (A) + 25 (B) + 240/12=20 (C) = 70
        # cost:   15 (A) + 18 (B) + 120/12=10 (C) = 43
        assert estimate.estimated_income == 70.0
        assert estimate.estimated_cost == 43.0
        assert estimate.estimated_profit == 27.0

        # Calling again upserts the SAME row -- no duplicate for this month.
        appmod.recalculate_estimated_profit(tenant.id)
        assert appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant.id, month=month).count() == 1
