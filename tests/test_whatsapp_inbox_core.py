from datetime import datetime, timedelta

import app as appmod
import whatsapp_inbox as wi
from tests.conftest import make_tenant


def _tid(username):
    return appmod.User.query.filter_by(username=username).first().tenant_id


def test_parse_inbound_every_type():
    ts = "1758600000"
    assert wi.parse_inbound({"id": "w1", "type": "text", "timestamp": ts, "text": {"body": "hi 👋"}})["text"] == "hi 👋"
    audio = wi.parse_inbound({"id": "w2", "type": "audio", "audio": {"id": "M1", "mime_type": "audio/ogg; codecs=opus"}})
    assert audio["msg_type"] == "audio" and audio["wa_media_id"] == "M1"
    voice = wi.parse_inbound({"id": "w2b", "type": "voice", "voice": {"id": "M1b"}})
    assert voice["msg_type"] == "audio" and voice["wa_media_id"] == "M1b"
    img = wi.parse_inbound({"id": "w3", "type": "image", "image": {"id": "M2", "caption": "receipt"}})
    assert img["msg_type"] == "image" and img["text"] == "receipt"
    st = wi.parse_inbound({"id": "w4", "type": "sticker", "sticker": {"id": "M3", "mime_type": "image/webp"}})
    assert st["msg_type"] == "sticker" and st["wa_media_id"] == "M3"
    rx = wi.parse_inbound({"id": "w5", "type": "reaction", "reaction": {"message_id": "wOUT", "emoji": "👍"}})
    assert rx["reaction_emoji"] == "👍" and rx["reaction_target_wa_id"] == "wOUT"
    loc = wi.parse_inbound({"id": "w6", "type": "location", "location": {"latitude": 33.9, "longitude": 35.5, "name": "Office"}})
    assert loc["text"] == "33.9,35.5 Office"
    inter = wi.parse_inbound({"id": "w7", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"title": "Yes"}}})
    assert inter["text"] == "Yes"
    ctx = wi.parse_inbound({"id": "w8", "type": "text", "text": {"body": "x"}, "context": {"id": "wPREV"}})
    assert ctx["reply_to_wa_message_id"] == "wPREV"
    assert wi.parse_inbound({"id": "w9", "type": "ephemeral"})["msg_type"] == "unsupported"
    assert wi.parse_inbound({"id": "w1", "type": "text", "timestamp": ts, "text": {"body": "x"}})["created_at"] == datetime(2025, 9, 23, 4, 0)


def test_preview_for():
    assert wi.preview_for("text", "hello") == "hello"
    assert wi.preview_for("audio") == "🎤 Voice note"
    assert wi.preview_for("image", "receipt") == "📷 Photo: receipt"
    assert wi.preview_for("reaction", reaction_emoji="❤️") == "Reacted ❤️"
    assert wi.preview_for("reaction", reaction_emoji="") == "Removed reaction"


def test_inbound_attention_reason_priority():
    r = wi.inbound_attention_reason
    assert r(ai_paused=True, ai_will_run=False, has_customer=False, msg_type="image") == "awaiting_admin"
    assert r(ai_paused=False, ai_will_run=False, has_customer=True, msg_type="text") == "ai_inactive"
    assert r(ai_paused=False, ai_will_run=True, has_customer=False, msg_type="image") == "media_received"
    assert r(ai_paused=False, ai_will_run=True, has_customer=False, msg_type="text") == "unknown_sender"
    assert r(ai_paused=False, ai_will_run=True, has_customer=True, msg_type="sticker") is None
    assert r(ai_paused=False, ai_will_run=True, has_customer=True, msg_type="reaction") is None


