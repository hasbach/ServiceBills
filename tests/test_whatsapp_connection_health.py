"""Connection health for automatic WhatsApp connections: account_update /
quality webhooks, send-error detection, the hourly health job, refresh and
disconnect (US4, FR-019..FR-023). See
specs/001-whatsapp-auto-connect/contracts/webhook-events.md sections 2-3."""
import json
import os
from datetime import datetime, timedelta

import pytest

import app as appmod
import wa_connect
import whatsapp_inbox
from config import Config
from tests.inbox_helpers import setup_wa_tenant, signed_post

PLATFORM_SECRET = "platform-app-secret"
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
WABA = "WABA_TEST_1"
PNID = "PN_HEALTH"


class Resp:
    def __init__(self, body=None, status=200):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._b = body if body is not None else {}
        self.text = json.dumps(self._b)

    def json(self):
        return self._b


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, "send_push_notification",
                        lambda payload, tenant_id=None, roles=None, topic=None: sent.append((payload, tenant_id, roles)))
    return sent


@pytest.fixture
def auto_tenant(app, client, monkeypatch):
    monkeypatch.setattr(Config, "META_APP_ID", "PLATFORM_APP")
    monkeypatch.setattr(Config, "META_APP_SECRET", PLATFORM_SECRET)
    monkeypatch.setattr(Config, "META_ES_CONFIG_ID", "CFG1")
    hdr, tid = setup_wa_tenant(app, client, "Biz Health", "health_admin", PNID, token="BIZ_TOKEN")
    with app.app_context():
        appmod.db.session.get(appmod.Tenant, tid).plan = "pro"
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.connection_method = "coexistence"
        s.connection_status = "connected"
        s.app_secret = None
        s.business_account_id = WABA
        s.registration_pin = "123456"
        s.token_obtained_at = datetime.utcnow()
        appmod.db.session.commit()
    return hdr, tid


def _settings(app, tid):
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        appmod.db.session.expunge(s)
        return s


def _events(app, tid):
    with app.app_context():
        return [(e.event, e.source, e.to_status, e.detail) for e in appmod.WhatsAppConnectionEvent.query
                .filter_by(tenant_id=tid).order_by(appmod.WhatsAppConnectionEvent.id)]


# ---- account_update webhook --------------------------------------------------

def test_partner_app_uninstalled_needs_attention(app, client, auto_tenant, pushes):
    _, tid = auto_tenant
    assert signed_post(client, fixture("meta_account_update_partner_app_uninstalled.json"), PLATFORM_SECRET).status_code == 200
    s = _settings(app, tid)
    assert s.connection_status == "needs_attention"
    assert "removed" in s.status_reason.lower()
    assert _events(app, tid)[-1][:3] == ("status_change", "webhook", "needs_attention")
    assert len(pushes) == 1 and pushes[0][1] == tid and pushes[0][2] == ["admin"]


def test_partner_removed_primary_inactivity_reason(app, client, auto_tenant, pushes):
    _, tid = auto_tenant
    signed_post(client, fixture("meta_account_update_partner_removed.json"), PLATFORM_SECRET)
    s = _settings(app, tid)
    assert s.connection_status == "needs_attention"
    assert "WhatsApp Business app not opened for ~14 days" in s.status_reason


def test_offboarded_then_reconnected(app, client, auto_tenant, pushes):
    _, tid = auto_tenant
    signed_post(client, fixture("meta_account_update_offboarded.json"), PLATFORM_SECRET)
    assert _settings(app, tid).connection_status == "needs_attention"
    assert pushes == []          # may come back by itself within minutes
    signed_post(client, fixture("meta_account_update_reconnected.json"), PLATFORM_SECRET)
    assert _settings(app, tid).connection_status == "connected"
    assert _settings(app, tid).status_reason is None


def test_restriction_keeps_status_but_alerts(app, client, auto_tenant, pushes):
    _, tid = auto_tenant
    payload = fixture("meta_account_update_partner_removed.json")
    payload["entry"][0]["changes"][0]["value"] = {"event": "ACCOUNT_RESTRICTION", "waba_info": {"waba_id": WABA}}
    signed_post(client, payload, PLATFORM_SECRET)
    s = _settings(app, tid)
    assert s.connection_status == "connected" and s.status_reason
    assert len(pushes) == 1


