import app as appmod
from tests.conftest import make_tenant
from tests.test_module_gating_api import _set_overrides


def test_whish_settings_blocked_on_free(app, client):
    hdr = make_tenant(client, "Wh Free", "whfree_admin")
    r = client.get("/api/tenant-whish-settings", headers=hdr)
    assert r.status_code == 403 and r.get_json()["module"] == "whish_payments"


def test_whish_settings_open_with_override(app, client):
    hdr = make_tenant(client, "Wh On", "whon_admin")
    _set_overrides(app, "wh-on", {"whish_payments": True})
    assert client.get("/api/tenant-whish-settings", headers=hdr).status_code == 200


def test_public_pay_page_404_when_module_off(app, client):
    make_tenant(client, "Wh Pub", "whpub_admin")
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug="wh-pub").first()
        t.public_pay_slug = "pubslug1"
        t.module_overrides = {"whish_payments": True}
        appmod.db.session.commit()
    assert client.get("/api/pay/t/pubslug1").status_code == 200
    _set_overrides(app, "wh-pub", {"whish_payments": False})
    assert client.get("/api/pay/t/pubslug1").status_code == 404


def test_plans_no_longer_carry_whish_flag():
    import plans
    assert all("whish_customer_payments" not in p for p in plans.PLANS.values())
