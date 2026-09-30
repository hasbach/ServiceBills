from datetime import datetime, timedelta
import hashlib
import hmac
import json

import app as appmod
import whatsapp_inbox as wi
from tests.conftest import make_tenant
from tests.test_module_gating_api import _set_overrides


def test_whatsapp_templates_blocked(app, client):
    hdr = make_tenant(client, "Wa Off", "waoff_admin")
    _set_overrides(app, "wa-off", {"whatsapp": False})
    r = client.get("/api/whatsapp/templates", headers=hdr)
    assert r.status_code == 403 and r.get_json()["module"] == "whatsapp"


def test_whatsapp_templates_open_on_free(app, client):
    hdr = make_tenant(client, "Wa On", "waon_admin")
    assert client.get("/api/whatsapp/templates", headers=hdr).status_code == 200


def test_inbox_blocked(app, client):
    hdr = make_tenant(client, "Inbox Off", "inboxoff_admin")
    _set_overrides(app, "inbox-off", {"whatsapp": False})
    r = client.get("/api/whatsapp/inbox/summary", headers=hdr)
    assert r.status_code == 403 and r.get_json()["module"] == "whatsapp"


def test_deeplink_settings_not_gated_but_reports_disabled(app, client):
    hdr = make_tenant(client, "Dl Off", "dloff_admin")
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="dl-off").first().id
        appmod.db.session.add(appmod.WhatsAppSettings(tenant_id=tid, enabled=True, mode="deeplink"))
        appmod.db.session.commit()
    r = client.get("/api/whatsapp-settings/deeplink", headers=hdr)
    assert r.status_code == 200 and r.get_json()["settings"]["enabled"] is True
    _set_overrides(app, "dl-off", {"whatsapp": False})
    r = client.get("/api/whatsapp-settings/deeplink", headers=hdr)
    assert r.status_code == 200 and r.get_json()["settings"]["enabled"] is False


def test_cs_agent_config_needs_ai_cs(app, client):
    hdr = make_tenant(client, "Ai Off", "aioff_admin")   # free: no ai_cs
    r = client.get("/api/cs-agent/config", headers=hdr)
    assert r.status_code == 403 and r.get_json()["module"] == "ai_cs"
    _set_overrides(app, "ai-off", {"ai_cs": True})
    assert client.get("/api/cs-agent/config", headers=hdr).status_code == 200


def test_network_diagnostic_tool_unavailable_without_network(app, client):
    hdr = make_tenant(client, "Ai Net", "ainet_admin")
    _set_overrides(app, "ai-net", {"ai_cs": True, "network": False})
    r = client.post("/api/cs-agent/tools/network-diagnostic", headers=hdr, json={"phone": "1"})
    assert r.status_code == 200 and r.get_json()["available"] is False


def test_payment_link_tool_unavailable_without_whish(app, client):
    hdr = make_tenant(client, "Ai Pay", "aipay_admin")
    _set_overrides(app, "ai-pay", {"ai_cs": True, "whish_payments": False})
    r = client.post("/api/cs-agent/tools/send-payment-link", headers=hdr, json={"customer_id": 1})
    assert r.status_code == 200 and r.get_json()["available"] is False


def _signed_post(client, payload, secret):
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/whatsapp/webhook", data=body, content_type="application/json",
                       headers={"X-Hub-Signature-256": f"sha256={sig}"})


def _webhook_setup(app, client, name, user, slug, pnid, overrides):
    make_tenant(client, name, user)
    _set_overrides(app, slug, overrides)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug=slug).first().id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=tid, phone_number_id=pnid, enabled=True, mode="api", app_secret="sekret"))
        appmod.db.session.commit()
    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": pnid},
        "contacts": [{"profile": {"name": "Cust"}}],
        "messages": [{"from": "96170123456", "id": "wamid.X1", "type": "text", "text": {"body": "hi"}}],
    }}]}]}
    return tid, payload


def test_webhook_skips_tenant_without_whatsapp(app, client):
    tid, payload = _webhook_setup(app, client, "Wh Off", "whoff_admin", "wh-off", "PN_OFF",
                                  {"whatsapp": False})
    r = _signed_post(client, payload, "sekret")
    assert r.status_code == 200
    with app.app_context():
        assert appmod.WhatsAppConversation.query.filter_by(tenant_id=tid).count() == 0
        assert appmod.WhatsAppMessage.query.filter_by(tenant_id=tid).count() == 0


def test_webhook_without_ai_cs_still_stores_inbox_no_ai(app, client, monkeypatch):
    monkeypatch.setattr(wi, "SYNC_BACKGROUND_TASKS", True)
    calls = []
    monkeypatch.setattr(appmod.cs_agent_tools, "handle_whatsapp_cs_ai_reply",
                        lambda *a, **k: calls.append(1))
    tid, payload = _webhook_setup(app, client, "Wh Noai", "whnoai_admin", "wh-noai", "PN_NOAI", {})
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.access_token = "tok"
        appmod.db.session.commit()
    r = _signed_post(client, payload, "sekret")
    assert r.status_code == 200
    with app.app_context():
        assert appmod.WhatsAppMessage.query.filter_by(tenant_id=tid).count() == 1
    assert calls == []


def test_webhook_with_ai_cs_runs_ai(app, client, monkeypatch):
    monkeypatch.setattr(wi, "SYNC_BACKGROUND_TASKS", True)
    calls = []
    monkeypatch.setattr(appmod.cs_agent_tools, "handle_whatsapp_cs_ai_reply",
                        lambda *a, **k: calls.append(1))
    # AI needs access_token + phone_number_id on settings
    tid, payload = _webhook_setup(app, client, "Wh Ai", "whai_admin", "wh-ai", "PN_AI", {"ai_cs": True})
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.access_token = "tok"
        appmod.db.session.commit()
    r = _signed_post(client, payload, "sekret")
    assert r.status_code == 200
    assert calls == [1]


def test_send_whatsapp_message_skipped_when_module_off(app, client, monkeypatch):
    make_tenant(client, "Wa Send", "wasend_admin")
    _set_overrides(app, "wa-send", {"whatsapp": False})
    calls = []
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: calls.append(a) or None)
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug="wa-send").first()
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=t.id, enabled=True, mode="api", access_token="x", phone_number_id="1"))
        plan = appmod.SubscriptionPlan(tenant_id=t.id, name="P", price=10.0, billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        c = appmod.Customer(tenant_id=t.id, name="C", phone="70111222", address="a", subscription_plan_id=plan.id,
                               subscription_expiry_date=datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(c)
        appmod.db.session.commit()
        res = appmod.send_whatsapp_message(c, "payment_paid", context={"amount": 1})
    assert res == {'success': False, 'status': 'Skipped', 'error': 'whatsapp module disabled'}
    assert calls == []
