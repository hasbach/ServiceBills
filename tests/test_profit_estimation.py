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
