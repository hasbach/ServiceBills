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


def _enable_whish(app, tenant_name):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        tenant.plan = 'pro'
        appmod.db.session.add(appmod.TenantWhishSettings(
            tenant_id=tenant.id, enabled=True, whish_channel="c", whish_secret="s"))
        appmod.db.session.commit()


def _make_attempt_with_external_id(app, tenant_name, amount=25.0, currency='USD'):
    """Create a pending CustomerWhishPaymentAttempt directly (bypassing
    checkout, matching test_tenant_whish_customer_payments.py's
    _make_link_with_external_id pattern) so success/failure callback tests
    don't need a real/mocked Whish HTTP round-trip."""
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        customer = appmod.Customer.query.filter_by(tenant_id=tenant.id).first()
        attempt = appmod.CustomerWhishPaymentAttempt(
            tenant_id=tenant.id, customer_id=customer.id, amount=amount, currency=currency,
            callback_token=secrets.token_urlsafe(32), whish_external_id=f"cwpa-ext-{secrets.token_hex(4)}",
        )
        appmod.db.session.add(attempt)
        appmod.db.session.commit()
        return attempt.whish_external_id, attempt.callback_token, attempt.id, customer.id


def test_checkout_creates_attempt_and_redirects(client, app, monkeypatch):
    slug, _ = _make_branded_tenant(app, client, "Biz Checkout1")
    _enable_whish(app, "Biz Checkout1")
    customer_id = _add_customer(app, "Biz Checkout1", "Nadia", "70123456")
    monkeypatch.setattr(appmod.whish_billing, "create_payment",
                         lambda **kw: "https://whish.money/pay/tenant-own")
    r = client.post(f"/api/pay/t/{slug}/checkout", json={"customer_id": customer_id, "amount": 25.0})
    assert r.status_code == 200
    assert r.get_json()["redirect"] == "https://whish.money/pay/tenant-own"
    with app.app_context():
        attempt = appmod.CustomerWhishPaymentAttempt.query.filter_by(customer_id=customer_id).first()
        assert attempt is not None
        assert attempt.amount == 25.0
        assert attempt.whish_external_id is not None


def test_checkout_rejects_customer_from_another_tenant(client, app):
    slug, _ = _make_branded_tenant(app, client, "Biz Checkout2")
    _enable_whish(app, "Biz Checkout2")
    make_tenant(client, "Biz Checkout2 Other", "checkout2_other_admin")
    other_customer_id = _add_customer(app, "Biz Checkout2 Other", "Other", "71000000")
    r = client.post(f"/api/pay/t/{slug}/checkout", json={"customer_id": other_customer_id, "amount": 10.0})
    assert r.status_code == 404  # generic, not a leaky 403


def test_checkout_rejects_when_whish_not_enabled(client, app):
    slug, _ = _make_branded_tenant(app, client, "Biz Checkout3")
    customer_id = _add_customer(app, "Biz Checkout3", "Nadia", "70123456")
    r = client.post(f"/api/pay/t/{slug}/checkout", json={"customer_id": customer_id, "amount": 10.0})
    # Matches the per-link checkout route's (Task 8) own not-enabled
    # response (app.py's public_pay_checkout) -- 503, not a generic 404.
    assert r.status_code == 503


def test_checkout_rejects_when_whish_api_fails(client, app, monkeypatch):
    slug, _ = _make_branded_tenant(app, client, "Biz Checkout5")
    _enable_whish(app, "Biz Checkout5")
    customer_id = _add_customer(app, "Biz Checkout5", "Nadia", "70123456")
    monkeypatch.setattr(appmod.whish_billing, "create_payment",
                         lambda **kw: (_ for _ in ()).throw(appmod.whish_billing.WhishAPIError("boom")))
    r = client.post(f"/api/pay/t/{slug}/checkout", json={"customer_id": customer_id, "amount": 10.0})
    assert r.status_code == 502