def test_partner_added_is_ignored(app, client, auto_tenant, pushes):
    _, tid = auto_tenant
    payload = fixture("meta_account_update_partner_removed.json")
    payload["entry"][0]["changes"][0]["value"] = {"event": "PARTNER_ADDED", "waba_info": {"waba_id": WABA}}
    signed_post(client, payload, PLATFORM_SECRET)
    assert _settings(app, tid).connection_status == "connected" and pushes == []


def test_account_update_falls_back_to_entry_id(app, client, auto_tenant, pushes):
    _, tid = auto_tenant
    payload = fixture("meta_account_update_partner_app_uninstalled.json")
    payload["entry"][0]["changes"][0]["value"].pop("waba_info")
    signed_post(client, payload, PLATFORM_SECRET)
    assert _settings(app, tid).connection_status == "needs_attention"


def test_account_update_bad_signature_rejected(app, client, auto_tenant, pushes):
    _, tid = auto_tenant
    r = signed_post(client, fixture("meta_account_update_partner_app_uninstalled.json"), "wrong")
    assert r.status_code == 401
    assert _settings(app, tid).connection_status == "connected"


def test_quality_update(app, client, auto_tenant, pushes):
    _, tid = auto_tenant
    signed_post(client, fixture("meta_phone_number_quality_update.json"), PLATFORM_SECRET)
    s = _settings(app, tid)
    assert s.messaging_limit_tier == "TIER_250" and s.quality_rating == "RED"
    assert s.connection_status == "connected"
    assert len(pushes) == 1


# ---- send errors -----------------------------------------------------------

def _customer(app, tid):
    with app.app_context():
        c = appmod.Customer(tenant_id=tid, name="Ali", phone="96170123456", balance=5,
                            whatsapp_notifications_enabled=True)
        return c


def test_send_auth_error_marks_needs_attention(app, client, auto_tenant, pushes, monkeypatch):
    _, tid = auto_tenant
    monkeypatch.setattr(appmod, "get_meta_template_definition", lambda s, n: None)
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: Resp(
        {"error": {"message": "Error validating access token", "code": 190}}, 401))
    with app.app_context():
        res = appmod.send_whatsapp_message(_customer(app, tid), "payment_paid", {"amount": 1})
    assert res["success"] is False
    s = _settings(app, tid)
    assert s.connection_status == "needs_attention"
    assert _events(app, tid)[-1][1] == "send_error"


def test_needs_attention_blocks_send_without_graph_call(app, client, auto_tenant, monkeypatch):
    _, tid = auto_tenant
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.connection_status = "needs_attention"
        appmod.db.session.commit()
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: pytest.fail("no Graph call expected"))
    with app.app_context():
        res = appmod.send_whatsapp_message(_customer(app, tid), "payment_paid", {"amount": 1})
    assert res["success"] is False
    assert "needs attention" in res["error"]


def test_manual_tenant_not_blocked_but_detected(app, client, pushes, monkeypatch):
    _, tid = setup_wa_tenant(app, client, "Biz MH", "mh_admin", "PN_MH")
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.connection_status = "needs_attention"
        appmod.db.session.commit()
    calls = []
    monkeypatch.setattr(appmod, "get_meta_template_definition", lambda s, n: None)
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: calls.append(1) or Resp({"messages": [{"id": "w"}]}))
    with app.app_context():
        res = appmod.send_whatsapp_message(_customer(app, tid), "payment_paid", {"amount": 1})
    assert res["success"] is True and calls == [1]


