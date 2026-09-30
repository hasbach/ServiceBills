import pytest

import app as appmod
from tests.conftest import make_tenant


@pytest.fixture
def pay_tenant(app, client):
    hdr = make_tenant(client, "Pub Url", "puburl_admin")
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug="pub-url").first()
        t.public_pay_slug = "puburlslug"
        t.module_overrides = {"whish_payments": True, "whatsapp": True}
        appmod.db.session.commit()
        tid = t.id
    return hdr, tid


def _set_public_url(app, tid, url):
    with app.app_context():
        bs = appmod.BusinessSettings.query.filter_by(tenant_id=tid).first()
        if not bs:
            bs = appmod.BusinessSettings(tenant_id=tid, business_name="B", address="a",
                                         mobile="1")
            appmod.db.session.add(bs)
        bs.public_url = url
        appmod.db.session.commit()


@pytest.fixture
def onprem_mode(app):
    old = app.config.get("DEPLOYMENT_MODE")
    app.config["DEPLOYMENT_MODE"] = "onprem"
    yield
    if old is None:
        app.config.pop("DEPLOYMENT_MODE", None)
    else:
        app.config["DEPLOYMENT_MODE"] = old


def test_saas_ignores_public_url(app, client, pay_tenant):
    hdr, tid = pay_tenant
    _set_public_url(app, tid, "https://biz.example.com")
    r = client.get("/api/tenant/whish/public-pay-link", headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["url"].startswith(appmod.Config.APP_BASE_URL + "/pay-business")
    with app.app_context():
        assert appmod._public_base_url(tid) == appmod.Config.APP_BASE_URL


def test_onprem_uses_public_url(app, client, pay_tenant, monkeypatch):
    import modules
    hdr, tid = pay_tenant
    _set_public_url(app, tid, "https://biz.example.com")
    monkeypatch.setattr(modules, "license_provider",
                        lambda tenant=None: {"modules": {"whish_payments": {"expires_at": "9999-12-31"}}},
                        raising=False)
    old = app.config.get("DEPLOYMENT_MODE")
    app.config["DEPLOYMENT_MODE"] = "onprem"
    try:
        r = client.get("/api/tenant/whish/public-pay-link", headers=hdr)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["url"] == "https://biz.example.com/pay-business?slug=puburlslug"
        with app.app_context():
            assert appmod._public_base_url(tid) == "https://biz.example.com"
            assert appmod._public_base_url() == "https://biz.example.com"
    finally:
        if old is None:
            app.config.pop("DEPLOYMENT_MODE", None)
        else:
            app.config["DEPLOYMENT_MODE"] = old


def test_onprem_without_public_url_falls_back(app, pay_tenant, onprem_mode):
    _, tid = pay_tenant
    with app.app_context():
        assert appmod._public_base_url(tid) == appmod.Config.APP_BASE_URL


def test_business_settings_public_url_validation(client, pay_tenant):
    hdr, _ = pay_tenant
    r = client.post("/api/business-settings", headers=hdr,
                    data={"business_name": "B", "address": "a", "mobile": "1",
                          "public_url": "ftp://x"})
    assert r.status_code == 400
    r = client.post("/api/business-settings", headers=hdr,
                    data={"business_name": "B", "address": "a", "mobile": "1",
                          "public_url": "https://a.b/"})
    assert r.status_code == 200
    assert r.get_json()["settings"]["public_url"] == "https://a.b"
    r = client.post("/api/business-settings", headers=hdr, data={"public_url": ""})
    assert r.status_code == 200
    assert r.get_json()["settings"]["public_url"] is None


def test_whatsapp_settings_has_webhook_url(client, pay_tenant):
    hdr, _ = pay_tenant
    r = client.get("/api/whatsapp-settings", headers=hdr)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["webhook_url"].endswith("/api/whatsapp/webhook")
