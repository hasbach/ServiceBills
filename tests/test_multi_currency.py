"""Multi-currency accounting for tenant customer billing -- see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md.
Platform-subscription (Whish) billing stays USD-only and is untouched here."""
from datetime import datetime, timedelta
from decimal import Decimal
import pytest
import app as appmod
from tests.conftest import make_tenant


def _make_plan(app, tenant_name, name="Basic Plan", price=30.0, cost=10.0,
                billing_cycle="monthly", currency="USD"):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name=name, price=price, cost=cost,
            billing_cycle=billing_cycle, currency=currency)
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        return plan.id


# --- Task 1: Currency / ExchangeRate models + fx.get_rate ------------------

def test_currency_seed_rows_exist(app, client):
    with app.app_context():
        usd = appmod.db.session.get(appmod.Currency, 'USD')
        lbp = appmod.db.session.get(appmod.Currency, 'LBP')
        assert usd is not None and usd.decimal_places == 2
        assert lbp is not None and lbp.decimal_places == 0


def test_exchange_rate_model_roundtrip(app, client):
    make_tenant(client, "Biz FX", "fx_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz FX").first()
        rate = appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('89542.37'),
        )
        appmod.db.session.add(rate)
        appmod.db.session.commit()
        fetched = appmod.ExchangeRate.query.filter_by(tenant_id=tenant.id).first()
        assert fetched.rate == Decimal('89542.37')
        assert fetched.source == 'manual'


def test_fx_get_rate_same_currency_is_always_one(app, client):
    make_tenant(client, "Biz Same", "same_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Same").first()
        assert appmod.fx.get_rate(tenant.id, 'USD', 'USD') == Decimal('1')


def test_fx_get_rate_direct_pair(app, client):
    make_tenant(client, "Biz Direct", "direct_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Direct").first()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('90000')))
        appmod.db.session.commit()
        assert appmod.fx.get_rate(tenant.id, 'USD', 'LBP') == Decimal('90000')


def test_fx_get_rate_inverse_pair_fallback(app, client):
    make_tenant(client, "Biz Inverse", "inverse_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Inverse").first()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('100000')))
        appmod.db.session.commit()
        assert appmod.fx.get_rate(tenant.id, 'LBP', 'USD') == Decimal('1') / Decimal('100000')


def test_fx_get_rate_as_of_uses_the_rate_effective_at_that_time(app, client):
    make_tenant(client, "Biz AsOf", "asof_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz AsOf").first()
        old = datetime.utcnow() - timedelta(days=10)
        new = datetime.utcnow()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('85000'), effective_at=old))
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('90000'), effective_at=new))
        appmod.db.session.commit()
        as_of_before_new_rate = new - timedelta(days=1)
        assert appmod.fx.get_rate(tenant.id, 'USD', 'LBP', as_of=as_of_before_new_rate) == Decimal('85000')
        assert appmod.fx.get_rate(tenant.id, 'USD', 'LBP', as_of=new) == Decimal('90000')


def test_fx_get_rate_missing_raises(app, client):
    make_tenant(client, "Biz Missing", "missing_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Missing").first()
        with pytest.raises(appmod.fx.FxRateMissingError):
            appmod.fx.get_rate(tenant.id, 'USD', 'LBP')


def test_exchange_rate_is_tenant_isolated(app, client):
    make_tenant(client, "Biz IsoA", "isoa_admin")
    make_tenant(client, "Biz IsoB", "isob_admin")
    with app.app_context():
        tenant_a = appmod.Tenant.query.filter_by(name="Biz IsoA").first()
        tenant_b = appmod.Tenant.query.filter_by(name="Biz IsoB").first()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant_a.id, from_currency='USD', to_currency='LBP', rate=Decimal('90000')))
        appmod.db.session.commit()
        with pytest.raises(appmod.fx.FxRateMissingError):
            appmod.fx.get_rate(tenant_b.id, 'USD', 'LBP')


# --- Task 2: BusinessSettings opt-in + reporting currency -------------------

def test_business_settings_default_single_currency(app, client):
    hdr = make_tenant(client, "Biz Default", "default_admin")
    r = client.get("/api/business-settings", headers=hdr)
    body = r.get_json()['settings']
    assert body['multi_currency_enabled'] is False
    assert body['reporting_currency'] == 'USD'


