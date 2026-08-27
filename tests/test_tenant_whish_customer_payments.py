"""Tenant-facing Whish customer payments -- see
docs/superpowers/specs/2026-08-27-tenant-whish-customer-payments-design.md.
Distinct from tests/test_whish_billing.py (platform billing: tenant -> ServiceBills).
This feature is: a tenant's own customer -> that tenant, via that tenant's own
Whish credentials. whish_billing.create_payment (the HTTP client) is shared and
reused unmodified; nothing else is."""
import secrets
from datetime import timedelta

import app as appmod
from tests.conftest import make_tenant


def test_tenant_whish_settings_model_roundtrip_and_encryption(app, client):
    make_tenant(client, "Biz TWS", "tws_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz TWS").first()
        settings = appmod.TenantWhishSettings(
            tenant_id=tenant.id, enabled=True,
            whish_channel="chan-123", whish_secret="sec-456",
        )
        appmod.db.session.add(settings)
        appmod.db.session.commit()

        fetched = appmod.TenantWhishSettings.query.filter_by(tenant_id=tenant.id).first()
        assert fetched.whish_channel == "chan-123"  # decrypts transparently on read
        assert fetched.whish_secret == "sec-456"

        # Confirm it's actually encrypted at rest, not merely round-tripping --
        # mirrors test_crypto.py's existing pattern for WhatsAppSettings' fields.
        raw = appmod.db.session.execute(
            appmod.db.text("SELECT whish_channel FROM tenant_whish_settings WHERE tenant_id = :tid"),
            {"tid": tenant.id},
        ).scalar()
        if appmod.Config.FERNET_KEY:
            assert raw != "chan-123"
        # If FERNET_KEY is unset (as in this test env by default), crypto.py
        # passes values through unchanged -- see crypto.py's own docstring --
        # so this assertion is conditional, matching how test_crypto.py handles it.


def test_tenant_whish_settings_is_tenant_isolated(app, client):
    make_tenant(client, "Biz TWS A", "tws_a_admin")
    make_tenant(client, "Biz TWS B", "tws_b_admin")
    with app.app_context():
        tenant_a = appmod.Tenant.query.filter_by(name="Biz TWS A").first()
        appmod.db.session.add(appmod.TenantWhishSettings(
            tenant_id=tenant_a.id, enabled=True, whish_channel="a-chan", whish_secret="a-sec"))
        appmod.db.session.commit()
    with app.app_context():
        tenant_b = appmod.Tenant.query.filter_by(name="Biz TWS B").first()
        assert appmod.TenantWhishSettings.query.filter_by(tenant_id=tenant_b.id).first() is None


