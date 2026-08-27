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