def test_business_settings_can_opt_into_multi_currency(app, client):
    hdr = make_tenant(client, "Biz OptIn", "optin_admin")
    r = client.post("/api/business-settings", headers=hdr, data={
        "business_name": "Biz OptIn", "address": "addr", "mobile": "123",
        "multi_currency_enabled": "true", "reporting_currency": "LBP",
    })
    assert r.status_code == 200
    body = r.get_json()['settings']
    assert body['multi_currency_enabled'] is True
    assert body['reporting_currency'] == 'LBP'


def test_business_settings_rejects_unknown_reporting_currency(app, client):
    hdr = make_tenant(client, "Biz BadRC", "badrc_admin")
    r = client.post("/api/business-settings", headers=hdr, data={
        "business_name": "Biz BadRC", "address": "addr", "mobile": "123",
        "reporting_currency": "ZZZ",
    })
    assert r.status_code == 400


# --- Task 3: SubscriptionPlan.currency --------------------------------------

def test_subscription_plan_defaults_to_usd(app, client):
    hdr = make_tenant(client, "Biz PlanCur", "plancur_admin")
    r = client.post("/api/subscription_plans", headers=hdr, json={
        "name": "Fiber 50", "price": 30.0, "cost": 10.0, "billing_cycle": "monthly"})
    assert r.status_code == 201
    assert r.get_json()['plan']['currency'] == 'USD'


def test_subscription_plan_currency_can_be_set_explicitly(app, client):
    hdr = make_tenant(client, "Biz PlanLbp", "planlbp_admin")
    r = client.post("/api/subscription_plans", headers=hdr, json={
        "name": "Fiber 50 LBP", "price": 2700000.0, "cost": 900000.0,
        "billing_cycle": "monthly", "currency": "LBP"})
    assert r.status_code == 201
    assert r.get_json()['plan']['currency'] == 'LBP'


def test_subscription_plan_rejects_unknown_currency(app, client):
    hdr = make_tenant(client, "Biz PlanBad", "planbad_admin")
    r = client.post("/api/subscription_plans", headers=hdr, json={
        "name": "Fiber Bad", "price": 30.0, "cost": 10.0,
        "billing_cycle": "monthly", "currency": "ZZZ"})
    assert r.status_code == 400


def test_update_subscription_plan_currency(app, client):
    hdr = make_tenant(client, "Biz PlanUpd", "planupd_admin")
    plan_id = _make_plan(client.application, "Biz PlanUpd")
    r = client.put(f"/api/subscription_plans/{plan_id}", headers=hdr, json={"currency": "LBP"})
    assert r.status_code == 200
    assert r.get_json()['plan']['currency'] == 'LBP'


# --- Task 4: Payment.currency / fx_rate_to_reporting (add_payment only) ----

def test_add_payment_opted_out_tenant_locks_rate_one(app, client):
    hdr = make_tenant(client, "Biz NoMC", "nomc_admin")
    plan_id = _make_plan(app, "Biz NoMC")
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust A", "phone": "123", "address": "addr", "subscription_plan_id": plan_id})
    assert r.status_code == 201, r.get_json()
    customer_id = r.get_json()['customer_id']
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 10.0, "reason": "test", "pre_payment": True})
    assert r.status_code == 201, r.get_json()
    with app.app_context():
        payment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).first()
        assert payment.currency == 'USD'
        assert payment.fx_rate_to_reporting == 1


def test_add_payment_opted_in_tenant_without_rate_returns_400(app, client):
    hdr = make_tenant(client, "Biz NoRate", "norate_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "Biz NoRate", "address": "a", "mobile": "1",
        "multi_currency_enabled": "true", "reporting_currency": "USD"})
    plan_id = _make_plan(app, "Biz NoRate", name="LBP Plan", price=1000000.0, cost=0.0, currency="LBP")
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust B", "phone": "123", "address": "addr", "subscription_plan_id": plan_id})
    customer_id = r.get_json()['customer_id']
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 500000.0, "reason": "test", "pre_payment": True})
    assert r.status_code == 400
    with app.app_context():
        assert appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).count() == 0


