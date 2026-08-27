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