def test_inbox_send_blocked_when_needs_attention(app, client, auto_tenant, monkeypatch):
    _, tid = auto_tenant
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.connection_status = "needs_attention"
        conv = appmod.WhatsAppConversation(tenant_id=tid, wa_phone="96170123456", unread_count=0,
                                           needs_attention=False, ai_paused=False,
                                           last_inbound_at=datetime.utcnow(), last_message_at=datetime.utcnow())
        appmod.db.session.add(conv)
        appmod.db.session.commit()
        monkeypatch.setattr(whatsapp_inbox.requests, "post", lambda *a, **k: pytest.fail("no Graph call expected"))
        with pytest.raises(whatsapp_inbox.SendError) as exc:
            whatsapp_inbox.send_admin_message(appmod, conv, None, "text", text="hi")
        assert exc.value.code == "needs_attention"


def test_inbox_send_auth_error_detected(app, client, auto_tenant, pushes, monkeypatch):
    _, tid = auto_tenant
    with app.app_context():
        conv = appmod.WhatsAppConversation(tenant_id=tid, wa_phone="96170123456", unread_count=0,
                                           needs_attention=False, ai_paused=False,
                                           last_inbound_at=datetime.utcnow(), last_message_at=datetime.utcnow())
        appmod.db.session.add(conv)
        appmod.db.session.commit()
        monkeypatch.setattr(whatsapp_inbox.requests, "post", lambda *a, **k: Resp(
            {"error": {"message": "token invalid", "code": 190}}, 401))
        with pytest.raises(whatsapp_inbox.SendError):
            whatsapp_inbox.send_admin_message(appmod, conv, None, "text", text="hi")
    assert _settings(app, tid).connection_status == "needs_attention"


# ---- health job ----------------------------------------------------------

def test_health_job_marks_failure_and_recovery(app, client, auto_tenant, pushes, monkeypatch):
    _, tid = auto_tenant
    monkeypatch.setattr(wa_connect.requests, "get", lambda *a, **k: Resp(
        {"error": {"message": "Unsupported get request", "code": 100}}, 400))
    appmod.whatsapp_connection_health_with_context()
    s = _settings(app, tid)
    assert s.connection_status == "needs_attention" and s.last_health_check_at
    assert _events(app, tid)[-1][1] == "health_check"

    monkeypatch.setattr(wa_connect.requests, "get", lambda *a, **k: Resp(
        {"display_phone_number": "+961 70 1", "verified_name": "DN", "quality_rating": "YELLOW"}))
    appmod.whatsapp_connection_health_with_context()
    s = _settings(app, tid)
    assert s.connection_status == "connected" and s.quality_rating == "YELLOW"


def test_health_job_alerts_stuck_offboarded(app, client, auto_tenant, pushes, monkeypatch):
    _, tid = auto_tenant
    signed_post(client, fixture("meta_account_update_offboarded.json"), PLATFORM_SECRET)
    with app.app_context():
        ev = appmod.WhatsAppConnectionEvent.query.filter_by(tenant_id=tid, to_status="needs_attention").first()
        ev.created_at = datetime.utcnow() - timedelta(minutes=20)
        appmod.db.session.commit()
    # Still offboarded: Meta keeps refusing the number.
    monkeypatch.setattr(wa_connect.requests, "get", lambda *a, **k: Resp(
        {"error": {"message": "offboarded", "code": 100}}, 400))
    appmod.whatsapp_connection_health_with_context()
    assert len(pushes) == 1


def test_health_job_skips_manual_and_disconnected(app, client, auto_tenant, monkeypatch):
    setup_wa_tenant(app, client, "Biz HM", "hm_admin", "PN_HM")
    _, tid = auto_tenant
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.connection_status = "disconnected"
        appmod.db.session.commit()
    monkeypatch.setattr(wa_connect.requests, "get", lambda *a, **k: pytest.fail("no Graph call expected"))
    appmod.whatsapp_connection_health_with_context()


def test_health_job_registered():
    assert appmod._SCHEDULED_JOBS["whatsapp_connection_health"] is appmod.whatsapp_connection_health_with_context
    assert appmod.SCHEDULE["whatsapp_connection_health"] == "5 * * * *"
    assert "whatsapp_connection_health" not in appmod.onprem_schedule()


# ---- refresh / disconnect routes -------------------------------------------

