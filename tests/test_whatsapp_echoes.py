"""Coexistence: messages the tenant sends from the WhatsApp Business app on
their phone (smb_message_echoes) appear in the Inbox as sender='phone' and
never trigger the auto-reply / AI / ticket path; history sync is ignored.
See specs/001-whatsapp-auto-connect/contracts/webhook-events.md section 1."""
import copy
import json
import os

import pytest

import app as appmod
import whatsapp_inbox
from config import Config
from tests.inbox_helpers import setup_wa_tenant, signed_post

PLATFORM_SECRET = "platform-app-secret"
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PNID = "PN_TEST_1"


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        data = json.load(f)
    # Fixtures use a placeholder phone_number_id; point them at this test's number.
    for entry in data["entry"]:
        for change in entry["changes"]:
            meta = change["value"].get("metadata")
            if meta:
                meta["phone_number_id"] = PNID
    return data


@pytest.fixture
def coex_tenant(app, client, monkeypatch):
    monkeypatch.setattr(Config, "META_APP_ID", "PLATFORM_APP")
    monkeypatch.setattr(Config, "META_APP_SECRET", PLATFORM_SECRET)
    monkeypatch.setattr(whatsapp_inbox, "SYNC_BACKGROUND_TASKS", True, raising=False)
    sent = []
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: sent.append((a, k)))
    _, tid = setup_wa_tenant(app, client, "Biz Echo", "echo_admin", PNID)
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.connection_method = "coexistence"
        s.connection_status = "connected"
        s.app_secret = None
        s.auto_reply_enabled = True       # even with auto-reply on, echoes must not trigger it
        appmod.db.session.commit()
    return tid, sent


def _messages(app, tid):
    with app.app_context():
        return [(m.direction, m.sender, m.msg_type, m.text, m.status, m.wa_message_id, m.wa_media_id)
                for m in appmod.WhatsAppMessage.query.filter_by(tenant_id=tid)
                .order_by(appmod.WhatsAppMessage.id)]


def test_text_echo_recorded_as_phone_message(app, client, coex_tenant):
    tid, sent = coex_tenant
    r = signed_post(client, fixture("meta_smb_message_echoes_text.json"), PLATFORM_SECRET)
    assert r.status_code == 200
    assert _messages(app, tid) == [("out", "phone", "text", "Hello from the phone", "sent",
                                    "wamid.ECHO_TEXT_1", None)]
    with app.app_context():
        conv = appmod.WhatsAppConversation.query.filter_by(tenant_id=tid).one()
        assert conv.wa_phone == "96171111111"
        assert conv.ai_paused is True
        assert conv.last_message_preview == "Hello from the phone"
        assert appmod.SupportTicket.query.filter_by(tenant_id=tid).count() == 0
    assert sent == []      # no auto-reply / AI send to Meta


def test_duplicate_echo_ignored(app, client, coex_tenant):
    tid, _ = coex_tenant
    payload = fixture("meta_smb_message_echoes_text.json")
    signed_post(client, payload, PLATFORM_SECRET)
    assert signed_post(client, copy.deepcopy(payload), PLATFORM_SECRET).status_code == 200
    assert len(_messages(app, tid)) == 1


def test_edit_echo_updates_original(app, client, coex_tenant):
    tid, _ = coex_tenant
    signed_post(client, fixture("meta_smb_message_echoes_text.json"), PLATFORM_SECRET)
    signed_post(client, fixture("meta_smb_message_echoes_edit.json"), PLATFORM_SECRET)
    msgs = _messages(app, tid)
    assert len(msgs) == 1
    assert msgs[0][3] == "Hello (edited)" and msgs[0][4] == "edited"


def test_revoke_echo_marks_original(app, client, coex_tenant):
    tid, _ = coex_tenant
    signed_post(client, fixture("meta_smb_message_echoes_text.json"), PLATFORM_SECRET)
    signed_post(client, fixture("meta_smb_message_echoes_revoke.json"), PLATFORM_SECRET)
    msgs = _messages(app, tid)
    assert len(msgs) == 1 and msgs[0][4] == "revoked"
    assert msgs[0][3] == "Hello from the phone"   # text kept for the audit trail


def test_image_echo_stores_media_id(app, client, coex_tenant, monkeypatch):
    tid, _ = coex_tenant
    monkeypatch.setattr(whatsapp_inbox, "store_inbound_media", lambda *a, **k: None, raising=False)
    signed_post(client, fixture("meta_smb_message_echoes_image.json"), PLATFORM_SECRET)
    msgs = _messages(app, tid)
    assert msgs[0][:3] == ("out", "phone", "image") and msgs[0][6] == "MEDIA_1"
    assert msgs[0][3] == "pic"


def test_history_payload_ignored(app, client, coex_tenant):
    tid, sent = coex_tenant
    assert signed_post(client, fixture("meta_history.json"), PLATFORM_SECRET).status_code == 200
    assert _messages(app, tid) == []
    with app.app_context():
        assert appmod.WhatsAppConversation.query.filter_by(tenant_id=tid).count() == 0


def test_echo_requires_valid_signature(app, client, coex_tenant):
    tid, _ = coex_tenant
    assert signed_post(client, fixture("meta_smb_message_echoes_text.json"), "wrong").status_code == 401
    assert _messages(app, tid) == []
