"""Schema additions shared by the tenant-wide self-service Whish payment
page -- see the 2026-08-27 plan's amendment section. Payment.collected_via
and Payment.whish_transaction_number are also set by the existing per-link
flow (Task 9's amendment note)."""
import secrets
from datetime import timedelta

import app as appmod
from tests.conftest import make_tenant


def test_payment_has_collected_via_and_transaction_number_columns(app):
    with app.app_context():
        insp = appmod.db.inspect(appmod.db.engine)
        cols = {c['name'] for c in insp.get_columns('payment')}
        assert 'collected_via' in cols
        assert 'whish_transaction_number' in cols


def test_tenant_has_public_pay_slug_column(app):
    with app.app_context():
        insp = appmod.db.inspect(appmod.db.engine)
        cols = {c['name'] for c in insp.get_columns('tenant')}
        assert 'public_pay_slug' in cols


def test_customer_whish_payment_attempt_model_exists(app):
    with app.app_context():
        assert hasattr(appmod, 'CustomerWhishPaymentAttempt')
        insp = appmod.db.inspect(appmod.db.engine)
        assert 'customer_whish_payment_attempt' in insp.get_table_names()


def test_customer_whish_payment_attempt_is_tenant_owned():
    assert appmod.CustomerWhishPaymentAttempt in appmod.TENANT_OWNED_MODELS


def _make_branded_tenant(app, client, business_name, logo_url=None):
    hdr = make_tenant(client, business_name, business_name.lower().replace(' ', '_') + "_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=business_name).first()
        tenant.public_pay_slug = secrets.token_urlsafe(12)
        bs = appmod.BusinessSettings.query.filter_by(tenant_id=tenant.id).first()
        if not bs:
            bs = appmod.BusinessSettings(tenant_id=tenant.id, business_name=business_name, address="Beirut", mobile="+96170000000")
            appmod.db.session.add(bs)
        bs.logo_url = logo_url
        appmod.db.session.commit()
        return tenant.public_pay_slug, hdr


def _add_customer(app, tenant_name, name, phone):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        plan = appmod.SubscriptionPlan.query.filter_by(tenant_id=tenant.id).first()
        if not plan:
            plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="P", price=30.0,
                                            billing_cycle="monthly", currency="USD")
            appmod.db.session.add(plan)
            appmod.db.session.commit()
        customer = appmod.Customer(tenant_id=tenant.id, name=name, phone=phone,
                                    subscription_plan_id=plan.id, address="Beirut",
                                    subscription_expiry_date=appmod.datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        return customer.id


def test_public_branding_route_returns_name_and_logo(client, app):
    slug, _ = _make_branded_tenant(app, client, "Biz Brand1", logo_url="https://x/logo.png")
    r = client.get(f"/api/pay/t/{slug}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["business_name"] == "Biz Brand1"
    assert body["logo_url"] == "https://x/logo.png"
    assert "brand_color" not in body  # explicit decision -- logo only, see this task's docstring


def test_public_branding_route_unknown_slug_generic_404(client):
    r = client.get("/api/pay/t/does-not-exist")
    assert r.status_code == 404


def test_phone_lookup_single_match_returns_one_customer(client, app):
    slug, _ = _make_branded_tenant(app, client, "Biz Lookup1")
    _add_customer(app, "Biz Lookup1", "Nadia Solo", "70123456")
    r = client.post(f"/api/pay/t/{slug}/lookup", json={"phone": "70123456"})
    assert r.status_code == 200
    customers = r.get_json()["customers"]
    assert len(customers) == 1
    assert customers[0]["name"] == "Nadia Solo"


def test_phone_lookup_no_match_is_generic(client, app):
    slug, _ = _make_branded_tenant(app, client, "Biz Lookup2")
    r = client.post(f"/api/pay/t/{slug}/lookup", json={"phone": "00000000"})
    assert r.status_code == 404


def test_phone_lookup_multiple_matches_returns_all_for_the_customer_to_pick(client, app):
    # Customer.phone has no uniqueness constraint (confirmed in this plan's
    # amendment investigation) -- e.g. a household sharing one phone across
    # two family members' subscriptions. Never silently pick one -- that
    # risks confirming the WRONG subscription name to whoever is paying.
    # Instead, return every match so the customer can pick the right one --
    # this serves the request's own goal better than erroring out would.
    slug, _ = _make_branded_tenant(app, client, "Biz Lookup3")
    _add_customer(app, "Biz Lookup3", "Customer A", "70999999")
    _add_customer(app, "Biz Lookup3", "Customer B", "70999999")
    r = client.post(f"/api/pay/t/{slug}/lookup", json={"phone": "70999999"})
    assert r.status_code == 200
    customers = r.get_json()["customers"]
    assert len(customers) == 2
    assert {c["name"] for c in customers} == {"Customer A", "Customer B"}


def test_phone_lookup_is_rate_limited(client, app):
    slug, _ = _make_branded_tenant(app, client, "Biz Lookup4")
    try:
        r = None
        for _ in range(15):
            r = client.post(f"/api/pay/t/{slug}/lookup", json={"phone": "00000000"})
        assert r.status_code == 429
    finally:
        # flask-limiter's in-memory storage is shared across the whole test
        # session (the Limiter object is created once at app-module-import
        # time, unlike the per-test in-memory DB) -- reset it so tripping
        # this route's per-IP limit doesn't leak into later tests hitting
        # the same endpoint.
        appmod.limiter.storage.reset()


def test_phone_lookup_tenant_isolated(client, app):
    slug_a, _ = _make_branded_tenant(app, client, "Biz LookupIsoA")
    slug_b, _ = _make_branded_tenant(app, client, "Biz LookupIsoB")
    _add_customer(app, "Biz LookupIsoB", "Only In B", "70555555")
    r = client.post(f"/api/pay/t/{slug_a}/lookup", json={"phone": "70555555"})
    assert r.status_code == 404
