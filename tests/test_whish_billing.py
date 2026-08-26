"""Self-serve Pro plan via Whish (Lebanon payment gateway) -- see
docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md.
Stripe stays fully dormant; these tests never touch billing.py."""
import app as appmod
from tests.conftest import make_tenant


def test_tenant_has_plan_expiry_fields(app, client):
    make_tenant(client, "Biz Expiry", "expiry_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Expiry").first()
        assert tenant.plan_expires_at is None
        assert tenant.plan_expiry_reminder_sent_at is None
        d = tenant.to_dict()
        assert "plan_expires_at" in d and d["plan_expires_at"] is None


def test_billing_payment_attempt_model_roundtrip(app, client):
    make_tenant(client, "Biz Attempt", "attempt_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Attempt").first()
        attempt = appmod.BillingPaymentAttempt(
            tenant_id=tenant.id, billing_cycle="monthly", amount=120.0,
            currency="USD", whish_external_id="ext-1", callback_token="tok-1",
        )
        appmod.db.session.add(attempt)
        appmod.db.session.commit()
        fetched = appmod.BillingPaymentAttempt.query.filter_by(whish_external_id="ext-1").first()
        assert fetched is not None
        assert fetched.status == "pending"
        assert fetched.tenant_id == tenant.id
        assert fetched.completed_at is None


import plans as plansmod


def test_pro_plan_has_whish_prices():
    assert plansmod.PLANS['pro']['whish_price_monthly'] == 120.0
    assert plansmod.PLANS['pro']['whish_price_yearly'] == 1000.0
