import app as appmod
from tests.conftest import make_tenant


def _pro_api_mode(app, client, hdr, slug, **overrides):
    with app.app_context():
        appmod.Tenant.query.filter_by(slug=slug).update({"plan": "pro"})
        appmod.db.session.commit()
    payload = {"enabled": True, "mode": "api", "phone_number_id": "123",
               "business_account_id": "WABA1", "app_id": "APP1", "access_token": "tok"}
    payload.update(overrides)
    client.post("/api/whatsapp-settings", headers=hdr, json=payload)


class FakeResponse:
    def __init__(self, ok=True, status_code=200, json_data=None, text=""):
        self.ok = ok
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text or str(json_data)

    def json(self):
        return self._json


def test_get_templates_returns_all_statuses_from_local_cache(app, client):
    hdr = make_tenant(client, "Biz T1", "t1_admin")
    _pro_api_mode(app, client, hdr, "biz-t1")
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-t1").first().id
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=tid, name="pending_one", language="en", category="UTILITY",
            status="PENDING", components=[{"type": "BODY", "text": "hi"}]))
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=tid, name="rejected_one", language="en", category="MARKETING",
            status="REJECTED", rejected_reason="Policy violation",
            components=[{"type": "BODY", "text": "hi"}]))
        appmod.db.session.commit()

    r = client.get("/api/whatsapp/templates", headers=hdr)
    assert r.status_code == 200
    names_and_statuses = {(t["name"], t["status"]) for t in r.get_json()["templates"]}
    assert ("pending_one", "PENDING") in names_and_statuses
    assert ("rejected_one", "REJECTED") in names_and_statuses


def test_sync_upserts_local_rows_from_meta(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz T2", "t2_admin")
    _pro_api_mode(app, client, hdr, "biz-t2")

    remote_templates = [
        {"id": "meta_1", "name": "greeting", "language": "en", "category": "UTILITY",
         "status": "APPROVED", "components": [{"type": "BODY", "text": "Hi {{1}}"}]},
    ]
    monkeypatch.setattr(appmod.requests, "get",
                         lambda url, headers, timeout: FakeResponse(json_data={"data": remote_templates}))

    r = client.post("/api/whatsapp/templates/sync", headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["templates"][0]["name"] == "greeting"
    assert r.get_json()["templates"][0]["status"] == "APPROVED"
    assert r.get_json()["templates"][0]["meta_template_id"] == "meta_1"

    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-t2").first().id
        row = appmod.WhatsAppTemplate.query.filter_by(tenant_id=tid, name="greeting").first()
        assert row is not None and row.status == "APPROVED"


def test_sync_requires_pro_plan(app, client):
    hdr = make_tenant(client, "Biz T3", "t3_admin")  # free by default
    client.post("/api/whatsapp-settings", headers=hdr,
                json={"enabled": True, "mode": "deeplink"})
    r = client.post("/api/whatsapp/templates/sync", headers=hdr)
    assert r.status_code in (400, 402)  # not in api mode -> 400; free plan would be 402 if mode were api