def test_get_tenant_whish_settings_returns_defaults_when_unconfigured(app, client):
    hdr = make_tenant(client, "Biz TWS Get", "tws_get_admin")
    r = client.get("/api/tenant-whish-settings", headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["settings"]["enabled"] is False
    assert r.get_json()["settings"]["configured"] is False


def test_save_tenant_whish_settings_rejected_on_free_plan(app, client):
    hdr = make_tenant(client, "Biz TWS Free", "tws_free_admin")
    r = client.post("/api/tenant-whish-settings", headers=hdr,
                     json={"enabled": True, "whish_channel": "c1", "whish_secret": "s1"})
    assert r.status_code == 402


def test_save_tenant_whish_settings_succeeds_on_pro_plan(app, client):
    hdr = make_tenant(client, "Biz TWS Pro", "tws_pro_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz TWS Pro").first()
        tenant.plan = 'pro'
        appmod.db.session.commit()
    r = client.post("/api/tenant-whish-settings", headers=hdr,
                     json={"enabled": True, "whish_channel": "c1", "whish_secret": "s1"})
    assert r.status_code == 200
    assert r.get_json()["settings"]["configured"] is True
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz TWS Pro").first()
        settings = appmod.TenantWhishSettings.query.filter_by(tenant_id=tenant.id).first()
        assert settings.whish_channel == "c1"


def test_save_tenant_whish_settings_enabled_requires_both_credentials(app, client):
    hdr = make_tenant(client, "Biz TWS Partial", "tws_partial_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz TWS Partial").first()
        tenant.plan = 'pro'
        appmod.db.session.commit()
    # enabled=True but only one credential set -- server-side coerces to
    # enabled=False rather than trusting the client's flag, since "enabled"
    # is what Task 6's auto-link-generation hook checks.
    r = client.post("/api/tenant-whish-settings", headers=hdr,
                     json={"enabled": True, "whish_channel": "c1", "whish_secret": ""})
    assert r.status_code == 200
    assert r.get_json()["settings"]["enabled"] is False


def test_customer_payment_link_model_roundtrip(app, client):
    make_tenant(client, "Biz CPL", "cpl_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz CPL").first()
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="Test Plan", price=50.0,
                                        billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(tenant_id=tenant.id, name="Jane Doe", phone="+96170123456",
                                    subscription_plan_id=plan.id, address="Beirut", subscription_expiry_date=appmod.datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        payment = appmod.Payment(tenant_id=tenant.id, customer_id=customer.id, amount=50.0,
                                  currency="USD", paid=False, date=appmod.datetime.utcnow())
        appmod.db.session.add(payment)
        appmod.db.session.commit()

        link = appmod.CustomerPaymentLink(
            tenant_id=tenant.id, customer_id=customer.id, payment_id=payment.id,
            amount=payment.amount, currency=payment.currency,
            view_token=secrets.token_urlsafe(32), callback_token=secrets.token_urlsafe(32),
            status='pending', expires_at=appmod.datetime.utcnow() + timedelta(days=7),
        )
        appmod.db.session.add(link)
        appmod.db.session.commit()

        fetched = appmod.CustomerPaymentLink.query.filter_by(payment_id=payment.id).first()
        assert fetched.status == 'pending'
        assert fetched.tenant_id == tenant.id
        assert fetched.customer_id == customer.id
        assert len(fetched.view_token) > 32 and fetched.view_token != fetched.callback_token


def test_customer_payment_link_goes_stale_when_payment_amount_changes(app, client):
    make_tenant(client, "Biz Stale1", "stale1_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Stale1").first()
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="P", price=50.0,
                                        billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(tenant_id=tenant.id, name="Jane", phone="+96170000001",
                                    subscription_plan_id=plan.id, address="Beirut", subscription_expiry_date=appmod.datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        payment = appmod.Payment(tenant_id=tenant.id, customer_id=customer.id, amount=50.0,
                                  currency="USD", paid=False, date=appmod.datetime.utcnow())
        appmod.db.session.add(payment)
        appmod.db.session.commit()
        link = appmod.CustomerPaymentLink(
            tenant_id=tenant.id, customer_id=customer.id, payment_id=payment.id,
            amount=payment.amount, currency=payment.currency,
            view_token=secrets.token_urlsafe(32), callback_token=secrets.token_urlsafe(32),
            status='pending', expires_at=appmod.datetime.utcnow() + timedelta(days=7),
        )
        appmod.db.session.add(link)
        appmod.db.session.commit()
        payment_id, link_id = payment.id, link.id

    with app.app_context():
        payment = appmod.db.session.get(appmod.Payment, payment_id)
        payment.amount = 75.0  # staff edits the amount after the link was generated
        appmod.db.session.commit()
        link = appmod.db.session.get(appmod.CustomerPaymentLink, link_id)
        assert link.status == 'stale'


def test_customer_payment_link_goes_stale_when_payment_marked_paid_out_of_band(app, client):
    # Guards against: link generated, then staff marks the Payment paid through
    # the normal admin flow WITHOUT going through this link -- the link must not
    # remain "pending" and payable a second time.
    make_tenant(client, "Biz Stale2", "stale2_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Stale2").first()
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="P", price=20.0,
                                        billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(tenant_id=tenant.id, name="Sam", phone="+96170000002",
                                    subscription_plan_id=plan.id, address="Beirut", subscription_expiry_date=appmod.datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        payment = appmod.Payment(tenant_id=tenant.id, customer_id=customer.id, amount=20.0,
                                  currency="USD", paid=False, date=appmod.datetime.utcnow())
        appmod.db.session.add(payment)
        appmod.db.session.commit()
        link = appmod.CustomerPaymentLink(
            tenant_id=tenant.id, customer_id=customer.id, payment_id=payment.id,
            amount=payment.amount, currency=payment.currency,
            view_token=secrets.token_urlsafe(32), callback_token=secrets.token_urlsafe(32),
            status='pending', expires_at=appmod.datetime.utcnow() + timedelta(days=7),
        )
        appmod.db.session.add(link)
        appmod.db.session.commit()
        payment_id, link_id = payment.id, link.id

    with app.app_context():
        payment = appmod.db.session.get(appmod.Payment, payment_id)
        payment.paid = True
        payment.paid_at = appmod.datetime.utcnow()
        appmod.db.session.commit()
        link = appmod.db.session.get(appmod.CustomerPaymentLink, link_id)
        assert link.status == 'stale'


def test_customer_payment_link_unaffected_by_mutation_when_not_pending(app, client):
    # A link that's already succeeded/failed/expired/stale must not be
    # re-touched by a later, unrelated Payment mutation -- the guard only
    # ever acts on status == 'pending'.
    make_tenant(client, "Biz StaleNoop", "stalenoop_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz StaleNoop").first()
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="P", price=20.0,
                                        billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(tenant_id=tenant.id, name="Sam", phone="+96170000003",
                                    subscription_plan_id=plan.id, address="Beirut", subscription_expiry_date=appmod.datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        payment = appmod.Payment(tenant_id=tenant.id, customer_id=customer.id, amount=20.0,
                                  currency="USD", paid=True, date=appmod.datetime.utcnow())
        appmod.db.session.add(payment)
        appmod.db.session.commit()
        link = appmod.CustomerPaymentLink(
            tenant_id=tenant.id, customer_id=customer.id, payment_id=payment.id,
            amount=payment.amount, currency=payment.currency,
            view_token=secrets.token_urlsafe(32), callback_token=secrets.token_urlsafe(32),
            status='succeeded', expires_at=appmod.datetime.utcnow() + timedelta(days=7),
        )
        appmod.db.session.add(link)
        appmod.db.session.commit()
        payment_id, link_id = payment.id, link.id

    with app.app_context():
        payment = appmod.db.session.get(appmod.Payment, payment_id)
        payment.reason = "note added later"  # unrelated field, and link is not pending
        appmod.db.session.commit()
        link = appmod.db.session.get(appmod.CustomerPaymentLink, link_id)
        assert link.status == 'succeeded'  # untouched


def _enable_whish_for_tenant(app, tenant_name, currency='USD'):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        tenant.plan = 'pro'
        appmod.db.session.add(appmod.TenantWhishSettings(
            tenant_id=tenant.id, enabled=True, whish_channel="c", whish_secret="s"))
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="P", price=30.0,
                                        billing_cycle="monthly", currency=currency)
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(tenant_id=tenant.id, name="Nadia", phone="+96170000099",
                                    subscription_plan_id=plan.id, address="Beirut",
                                    subscription_expiry_date=appmod.datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        return tenant.id, customer.id


def test_add_payment_creates_customer_payment_link_when_whish_enabled(app, client):
    hdr = make_tenant(client, "Biz Hook1", "hook1_admin")
    tenant_id, customer_id = _enable_whish_for_tenant(app, "Biz Hook1")
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 30.0, "reason": "Monthly",
        "date": appmod.datetime.utcnow().strftime('%Y-%m-%d'), "pre_payment": False,
    })
    assert r.status_code == 201
    payment_id = r.get_json()['payment']['id']
    with app.app_context():
        link = appmod.CustomerPaymentLink.query.filter_by(payment_id=payment_id).first()
        assert link is not None
        assert link.status == 'pending'
        assert link.amount == 30.0


def test_add_payment_no_link_when_whish_not_enabled(app, client):
    hdr = make_tenant(client, "Biz Hook2", "hook2_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Hook2").first()
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="P", price=30.0,
                                        billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(tenant_id=tenant.id, name="Ali", phone="+96170000098",
                                    subscription_plan_id=plan.id, address="Beirut",
                                    subscription_expiry_date=appmod.datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        customer_id = customer.id
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 30.0, "reason": "Monthly",
        "date": appmod.datetime.utcnow().strftime('%Y-%m-%d'), "pre_payment": False,
    })
    assert r.status_code == 201
    payment_id = r.get_json()['payment']['id']
    with app.app_context():
        assert appmod.CustomerPaymentLink.query.filter_by(payment_id=payment_id).first() is None


def test_add_payment_no_link_for_non_whish_currency(app, client):
    hdr = make_tenant(client, "Biz Hook3", "hook3_admin")
    tenant_id, customer_id = _enable_whish_for_tenant(app, "Biz Hook3")
    with app.app_context():
        appmod.db.session.add(appmod.Currency(code='EUR', name='Euro', decimal_places=2))
        # add_payment() locks an FX rate at creation time (multi-currency
        # accounting) -- without one on file for EUR->USD it 400s before ever
        # reaching Payment creation, regardless of this test's actual concern.
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant_id, from_currency='EUR', to_currency='USD', rate=1.1,
            effective_at=appmod.datetime.utcnow() - timedelta(days=1),
        ))
        plan = appmod.SubscriptionPlan(tenant_id=tenant_id, name="EuroPlan", price=30.0,
                                        billing_cycle="monthly", currency="EUR")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        customer = appmod.Customer(tenant_id=tenant_id, name="Marc", phone="+33600000000",
                                    subscription_plan_id=plan.id, address="Paris",
                                    subscription_expiry_date=appmod.datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        eur_customer_id = customer.id
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": eur_customer_id, "amount": 30.0, "reason": "Monthly",
        "currency": "EUR",
        "date": appmod.datetime.utcnow().strftime('%Y-%m-%d'), "pre_payment": False,
    })
    assert r.status_code == 201
    payment_id = r.get_json()['payment']['id']
    with app.app_context():
        assert appmod.CustomerPaymentLink.query.filter_by(payment_id=payment_id).first() is None