def test_add_payment_locks_rate_and_later_rate_changes_dont_affect_it(app, client):
    hdr = make_tenant(client, "Biz LockedRate", "lockedrate_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "Biz LockedRate", "address": "a", "mobile": "1",
        "multi_currency_enabled": "true", "reporting_currency": "USD"})
    plan_id = _make_plan(app, "Biz LockedRate", name="LBP Plan 2", price=1000000.0, cost=0.0, currency="LBP")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz LockedRate").first()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency="LBP", to_currency="USD", rate=Decimal('0.0000111')))
        appmod.db.session.commit()
        tenant_id = tenant.id
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust C", "phone": "123", "address": "addr", "subscription_plan_id": plan_id})
    customer_id = r.get_json()['customer_id']
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 500000.0, "reason": "test", "pre_payment": True})
    assert r.status_code == 201, r.get_json()
    with app.app_context():
        payment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).first()
        first_rate = payment.fx_rate_to_reporting
        assert float(first_rate) == pytest.approx(0.0000111)

        # A new rate is entered afterward -- must not retroactively change the locked payment.
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant_id, from_currency="LBP", to_currency="USD", rate=Decimal('0.0000200')))
        appmod.db.session.commit()
        payment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).first()
        assert payment.fx_rate_to_reporting == first_rate


# --- Task 5: Exchange-rate CRUD API -----------------------------------------

def test_post_exchange_rate_rejected_when_multi_currency_disabled(app, client):
    hdr = make_tenant(client, "Biz FxOff", "fxoff_admin")
    r = client.post("/api/exchange-rates", headers=hdr, json={
        "from_currency": "USD", "to_currency": "LBP", "rate": 90000})
    assert r.status_code == 400


