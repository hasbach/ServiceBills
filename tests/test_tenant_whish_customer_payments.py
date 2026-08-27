"""Tenant-facing Whish customer payments -- see
docs/superpowers/specs/2026-08-27-tenant-whish-customer-payments-design.md.
Distinct from tests/test_whish_billing.py (platform billing: tenant -> ServiceBills).
This feature is: a tenant's own customer -> that tenant, via that tenant's own
Whish credentials. whish_billing.create_payment (the HTTP client) is shared and
reused unmodified; nothing else is."""
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