def test_refresh_route(app, client, auto_tenant, monkeypatch):
    hdr, tid = auto_tenant
    monkeypatch.setattr(wa_connect.requests, "get", lambda *a, **k: Resp(
        {"display_phone_number": "+961 70 2", "quality_rating": "GREEN"}))
    r = client.post("/api/whatsapp/connect/refresh", headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["connection"]["display_phone_number"] == "+961 70 2"


def test_disconnect_route(app, client, auto_tenant, monkeypatch):
    hdr, tid = auto_tenant
    with app.app_context():
        conv = appmod.WhatsAppConversation(tenant_id=tid, wa_phone="1", unread_count=0, needs_attention=False,
                                           ai_paused=False, last_message_at=datetime.utcnow())
        appmod.db.session.add(conv)
        appmod.db.session.flush()
        appmod.db.session.add(appmod.WhatsAppMessage(tenant_id=tid, conversation_id=conv.id, direction="in",
                                                     sender="customer", msg_type="text", text="hi", status="received"))
        appmod.db.session.commit()
    calls = []
    monkeypatch.setattr(wa_connect.requests, "delete", lambda url, **k: calls.append(url) or Resp({"success": True}))
    monkeypatch.setattr(wa_connect.requests, "post", lambda url, **k: pytest.fail("never deregister"))
    assert client.post("/api/whatsapp/connect/disconnect", headers=hdr, json={}).status_code == 400
    r = client.post("/api/whatsapp/connect/disconnect", headers=hdr, json={"confirm": True})
    assert r.status_code == 200
    assert r.get_json()["connection"]["status"] == "disconnected"
    assert calls and calls[0].endswith(f"/{WABA}/subscribed_apps")
    s = _settings(app, tid)
    assert s.access_token is None and s.registration_pin is None and s.token_obtained_at is None
    assert s.mode == "deeplink" and s.connection_status == "disconnected"
    assert s.connection_method == "coexistence"       # remembered until the next connect
    with app.app_context():
        assert appmod.WhatsAppMessage.query.filter_by(tenant_id=tid).count() == 1
    ev = _events(app, tid)[-1]
    assert ev[0] == "disconnect" and ev[1] == "user"


def test_disconnect_tolerates_meta_failure(app, client, auto_tenant, monkeypatch):
    hdr, tid = auto_tenant
    monkeypatch.setattr(wa_connect.requests, "delete", lambda url, **k: Resp({"error": {"message": "x"}}, 400))
    r = client.post("/api/whatsapp/connect/disconnect", headers=hdr, json={"confirm": True})
    assert r.status_code == 200 and _settings(app, tid).connection_status == "disconnected"


def test_disconnect_to_manual_then_credentials_save(app, client, auto_tenant, monkeypatch):
    hdr, tid = auto_tenant
    monkeypatch.setattr(wa_connect.requests, "delete", lambda url, **k: Resp({"success": True}))
    r = client.post("/api/whatsapp/connect/disconnect", headers=hdr,
                    json={"confirm": True, "next_method": "manual"})
    assert r.status_code == 200
    assert _settings(app, tid).connection_method == "manual"
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: Resp({"success": True}))
    client.post("/api/whatsapp-settings", headers=hdr, json={
        "mode": "api", "phone_number_id": "PN_MANUAL", "access_token": "own-token", "app_secret": "own-secret"})
    s = _settings(app, tid)
    assert s.phone_number_id == "PN_MANUAL" and s.access_token == "own-token"
    assert s.connection_status == "connected"


def test_disconnect_manual_tenant(app, client, monkeypatch):
    monkeypatch.setattr(Config, "META_APP_ID", None)   # still allowed without the platform app
    hdr, tid = setup_wa_tenant(app, client, "Biz DM", "dm_admin", "PN_DM", secret="sec", token="tok")
    with app.app_context():
        appmod.db.session.get(appmod.Tenant, tid).plan = "pro"
        appmod.db.session.commit()
    monkeypatch.setattr(wa_connect.requests, "delete", lambda url, **k: Resp({"success": True}))
    r = client.post("/api/whatsapp/connect/disconnect", headers=hdr, json={"confirm": True})
    assert r.status_code == 200
    s = _settings(app, tid)
    assert s.access_token is None and s.app_secret is None and s.mode == "deeplink"