def test_post_exchange_rate_rejects_unknown_currency(app, client):
    hdr = make_tenant(client, "Biz FxBadCode", "fxbadcode_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "x", "address": "a", "mobile": "1", "multi_currency_enabled": "true"})
    r = client.post("/api/exchange-rates", headers=hdr, json={
        "from_currency": "USD", "to_currency": "ZZZ", "rate": 90000})
    assert r.status_code == 400


def test_post_exchange_rate_rejects_non_positive_rate(app, client):
    hdr = make_tenant(client, "Biz FxNeg", "fxneg_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "x", "address": "a", "mobile": "1", "multi_currency_enabled": "true"})
    r = client.post("/api/exchange-rates", headers=hdr, json={
        "from_currency": "USD", "to_currency": "LBP", "rate": 0})
    assert r.status_code == 400


def test_post_and_get_exchange_rate_happy_path(app, client):
    hdr = make_tenant(client, "Biz FxOk", "fxok_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "x", "address": "a", "mobile": "1", "multi_currency_enabled": "true"})
    r = client.post("/api/exchange-rates", headers=hdr, json={
        "from_currency": "USD", "to_currency": "LBP", "rate": 89542.37})
    assert r.status_code == 201, r.get_json()
    r = client.get("/api/exchange-rates", headers=hdr)
    rates = r.get_json()['exchange_rates']
    assert len(rates) == 1 and rates[0]['from_currency'] == 'USD' and rates[0]['rate'] == pytest.approx(89542.37)


def test_exchange_rates_are_tenant_isolated_via_api(app, client):
    hdr_a = make_tenant(client, "Biz FxIsoA", "fxisoa_admin")
    hdr_b = make_tenant(client, "Biz FxIsoB", "fxisob_admin")
    for hdr, name in ((hdr_a, "Biz FxIsoA"), (hdr_b, "Biz FxIsoB")):
        client.post("/api/business-settings", headers=hdr, data={
            "business_name": name, "address": "a", "mobile": "1", "multi_currency_enabled": "true"})
    client.post("/api/exchange-rates", headers=hdr_a, json={
        "from_currency": "USD", "to_currency": "LBP", "rate": 90000})
    r = client.get("/api/exchange-rates", headers=hdr_b)
    assert r.get_json()['exchange_rates'] == []


# --- Task 6: Float -> Numeric conversion ------------------------------------

def test_money_columns_are_numeric_not_float(app, client):
    import sqlalchemy as sa
    numeric_targets = [
        (appmod.Reseller, 'balance'), (appmod.ResellerPayment, 'amount'),
        (appmod.UpstreamProvider, 'balance'), (appmod.UpstreamProviderPayment, 'amount'),
        (appmod.Customer, 'balance'), (appmod.Customer, 'discount'), (appmod.Customer, 'cost_override'),
        (appmod.SubscriptionPlan, 'price'), (appmod.SubscriptionPlan, 'cost'),
        (appmod.Supplier, 'balance'), (appmod.SupplierPayment, 'amount'),
        (appmod.Expense, 'amount'),
        (appmod.Employee, 'monthly_salary'), (appmod.Employee, 'balance'),
        (appmod.SalaryCharge, 'amount'), (appmod.SalaryPayment, 'amount'),
        (appmod.MonthlyProfitEstimate, 'estimated_income'), (appmod.MonthlyProfitEstimate, 'estimated_cost'),
        (appmod.MonthlyProfitEstimate, 'estimated_profit'),
        (appmod.Payment, 'amount'), (appmod.Payment, 'collected_amount'),
        (appmod.AddonPurchase, 'amount'), (appmod.BillingPaymentAttempt, 'amount'),
    ]
    for model, colname in numeric_targets:
        coltype = getattr(model, colname).type
        # sa.Float IS-A sa.Numeric in SQLAlchemy's type hierarchy, so a bare
        # isinstance(..., sa.Numeric) check would vacuously pass on an
        # unconverted Float column -- explicitly exclude Float.
        assert isinstance(coltype, sa.Numeric) and not isinstance(coltype, sa.Float), \
            f"{model.__name__}.{colname} is {type(coltype)}, expected Numeric (not Float)"


def test_money_values_still_round_trip_as_float_via_to_dict(app, client):
    make_tenant(client, "Biz NumericRT", "numericrt_admin")
    plan_id = _make_plan(app, "Biz NumericRT")
    with app.app_context():
        plan = appmod.db.session.get(appmod.SubscriptionPlan, plan_id)
        assert isinstance(plan.to_dict()['price'], float)
        assert plan.to_dict()['price'] == float(plan.price)


# --- Task 7: cross-currency plan-change guard -------------------------------

def test_plan_change_blocked_when_currency_differs_and_balance_nonzero(app, client):
    hdr = make_tenant(client, "Biz GuardBlock", "guardblock_admin")
    usd_plan_id = _make_plan(app, "Biz GuardBlock", name="USD Plan", currency="USD")
    lbp_plan_id = _make_plan(app, "Biz GuardBlock", name="LBP Plan", price=2700000.0, cost=900000.0, currency="LBP")
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust Guard", "phone": "1", "address": "a", "subscription_plan_id": usd_plan_id})
    customer_id = r.get_json()['customer_id']
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        customer.balance = 15.0
        appmod.db.session.commit()
    r = client.put(f"/api/customers/{customer_id}", headers=hdr, json={"subscription_plan_id": lbp_plan_id})
    assert r.status_code == 400
    with app.app_context():
        assert appmod.db.session.get(appmod.Customer, customer_id).subscription_plan_id == usd_plan_id


def test_plan_change_allowed_across_currencies_when_balance_zero(app, client):
    hdr = make_tenant(client, "Biz GuardOk", "guardok_admin")
    usd_plan_id = _make_plan(app, "Biz GuardOk", name="USD Plan 2", currency="USD")
    lbp_plan_id = _make_plan(app, "Biz GuardOk", name="LBP Plan 2", price=2700000.0, cost=900000.0, currency="LBP")
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust GuardOk", "phone": "1", "address": "a", "subscription_plan_id": usd_plan_id})
    customer_id = r.get_json()['customer_id']
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        customer.balance = 0.0
        appmod.db.session.commit()
    r = client.put(f"/api/customers/{customer_id}", headers=hdr, json={"subscription_plan_id": lbp_plan_id})
    assert r.status_code == 200, r.get_json()


def test_plan_change_allowed_within_same_currency_even_with_balance(app, client):
    hdr = make_tenant(client, "Biz GuardSame", "guardsame_admin")
    plan_a_id = _make_plan(app, "Biz GuardSame", name="USD Plan A", currency="USD")
    plan_b_id = _make_plan(app, "Biz GuardSame", name="USD Plan B", price=45.0, currency="USD")
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust GuardSame", "phone": "1", "address": "a", "subscription_plan_id": plan_a_id})
    customer_id = r.get_json()['customer_id']
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        customer.balance = 15.0
        appmod.db.session.commit()
    r = client.put(f"/api/customers/{customer_id}", headers=hdr, json={"subscription_plan_id": plan_b_id})
    assert r.status_code == 200, r.get_json()


# --- Task 8: reporting-currency conversion ----------------------------------

def test_financial_report_opted_out_tenant_reports_usd(app, client):
    hdr = make_tenant(client, "Biz ReportOff", "reportoff_admin")
    plan_id = _make_plan(app, "Biz ReportOff")
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust Report", "phone": "1", "address": "a", "subscription_plan_id": plan_id})
    customer_id = r.get_json()['customer_id']
    client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 42.0, "reason": "t", "pre_payment": True})
    today = datetime.utcnow().strftime('%Y-%m-%d')
    r = client.get(f"/api/reports/financial?start_date={today}&end_date={today}", headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['currency'] == 'USD'
    # Every payment locks fx_rate_to_reporting=1 for an opted-out tenant, so the
    # reporting-currency-converted total must exactly equal the raw sum of paid
    # amounts (a no-op multiply-by-1) -- computed independently here rather than
    # hard-coding an expected figure, since add_customer's own backdated-payment
    # backfill can add unrelated paid amounts depending on plan/date interaction.
    with app.app_context():
        expected = sum(
            float(p.amount) for p in appmod.Payment.query.filter_by(
                customer_id=customer_id, paid=True, is_gratis=False, is_refund=False)
        )
    assert body['totals']['income'] == pytest.approx(expected)
    assert expected >= 42.0


def test_financial_report_opted_in_tenant_converts_using_locked_rate(app, client):
    hdr = make_tenant(client, "Biz ReportOn", "reporton_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "x", "address": "a", "mobile": "1",
        "multi_currency_enabled": "true", "reporting_currency": "USD"})
    plan_id = _make_plan(app, "Biz ReportOn", name="LBP Report Plan", price=1000000.0, cost=0.0, currency="LBP")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz ReportOn").first()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency="LBP", to_currency="USD", rate=Decimal('0.00001')))
        appmod.db.session.commit()
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust ReportLbp", "phone": "1", "address": "a", "subscription_plan_id": plan_id})
    customer_id = r.get_json()['customer_id']
    client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 1000000.0, "reason": "t", "pre_payment": True})
    today = datetime.utcnow().strftime('%Y-%m-%d')
    r = client.get(f"/api/reports/financial?start_date={today}&end_date={today}", headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['currency'] == 'USD'
    # 1,000,000 LBP * 0.00001 = 10.00 USD
    assert body['totals']['income'] == pytest.approx(10.0)


def test_total_sales_report_opted_out_tenant_unchanged(app, client):
    hdr = make_tenant(client, "Biz TotalSales", "totalsales_admin")
    plan_id = _make_plan(app, "Biz TotalSales")
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust TS", "phone": "1", "address": "a", "subscription_plan_id": plan_id})
    customer_id = r.get_json()['customer_id']
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        payment = appmod.Payment(
            tenant_id=customer.tenant_id, customer_id=customer.id, amount=25.0, paid=True,
            paid_at=datetime.utcnow(), pre_payment=False)
        appmod.db.session.add(payment)
        appmod.db.session.commit()
    r = client.get("/api/reports/total-sales", headers=hdr)
    assert r.status_code == 200
    rows = r.get_json()
    assert sum(row['value'] for row in rows) == pytest.approx(25.0)


def test_total_sales_report_opted_in_tenant_converts_using_locked_rate(app, client):
    hdr = make_tenant(client, "Biz TotalSalesFx", "totalsalesfx_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "x", "address": "a", "mobile": "1",
        "multi_currency_enabled": "true", "reporting_currency": "USD"})
    plan_id = _make_plan(app, "Biz TotalSalesFx", name="LBP TS Plan", price=1000000.0, cost=0.0, currency="LBP")
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust TSFX", "phone": "1", "address": "a", "subscription_plan_id": plan_id})
    customer_id = r.get_json()['customer_id']
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        payment = appmod.Payment(
            tenant_id=customer.tenant_id, customer_id=customer.id, amount=1000000.0, paid=True,
            paid_at=datetime.utcnow(), pre_payment=False, currency='LBP',
            fx_rate_to_reporting=Decimal('0.00001'))
        appmod.db.session.add(payment)
        appmod.db.session.commit()
    r = client.get("/api/reports/total-sales", headers=hdr)
    assert r.status_code == 200
    rows = r.get_json()
    assert sum(row['value'] for row in rows) == pytest.approx(10.0)
