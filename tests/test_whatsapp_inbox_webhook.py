# tests/test_whatsapp_inbox_webhook.py
import pytest

import app as appmod
import cs_agent_tools
import storage
import whatsapp_inbox as wi
from tests.inbox_helpers import signed_post, seed_customer, setup_wa_tenant, wa_payload, FakeResponse


@pytest.fixture
def env(app, client, monkeypatch, tmp_path):
    monkeypatch.setattr(wi, "SYNC_BACKGROUND_TASKS", True)
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    pushes, ai_calls = [], []
    monkeypatch.setattr(appmod, "send_push_notification", lambda payload, **kw: pushes.append(payload) or 1)
    monkeypatch.setattr(cs_agent_tools, "handle_whatsapp_cs_ai_reply",
                        lambda **kw: ai_calls.append(kw) or {"reply_text": "ok", "ai_source": "gemini", "gemini_configured": True})
    monkeypatch.setattr(cs_agent_tools, "handle_whatsapp_audio_transcription", lambda *a, **kw: "وين الانترنت")
    monkeypatch.setattr(cs_agent_tools, "download_meta_media", lambda *a, **kw: (b"\x89PNGfake", "image/png"))
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **kw: FakeResponse())
    hdr, tid = setup_wa_tenant(app, client, "Biz WH", "wh_admin", "PNID_WH")
    return {"hdr": hdr, "tid": tid, "pushes": pushes, "ai_calls": ai_calls}


def _send(client, *messages, statuses=None):
    return signed_post(client, wa_payload("PNID_WH", messages=list(messages), statuses=statuses), "s3cret")


def _conv(app, tid):
    return appmod.WhatsAppConversation.query.filter_by(tenant_id=tid).first()


def test_text_from_known_customer_is_stored_and_ai_runs(app, client, env):
    seed_customer(client, env["hdr"], "70123456", name="Rami")
    r = _send(client, {"from": "96170123456", "id": "wamid.T1", "type": "text", "text": {"body": "hello"}})
    assert r.status_code == 200
    with app.app_context():
        conv = _conv(app, env["tid"])
        assert conv.customer_id is not None and conv.contact_name == "Cust"
        msgs = appmod.WhatsAppMessage.query.filter_by(conversation_id=conv.id).all()
        assert [(m.direction, m.msg_type, m.text) for m in msgs] == [("in", "text", "hello")]
        assert conv.needs_attention is False
    assert len(env["ai_calls"]) == 1 and env["pushes"] == []


def test_duplicate_delivery_is_ignored_entirely(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    m = {"from": "96170123456", "id": "wamid.DUP", "type": "text", "text": {"body": "hello"}}
    _send(client, m)
    _send(client, m)
    with app.app_context():
        assert appmod.WhatsAppMessage.query.filter_by(wa_message_id="wamid.DUP").count() == 1
    assert len(env["ai_calls"]) == 1


def test_unknown_sender_flagged_and_pushed(app, client, env):
    _send(client, {"from": "96179000000", "id": "wamid.U1", "type": "text", "text": {"body": "hi"}})
    with app.app_context():
        conv = _conv(app, env["tid"])
        assert conv.needs_attention and conv.attention_reason == "unknown_sender"
    assert len(env["pushes"]) == 1 and env["pushes"][0]["body"].startswith("Unknown sender")


def test_image_flags_media_and_is_downloaded(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    _send(client, {"from": "96170123456", "id": "wamid.I1", "type": "image", "image": {"id": "MEDIA_I", "caption": "receipt"}})
    with app.app_context():
        conv = _conv(app, env["tid"])
        assert conv.attention_reason == "media_received"
        msg = appmod.WhatsAppMessage.query.filter_by(wa_message_id="wamid.I1").first()
        assert msg.media_status == "stored" and storage.read_bytes(msg.media_key) == b"\x89PNGfake"


def test_sticker_is_not_flagged(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    _send(client, {"from": "96170123456", "id": "wamid.S1", "type": "sticker", "sticker": {"id": "MEDIA_S"}})
    with app.app_context():
        assert _conv(app, env["tid"]).needs_attention is False


def test_voice_note_transcript_saved(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    _send(client, {"from": "96170123456", "id": "wamid.V1", "type": "audio", "audio": {"id": "MEDIA_V"}})
    with app.app_context():
        msg = appmod.WhatsAppMessage.query.filter_by(wa_message_id="wamid.V1").first()
        assert msg.msg_type == "audio" and msg.transcript == "وين الانترنت"
    assert env["ai_calls"][0]["is_voice"] is True


def test_paused_conversation_skips_ai_and_flags_awaiting_admin(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    with app.app_context():
        conv = wi.upsert_conversation(appmod, env["tid"], "96170123456")
        conv.ai_paused = True
        from datetime import datetime
        conv.last_admin_reply_at = datetime.utcnow()
        appmod.db.session.commit()
    _send(client, {"from": "96170123456", "id": "wamid.P1", "type": "text", "text": {"body": "still down"}})
    with app.app_context():
        assert _conv(app, env["tid"]).attention_reason == "awaiting_admin"
    assert env["ai_calls"] == [] and len(env["pushes"]) == 1


def test_ai_inactive_flags_and_keeps_ticket(app, client, env):
    seed_customer(client, env["hdr"], "70123456")
    with app.app_context():
        appmod.db.session.add(appmod.CSAgentSettings(tenant_id=env["tid"], is_active=False))
        appmod.db.session.commit()
    _send(client, {"from": "96170123456", "id": "wamid.X1", "type": "text", "text": {"body": "hi"}})
    with app.app_context():
        assert _conv(app, env["tid"]).attention_reason == "ai_inactive"
        assert appmod.SupportTicket.query.filter_by(tenant_id=env["tid"]).count() == 1
        system_msgs = appmod.WhatsAppMessage.query.filter_by(sender="system").all()
        assert len(system_msgs) == 1 and system_msgs[0].wa_message_id == "wamid.OUT1"
    assert env["ai_calls"] == []


def test_forwarding_mobile_messages_not_persisted(app, client, env):
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=env["tid"]).first()
        s.forwarding_mobile = "96176000000"
        appmod.db.session.commit()
    _send(client, {"from": "96176000000", "id": "wamid.F1", "type": "text", "text": {"body": "ok"}})
    with app.app_context():
        assert appmod.WhatsAppConversation.query.count() == 0


def test_status_callbacks_update_outbound(app, client, env):
    with app.app_context():
        conv = wi.upsert_conversation(appmod, env["tid"], "96170123456")
        wi.record_outbound(appmod, conv, sender="admin", msg_type="text", text="hi", wa_message_id="wamid.O9")
        appmod.db.session.commit()
    _send(client, statuses=[{"id": "wamid.O9", "status": "delivered", "recipient_id": "96170123456"}])
    with app.app_context():
        assert appmod.WhatsAppMessage.query.filter_by(wa_message_id="wamid.O9").first().status == "delivered"
    _send(client, statuses=[{"id": "wamid.O9", "status": "failed", "errors": [{"code": 131026, "title": "Undeliverable"}]}])
    with app.app_context():
        assert _conv(app, env["tid"]).attention_reason == "send_failed"
    assert len(env["pushes"]) == 1
