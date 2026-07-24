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


def test_customer_lifecycle_triggers_recompute(app, client):
    a = make_tenant(client, "Biz A", "a_admin")

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id

    r = client.post("/api/subscription_plans", headers=a,
                     json={"name": "Fiber 50", "price": 25, "billing_cycle": "monthly", "cost": 15})
    plan_id = r.get_json()["plan"]["id"]

    r2 = client.post("/api/customers", headers=a,
                      json={"name": "Cust", "phone": "1", "address": "a",
                            "subscription_plan_id": plan_id, "subscription_start_date": "2026-01-01"})
    assert r2.status_code == 201
    customer_id = r2.get_json()["customer_id"]

    def _current_estimate():
        with app.app_context():
            month = appmod.datetime.utcnow().strftime('%Y-%m')
            return appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first()

    # Adding an active customer triggers a recompute.
    est = _current_estimate()
    assert est is not None
    assert est.estimated_income == 25.0
    assert est.estimated_cost == 15.0

    # Setting a per-customer cost override triggers a recompute.
    r3 = client.put(f"/api/customers/{customer_id}", headers=a, json={"cost_override": 18})
    assert r3.status_code == 200
    assert r3.get_json()["customer"]["cost_override"] == 18.0
    assert _current_estimate().estimated_cost == 18.0

    # Clearing the override falls back to the plan's cost.
    r4 = client.put(f"/api/customers/{customer_id}", headers=a, json={"cost_override": ""})
    assert r4.status_code == 200
    assert r4.get_json()["customer"]["cost_override"] is None
    assert _current_estimate().estimated_cost == 15.0

    # cost_override also appears in the customer list.
    listed = client.get("/api/customers", headers=a).get_json()
    assert listed["customers"][0]["cost_override"] is None

    # Canceling the subscription removes the customer's contribution.
    r5 = client.put(f"/api/customers/{customer_id}/cancel_subscription", headers=a)
    assert r5.status_code == 200
    assert _current_estimate().estimated_income == 0.0

    # Reactivating restores it.
    r6 = client.put(f"/api/customers/{customer_id}/activate_subscription", headers=a)
    assert r6.status_code == 200
    assert _current_estimate().estimated_income == 25.0

    # Deleting the customer also removes their contribution.
    r7 = client.delete(f"/api/customers/{customer_id}", headers=a)
    assert r7.status_code == 200
    assert _current_estimate().estimated_income == 0.0


def test_subscription_plan_cost_field_and_recompute_on_edit(app, client):
    a = make_tenant(client, "Biz A", "a_admin")

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id

    r = client.post("/api/subscription_plans", headers=a,
                     json={"name": "Fiber 50", "price": 25, "billing_cycle": "monthly", "cost": 15})
    assert r.status_code in (200, 201)
    plan = r.get_json()["plan"]
    assert plan["cost"] == 15.0

    r2 = client.post("/api/customers", headers=a,
                      json={"name": "Cust", "phone": "1", "address": "a",
                            "subscription_plan_id": plan["id"], "subscription_start_date": "2026-01-01"})
    assert r2.status_code == 201

    with app.app_context():
        month = appmod.datetime.utcnow().strftime('%Y-%m')
        estimate = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first()
        assert estimate is not None
        assert estimate.estimated_cost == 15.0

    r3 = client.put(f"/api/subscription_plans/{plan['id']}", headers=a, json={"cost": 20})
    assert r3.status_code == 200
    assert r3.get_json()["plan"]["cost"] == 20.0

    with app.app_context():
        month = appmod.datetime.utcnow().strftime('%Y-%m')
        estimate = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first()
        assert estimate.estimated_cost == 20.0