def test_success_pays_down_debt_fully_no_prepayment(client, app):
    _make_branded_tenant(app, client, "Biz Success1")
    _enable_whish(app, "Biz Success1")
    customer_id = _add_customer(app, "Biz Success1", "Nadia", "70123456")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Success1").first()
        payment = appmod.Payment(tenant_id=tenant.id, customer_id=customer_id, amount=40.0,
                                  currency="USD", paid=False, date=appmod.datetime.utcnow())
        appmod.db.session.add(payment)
        appmod.db.session.commit()
        payment_id = payment.id
    ext_id, cb_token, attempt_id, _ = _make_attempt_with_external_id(app, "Biz Success1", amount=40.0)
    r = client.get(f"/api/pay-attempt/success?order={ext_id}&token={cb_token}", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        payment = appmod.db.session.get(appmod.Payment, payment_id)
        assert payment.paid is True
        assert payment.collected_via == 'whish'
        prepayment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).first()
        assert prepayment is None
        attempt = appmod.db.session.get(appmod.CustomerWhishPaymentAttempt, attempt_id)
        assert attempt.status == 'succeeded'
        assert float(attempt.applied_to_debt) == 40.0
        assert float(attempt.applied_as_prepayment) == 0.0


def test_success_debt_partially_covered_remainder_is_prepayment(client, app):
    _make_branded_tenant(app, client, "Biz Success2")
    _enable_whish(app, "Biz Success2")
    customer_id = _add_customer(app, "Biz Success2", "Nadia", "70123456")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Success2").first()
        payment = appmod.Payment(tenant_id=tenant.id, customer_id=customer_id, amount=40.0,
                                  currency="USD", paid=False, date=appmod.datetime.utcnow())
        appmod.db.session.add(payment)
        appmod.db.session.commit()
    ext_id, cb_token, _, _ = _make_attempt_with_external_id(app, "Biz Success2", amount=60.0)
    r = client.get(f"/api/pay-attempt/success?order={ext_id}&token={cb_token}", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        payment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=False).first()
        assert payment.paid is True
        prepayment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).first()
        assert prepayment is not None
        assert float(prepayment.amount) == 20.0
        assert prepayment.paid is True
        assert prepayment.collected_via == 'whish'


def test_success_no_debt_entire_amount_is_prepayment(client, app):
    _make_branded_tenant(app, client, "Biz Success3")
    _enable_whish(app, "Biz Success3")
    customer_id = _add_customer(app, "Biz Success3", "Nadia", "70123456")
    ext_id, cb_token, _, _ = _make_attempt_with_external_id(app, "Biz Success3", amount=15.0)
    r = client.get(f"/api/pay-attempt/success?order={ext_id}&token={cb_token}", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        prepayment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).first()
        assert prepayment is not None
        assert float(prepayment.amount) == 15.0


def test_success_never_partially_marks_a_single_payment(client, app):
    # Two unpaid Payments of 40.0 each; attempt.amount == 50.0 -> the older
    # one is paid in full (40.0 applied), the remaining 10.0 becomes a
    # prepayment -- the second unpaid Payment is left untouched, never
    # partially reduced. Mirrors apply_customer_balance_to_unpaid_payments's
    # existing all-or-nothing-per-row behavior (app.py:1358).
    _make_branded_tenant(app, client, "Biz Success4")
    _enable_whish(app, "Biz Success4")
    customer_id = _add_customer(app, "Biz Success4", "Nadia", "70123456")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Success4").first()
        p1 = appmod.Payment(tenant_id=tenant.id, customer_id=customer_id, amount=40.0,
                             currency="USD", paid=False, date=appmod.datetime.utcnow() - timedelta(days=2))
        p2 = appmod.Payment(tenant_id=tenant.id, customer_id=customer_id, amount=40.0,
                             currency="USD", paid=False, date=appmod.datetime.utcnow() - timedelta(days=1))
        appmod.db.session.add_all([p1, p2])
        appmod.db.session.commit()
        p1_id, p2_id = p1.id, p2.id
    ext_id, cb_token, _, _ = _make_attempt_with_external_id(app, "Biz Success4", amount=50.0)
    r = client.get(f"/api/pay-attempt/success?order={ext_id}&token={cb_token}", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        p1 = appmod.db.session.get(appmod.Payment, p1_id)
        p2 = appmod.db.session.get(appmod.Payment, p2_id)
        assert p1.paid is True
        assert p2.paid is False  # left untouched, not partially reduced
        prepayment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).first()
        assert float(prepayment.amount) == 10.0