def test_add_payment_no_link_when_payment_created_already_paid(app, client):
    # add_payment()'s real contract: is_paid is derived from `pre_payment`
    # (a pre-payment is paid at creation), not a separate `is_paid` field.
    hdr = make_tenant(client, "Biz Hook4", "hook4_admin")
    tenant_id, customer_id = _enable_whish_for_tenant(app, "Biz Hook4")
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 30.0, "reason": "Monthly",
        "date": appmod.datetime.utcnow().strftime('%Y-%m-%d'), "pre_payment": True,
    })
    assert r.status_code == 201
    payment_id = r.get_json()['payment']['id']
    with app.app_context():
        payment = appmod.db.session.get(appmod.Payment, payment_id)
        assert payment.paid is True  # confirms the premise: pre_payment=True -> paid=True
        assert appmod.CustomerPaymentLink.query.filter_by(payment_id=payment_id).first() is None


def test_generate_missing_payments_scheduler_job_creates_links(app, client):
    # Exercises call site #2 (the daily scheduler job) directly, mirroring how
    # tests/test_whish_billing.py calls check_pro_plan_expirations_for_tenant
    # directly rather than through the scheduler itself.
    #
    # generate_missing_payments' billing cursor for a non-reseller customer
    # with no prior payment is subscription_start_date (NOT
    # subscription_expiry_date, which the plan's own test assumed) -- back-
    # date that instead so a monthly-cycle billing date falls due.
    hdr = make_tenant(client, "Biz Hook5", "hook5_admin")
    tenant_id, customer_id = _enable_whish_for_tenant(app, "Biz Hook5")
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        customer.subscription_start_date = appmod.datetime.utcnow() - timedelta(days=40)
        appmod.db.session.commit()
        appmod.generate_missing_payments(tenant_id)
        links = appmod.CustomerPaymentLink.query.filter_by(customer_id=customer_id).all()
        assert len(links) >= 1