def test_daily_recompute_covers_quiet_tenants_without_events(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    r = client.post("/api/subscription_plans", headers=a,
                     json={"name": "Fiber 50", "price": 25, "billing_cycle": "monthly", "cost": 15})
    plan_id = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=a,
                json={"name": "Cust", "phone": "1", "address": "a",
                      "subscription_plan_id": plan_id, "subscription_start_date": "2026-01-01"})

    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        b_tid = appmod.Tenant.query.filter_by(slug="biz-b").first().id
        month = appmod.datetime.utcnow().strftime('%Y-%m')

        # Wipe out the estimate the add_customer trigger already created, to
        # prove the daily job -- not the earlier event -- recreates it below.
        appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=a_tid, month=month).delete()
        appmod.db.session.commit()
        assert appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=a_tid, month=month).first() is None

    # Runs with NO request context, exactly like a real scheduler tick.
    appmod.recalculate_all_estimated_profits_with_context()

    with app.app_context():
        estimate_a = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=a_tid, month=month).first()
        assert estimate_a is not None
        assert estimate_a.estimated_income == 25.0

        # Tenant B has no customers/plans at all -- the job must not error on
        # it, and every row must stay under its own tenant.
        estimate_b = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=b_tid, month=month).first()
        assert estimate_b is not None
        assert estimate_b.estimated_income == 0.0
        assert appmod.MonthlyProfitEstimate.query.filter(appmod.MonthlyProfitEstimate.tenant_id.is_(None)).count() == 0


def test_financial_report_includes_estimated_profit_and_variance(app, client):
    from datetime import datetime, timedelta

    a = make_tenant(client, "Biz A", "a_admin")

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id

    r = client.post("/api/subscription_plans", headers=a,
                     json={"name": "Fiber 50", "price": 25, "billing_cycle": "monthly", "cost": 15})
    plan_id = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=a,
                json={"name": "Cust", "phone": "1", "address": "a",
                      "subscription_plan_id": plan_id, "subscription_start_date": "2026-01-01"})

    # Delete the estimate row that add_customer's trigger already created, so
    # the assertions below can only pass via the endpoint's OWN lazy
    # current-month backfill, not a row left over from an earlier trigger.
    with app.app_context():
        month = appmod.datetime.utcnow().strftime('%Y-%m')
        appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).delete()
        appmod.db.session.commit()
        assert appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first() is None

    start = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%dT00:00:00Z')
    end = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%dT00:00:00Z')
    fin = client.get(f"/api/reports/financial?start_date={start}&end_date={end}", headers=a).get_json()

    assert len(fin["monthly_data"]) == 1
    row = fin["monthly_data"][0]
    # No real cash income/expenses recorded this month, but the estimate
    # (25 income - 15 cost = 10 profit) is present via the lazy current-month backfill.
    assert row["income"] == 0.0
    assert row["profit"] == 0.0
    assert row["estimated_profit"] == 10.0
    assert row["variance"] == -10.0  # real (0) - estimated (10)
    assert fin["totals"]["estimated_profit"] == 10.0
    assert fin["totals"]["variance"] == -10.0


def test_financial_report_past_month_with_no_estimate_is_null(client):
    from datetime import datetime, timedelta

    a = make_tenant(client, "Biz A", "a_admin")

    # A far-past range that predates this feature entirely: no MonthlyProfitEstimate
    # row will ever exist for it, and it is NOT the current month, so there is no
    # lazy backfill either.
    start = "2020-01-01T00:00:00Z"
    end = "2020-01-31T23:59:59Z"

    # Force a real cash data point in that month so it actually appears as a row.
    client.post("/api/expense_categories", headers=a, json={"name": "Rent"})
    client.post("/api/expenses", headers=a,
                json={"category": "Rent", "amount": 50, "description": "Office rent",
                      "date": "2020-01-15", "is_credit": False})

    fin = client.get(f"/api/reports/financial?start_date={start}&end_date={end}", headers=a).get_json()
    assert len(fin["monthly_data"]) == 1
    row = fin["monthly_data"][0]
    assert row["estimated_profit"] is None
    assert row["variance"] is None