def test_success_sends_whatsapp_payment_paid_confirmation(client, app, monkeypatch):
    _make_branded_tenant(app, client, "Biz Success5")
    _enable_whish(app, "Biz Success5")
    customer_id = _add_customer(app, "Biz Success5", "Nadia", "70123456")
    sent = []
    monkeypatch.setattr(appmod, 'send_whatsapp_message',
                         lambda customer, event_type, context=None: sent.append((event_type, context)))
    ext_id, cb_token, _, _ = _make_attempt_with_external_id(app, "Biz Success5", amount=15.0)
    r = client.get(f"/api/pay-attempt/success?order={ext_id}&token={cb_token}", follow_redirects=False)
    assert r.status_code == 302
    assert sent[0][0] == 'payment_paid'
    assert sent[0][1]['amount'] == 15.0


def test_success_callback_wrong_token_rejected(client, app):
    _make_branded_tenant(app, client, "Biz Success6")
    _enable_whish(app, "Biz Success6")
    _add_customer(app, "Biz Success6", "Nadia", "70123456")
    ext_id, cb_token, attempt_id, _ = _make_attempt_with_external_id(app, "Biz Success6", amount=15.0)
    r = client.get(f"/api/pay-attempt/success?order={ext_id}&token=totally-wrong", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        attempt = appmod.db.session.get(appmod.CustomerWhishPaymentAttempt, attempt_id)
        assert attempt.status == 'pending'  # untouched


def test_success_callback_is_single_use(client, app):
    _make_branded_tenant(app, client, "Biz Success7")
    _enable_whish(app, "Biz Success7")
    customer_id = _add_customer(app, "Biz Success7", "Nadia", "70123456")
    ext_id, cb_token, _, _ = _make_attempt_with_external_id(app, "Biz Success7", amount=15.0)
    r1 = client.get(f"/api/pay-attempt/success?order={ext_id}&token={cb_token}", follow_redirects=False)
    assert r1.status_code == 302
    r2 = client.get(f"/api/pay-attempt/success?order={ext_id}&token={cb_token}", follow_redirects=False)
    assert r2.status_code == 302
    with app.app_context():
        prepayments = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).all()
        assert len(prepayments) == 1  # second call didn't double-apply


def test_failure_callback_marks_attempt_failed(client, app):
    _make_branded_tenant(app, client, "Biz Failure1")
    _enable_whish(app, "Biz Failure1")
    _add_customer(app, "Biz Failure1", "Nadia", "70123456")
    ext_id, cb_token, attempt_id, _ = _make_attempt_with_external_id(app, "Biz Failure1", amount=15.0)
    r = client.get(f"/api/pay-attempt/failure?order={ext_id}&token={cb_token}", follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        attempt = appmod.db.session.get(appmod.CustomerWhishPaymentAttempt, attempt_id)
        assert attempt.status == 'failed'


def _make_pro_tenant(client, business_name, admin_name):
    hdr = make_tenant(client, business_name, admin_name)
    with client.application.app_context():
        tenant = appmod.Tenant.query.filter_by(name=business_name).first()
        tenant.plan = 'pro'
        appmod.db.session.commit()
    return hdr


def test_regenerate_public_pay_slug_requires_pro(client, app):
    hdr = make_tenant(client, "Biz Slug1", "slug1_admin")  # Free plan by default
    r = client.post("/api/tenant/whish/public-pay-link/regenerate", headers=hdr)
    assert r.status_code == 402


def test_regenerate_public_pay_slug_sets_and_changes_it(client, app):
    hdr = _make_pro_tenant(client, "Biz Slug2", "slug2_admin")
    r1 = client.post("/api/tenant/whish/public-pay-link/regenerate", headers=hdr)
    assert r1.status_code == 200
    slug1 = r1.get_json()["slug"]
    assert slug1
    r2 = client.post("/api/tenant/whish/public-pay-link/regenerate", headers=hdr)
    slug2 = r2.get_json()["slug"]
    assert slug1 != slug2  # old link is deliberately invalidated -- see this task's Judgment call


def test_get_public_pay_link_returns_none_when_not_generated(client, app):
    hdr = _make_pro_tenant(client, "Biz Slug3", "slug3_admin")
    r = client.get("/api/tenant/whish/public-pay-link", headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["slug"] is None
