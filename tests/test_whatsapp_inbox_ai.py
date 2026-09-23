from unittest.mock import MagicMock

import app as appmod
import cs_agent_tools
import whatsapp_inbox as wi
from tests.conftest import make_tenant
from tests.inbox_helpers import FakeResponse, seed_customer


def _tenant(app, client, name, user):
    hdr = make_tenant(client, name, user)
    with app.app_context():
        return hdr, appmod.User.query.filter_by(username=user).first().tenant_id


def _settings():
    s = MagicMock()
    s.access_token = "tok"; s.phone_number_id = "PN"; s.api_version = "v19.0"
    return s


def test_ai_reply_is_recorded_as_ai_message(app, client, monkeypatch):
    hdr, tid = _tenant(app, client, "Biz AI", "ai_admin")
    seed_customer(client, hdr, "70777888", name="Salim")
    monkeypatch.setattr(cs_agent_tools.requests, "post", lambda *a, **kw: FakeResponse(body={"messages": [{"id": "wamid.AI1"}]}))
    with app.app_context():
        cust = appmod.Customer.query.filter_by(tenant_id=tid).first()
        wi.upsert_conversation(appmod, tid, "96170777888"); appmod.db.session.commit()
        res = cs_agent_tools.handle_whatsapp_cs_ai_reply(appmod, tid, "96170777888", cust, "رصيدي", settings=_settings())
        assert res["ai_source"] == "rules" and res["gemini_configured"] is False and res["inbox_send_failed"] is False
        ai = appmod.WhatsAppMessage.query.filter_by(sender="ai").all()
        assert len(ai) == 1 and ai[0].wa_message_id == "wamid.AI1" and ai[0].text == res["reply_text"]


def test_ai_text_send_failure_flags_send_failed(app, client, monkeypatch):
    hdr, tid = _tenant(app, client, "Biz AIF", "aif_admin")
    seed_customer(client, hdr, "70777889")
    monkeypatch.setattr(cs_agent_tools.requests, "post",
                        lambda *a, **kw: FakeResponse(ok=False, status_code=400, body={"error": {"code": 131047, "message": "Re-engagement"}}))
    with app.app_context():
        cust = appmod.Customer.query.filter_by(tenant_id=tid).first()
        res = cs_agent_tools.handle_whatsapp_cs_ai_reply(appmod, tid, "96170777889", cust, "hi", settings=_settings())
        assert res["inbox_send_failed"] is True
        conv = wi.find_conversation_by_phone(appmod, tid, "96170777889")
        assert conv.attention_reason == "send_failed"
        failed = appmod.WhatsAppMessage.query.filter_by(sender="ai").first()
        assert failed.status == "failed" and failed.error_code == "131047"


def test_after_ai_reply_flags(app, client, monkeypatch):
    _, tid = _tenant(app, client, "Biz After", "after_admin")
    pushes = []
    monkeypatch.setattr(appmod, "send_push_notification", lambda p, **kw: pushes.append(p) or 1)
    with app.app_context():
        conv = wi.upsert_conversation(appmod, tid, "96170100200"); appmod.db.session.commit()
        wi.after_ai_reply(appmod, tid, "96170100200", {"reply_text": "x", "ai_source": "gemini", "gemini_configured": True})
        assert conv.needs_attention is False
        wi.after_ai_reply(appmod, tid, "96170100200", {"reply_text": "x", "ai_source": "rules", "gemini_configured": False})
        assert conv.needs_attention is False  # tenant without Gemini relies on rules by design
        wi.after_ai_reply(appmod, tid, "96170100200", {"reply_text": "x", "ai_source": "rules", "gemini_configured": True})
        assert conv.attention_reason == "ai_failed"
        wi.clear_attention(conv); appmod.db.session.commit()
        wi.after_ai_reply(appmod, tid, "96170100200", None)
        assert conv.attention_reason == "ai_failed"
        wi.clear_attention(conv); appmod.db.session.commit()
        wi.after_ai_reply(appmod, tid, "96170100200", {"reply_text": "x", "ai_source": "rules", "gemini_configured": False, "escalate": True})
        assert conv.attention_reason == "escalated"
    assert len(pushes) >= 1


def test_escalate_to_human_flags_existing_conversation(app, client, monkeypatch):
    hdr, tid = _tenant(app, client, "Biz Esc", "esc_admin")
    seed_customer(client, hdr, "70555666")
    monkeypatch.setattr(appmod, "send_push_notification", lambda p, **kw: 1)
    with app.app_context():
        conv = wi.upsert_conversation(appmod, tid, "96170555666"); appmod.db.session.commit()
        cust = appmod.Customer.query.filter_by(tenant_id=tid).first()
        cs_agent_tools.escalate_to_human(appmod, tid, cust.id, "need human", "summary", phone="96170555666")
        assert conv.attention_reason == "escalated"
        # No conversation for this phone -> nothing created.
        cs_agent_tools.escalate_to_human(appmod, tid, None, "r", "s", phone="96171999999")
        assert wi.find_conversation_by_phone(appmod, tid, "96171999999") is None