def test_upsert_record_and_dedupe(app, client):
    make_tenant(client, "Biz Core", "core_admin")
    with app.app_context():
        tid = _tid("core_admin")
        conv = wi.upsert_conversation(appmod, tid, "+961 70 123 456", "Rami")
        parsed = wi.parse_inbound({"id": "wamid.A", "type": "text", "text": {"body": "hello"}})
        msg = wi.record_inbound(appmod, tid, conv, parsed)
        appmod.db.session.commit()
        assert conv.wa_phone == "96170123456" and conv.contact_name == "Rami"
        assert conv.unread_count == 1 and conv.last_message_preview == "hello"
        assert conv.last_inbound_at is not None
        assert msg.sender == "customer" and msg.status == "received" and msg.media_status == "none"
        assert wi.is_duplicate(appmod, tid, "wamid.A") is True
        assert wi.is_duplicate(appmod, tid, "wamid.B") is False
        again = wi.upsert_conversation(appmod, tid, "96170123456")
        assert again.id == conv.id
        assert wi.find_conversation_by_phone(appmod, tid, "70123456").id == conv.id


def test_media_message_starts_pending(app, client):
    make_tenant(client, "Biz Media", "media_admin")
    with app.app_context():
        tid = _tid("media_admin")
        conv = wi.upsert_conversation(appmod, tid, "96171000000")
        msg = wi.record_inbound(appmod, tid, conv, wi.parse_inbound(
            {"id": "wamid.M", "type": "sticker", "sticker": {"id": "MEDIA1"}}))
        assert msg.media_status == "pending" and msg.wa_media_id == "MEDIA1"


def test_flag_and_clear_attention(app, client):
    make_tenant(client, "Biz Flag", "flag_admin")
    with app.app_context():
        conv = wi.upsert_conversation(appmod, _tid("flag_admin"), "96171111111")
        wi.flag_attention(conv, "ai_failed")
        first_since = conv.attention_since
        wi.flag_attention(conv, "escalated")
        assert conv.needs_attention and conv.attention_reason == "escalated"
        assert conv.attention_since == first_since
        wi.clear_attention(conv)
        assert not conv.needs_attention and conv.attention_reason is None and conv.attention_since is None


def test_window_and_auto_resume(app, client):
    make_tenant(client, "Biz Win", "win_admin")
    with app.app_context():
        conv = wi.upsert_conversation(appmod, _tid("win_admin"), "96172222222")
        now = datetime(2026, 9, 23, 12, 0)
        conv.last_inbound_at = now - timedelta(hours=23)
        assert wi.window_open(conv, now) is True
        conv.last_inbound_at = now - timedelta(hours=25)
        assert wi.window_open(conv, now) is False
        conv.ai_paused = True
        conv.last_admin_reply_at = now - timedelta(hours=2)
        assert wi.maybe_auto_resume(conv, now) is False and conv.ai_paused
        conv.last_admin_reply_at = now - timedelta(hours=25)
        assert wi.maybe_auto_resume(conv, now) is True and not conv.ai_paused


def test_apply_status_advances_and_never_regresses(app, client):
    make_tenant(client, "Biz St", "st_admin")
    with app.app_context():
        tid = _tid("st_admin")
        conv = wi.upsert_conversation(appmod, tid, "96173333333")
        out = wi.record_outbound(appmod, conv, sender="admin", msg_type="text", text="hi", wa_message_id="wamid.O")
        appmod.db.session.commit()
        wi.apply_status(appmod, tid, {"id": "wamid.O", "status": "read"})
        assert out.status == "read"
        wi.apply_status(appmod, tid, {"id": "wamid.O", "status": "delivered"})
        assert out.status == "read"
        m, flagged = wi.apply_status(appmod, tid, {"id": "wamid.O", "status": "failed",
                                                  "errors": [{"code": 131047, "title": "Re-engagement message"}]})
        assert out.status == "failed" and out.error_code == "131047"
        assert flagged is conv and conv.attention_reason == "send_failed"
        assert wi.apply_status(appmod, tid, {"id": "wamid.UNKNOWN", "status": "read"}) == (None, None)


def test_run_background_sync_mode(monkeypatch):
    monkeypatch.setattr(wi, "SYNC_BACKGROUND_TASKS", True)
    calls = []
    wi.run_background(None, lambda: calls.append(1))
    assert calls == [1]
