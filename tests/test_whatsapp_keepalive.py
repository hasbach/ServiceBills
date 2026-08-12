"""Tests for send_daily_whatsapp_keepalive -- the daily template send that
prompts an auto-reply on forwarding_mobile's own device, which is what
actually opens the 24h session the restored raw customer-reply forward in
the webhook handler depends on. See
docs/superpowers/specs/2026-08-12-whatsapp-forwarding-keepalive.md.
"""
from datetime import datetime, timedelta

import app as appmod
from tests.conftest import make_tenant


def _configure_api_mode(app, client, hdr, slug, **overrides):
    # 'api' mode is plan-gated (whatsapp_api, see plans.py) -- a fresh tenant
    # starts on 'free', which the save endpoint would otherwise 402 on.
    with app.app_context():
        appmod.Tenant.query.filter_by(slug=slug).update({"plan": "pro"})
        appmod.db.session.commit()

    payload = {
        "enabled": True, "mode": "api",
        "phone_number_id": "123", "access_token": "tok",
        "forwarding_mobile": "96170000000",
    }
    payload.update(overrides)
    client.post("/api/whatsapp-settings", headers=hdr, json=payload)


class FakeResponse:
    def __init__(self, ok=True, status_code=200, text=""):
        self.ok = ok
        self.status_code = status_code
        self.text = text


def test_keepalive_sends_template_and_records_timestamp(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz KA", "ka_admin")
    _configure_api_mode(app, client, hdr, "biz-ka")

    calls = []
    monkeypatch.setattr(appmod.requests, "post",
                         lambda url, json, headers, timeout: (calls.append((url, json)), FakeResponse())[1])

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(slug="biz-ka").first()
        appmod.send_daily_whatsapp_keepalive(tenant.id)

        assert len(calls) == 1
        url, payload = calls[0]
        assert payload["to"] == "96170000000"
        assert payload["type"] == "template"
        assert payload["template"]["name"] == "daily_checkin"

        settings = appmod.WhatsAppSettings.query.filter_by(tenant_id=tenant.id).first()
        assert settings.last_forwarding_keepalive_sent_at is not None


def test_keepalive_falls_back_to_fewer_params_until_accepted(app, client, monkeypatch):
    # Simulates reusing an already-approved template (e.g. customer_reply_alert)
    # whose exact placeholder count isn't known ahead of time -- only the
    # 1-parameter call should be accepted here.
    hdr = make_tenant(client, "Biz KG", "kg_admin")
    _configure_api_mode(app, client, hdr, "biz-kg", template_forward_keepalive="customer_reply_alert")

    attempts = []

    def fake_post(url, json, headers, timeout):
        params = json["template"].get("components", [{}])[0].get("parameters", [])
        attempts.append([p["text"] for p in params])
        accepted = len(params) == 1
        return FakeResponse(ok=accepted, status_code=200 if accepted else 400,
                             text="" if accepted else "param count mismatch")

    monkeypatch.setattr(appmod.requests, "post", fake_post)

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(slug="biz-kg").first()
        appmod.send_daily_whatsapp_keepalive(tenant.id)

        # 3-param, then 2-param, then 1-param (accepted) -- stops there.
        assert len(attempts) == 3
        assert attempts[-1] == ["daily ping"]

        settings = appmod.WhatsAppSettings.query.filter_by(tenant_id=tenant.id).first()
        assert settings.last_forwarding_keepalive_sent_at is not None


def test_keepalive_does_not_resend_same_day(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz KB", "kb_admin")
    _configure_api_mode(app, client, hdr, "biz-kb")

    calls = []
    monkeypatch.setattr(appmod.requests, "post",
                         lambda url, json, headers, timeout: (calls.append(1), FakeResponse())[1])

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(slug="biz-kb").first()
        settings = appmod.WhatsAppSettings.query.filter_by(tenant_id=tenant.id).first()
        settings.last_forwarding_keepalive_sent_at = datetime.utcnow()
        appmod.db.session.commit()

        appmod.send_daily_whatsapp_keepalive(tenant.id)

        assert calls == []  # already sent today -- must not resend


def test_keepalive_resends_after_a_day_has_passed(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz KC", "kc_admin")
    _configure_api_mode(app, client, hdr, "biz-kc")

    calls = []
    monkeypatch.setattr(appmod.requests, "post",
                         lambda url, json, headers, timeout: (calls.append(1), FakeResponse())[1])

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(slug="biz-kc").first()
        settings = appmod.WhatsAppSettings.query.filter_by(tenant_id=tenant.id).first()
        settings.last_forwarding_keepalive_sent_at = datetime.utcnow() - timedelta(days=1, hours=1)
        appmod.db.session.commit()

        appmod.send_daily_whatsapp_keepalive(tenant.id)

        assert len(calls) == 1


def test_keepalive_skipped_without_forwarding_mobile(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz KD", "kd_admin")
    _configure_api_mode(app, client, hdr, "biz-kd", forwarding_mobile="")

    calls = []
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: (calls.append(1), FakeResponse())[1])

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(slug="biz-kd").first()
        appmod.send_daily_whatsapp_keepalive(tenant.id)

        assert calls == []


def test_keepalive_skipped_when_not_enabled(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz KE", "ke_admin")
    _configure_api_mode(app, client, hdr, "biz-ke", enabled=False)

    calls = []
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: (calls.append(1), FakeResponse())[1])

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(slug="biz-ke").first()
        appmod.send_daily_whatsapp_keepalive(tenant.id)

        assert calls == []


def test_whatsapp_settings_save_persists_template_forward_keepalive(client):
    hdr = make_tenant(client, "Biz KF", "kf_admin")

    client.post("/api/whatsapp-settings", headers=hdr,
                json={"enabled": True, "mode": "deeplink",
                      "template_forward_keepalive": "custom_daily_ping"})

    r = client.get("/api/whatsapp-settings", headers=hdr)
    assert r.get_json()["settings"]["template_forward_keepalive"] == "custom_daily_ping"
