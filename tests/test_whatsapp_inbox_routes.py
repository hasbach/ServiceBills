import io
from datetime import datetime, timedelta

import pytest

import app as appmod
import media_convert
import storage
import whatsapp_inbox as wi
from tests.conftest import auth_headers
from tests.inbox_helpers import FakeResponse, setup_wa_tenant


@pytest.fixture
def two_tenants(app, client, monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    monkeypatch.setattr(wi.requests, "post", lambda url, **kw: FakeResponse(body={"messages": [{"id": "wamid.R1"}]}))
    a_hdr, a_tid = setup_wa_tenant(app, client, "Biz RA", "ra_admin", "PNID_RA")
    b_hdr, b_tid = setup_wa_tenant(app, client, "Biz RB", "rb_admin", "PNID_RB")
    with app.app_context():
        a1 = wi.upsert_conversation(appmod, a_tid, "96170000001", "Alpha")
        wi.record_inbound(appmod, a_tid, a1, wi.parse_inbound({"id": "wamid.A1", "type": "text", "text": {"body": "no internet"}}))
        wi.flag_attention(a1, "ai_failed")
        a2 = wi.upsert_conversation(appmod, a_tid, "96170000002", "Beta")
        img = wi.record_inbound(appmod, a_tid, a2, wi.parse_inbound({"id": "wamid.A2", "type": "image", "image": {"id": "M"}}))
        img.media_key = storage.save_bytes(b"IMGDATA", a_tid, "x.jpg", "image/jpeg")
        img.media_mime = "image/jpeg"; img.media_status = "stored"
        a2.unread_count = 0
        b1 = wi.upsert_conversation(appmod, b_tid, "96170000009", "Other")
        appmod.db.session.commit()
        ids = {"a1": a1.id, "a2": a2.id, "b1": b1.id, "img": img.id}
    return {"a": a_hdr, "b": b_hdr, **ids}


def test_summary_and_list_filters(client, two_tenants):
    r = client.get("/api/whatsapp/inbox/summary", headers=two_tenants["a"])
    assert r.status_code == 200 and r.get_json()["needs_attention"] == 1
    r = client.get("/api/whatsapp/inbox/conversations", headers=two_tenants["a"])  # default: attention
    assert [c["contact_name"] for c in r.get_json()["conversations"]] == ["Alpha"]
    r = client.get("/api/whatsapp/inbox/conversations?filter=all", headers=two_tenants["a"])
    assert {c["contact_name"] for c in r.get_json()["conversations"]} == {"Alpha", "Beta"}
    r = client.get("/api/whatsapp/inbox/conversations?filter=unread", headers=two_tenants["a"])
    assert [c["contact_name"] for c in r.get_json()["conversations"]] == ["Alpha"]
    r = client.get("/api/whatsapp/inbox/conversations?filter=all&q=beta", headers=two_tenants["a"])
    assert [c["contact_name"] for c in r.get_json()["conversations"]] == ["Beta"]
    r = client.get("/api/whatsapp/inbox/conversations?filter=all&q=0000002", headers=two_tenants["a"])
    assert [c["contact_name"] for c in r.get_json()["conversations"]] == ["Beta"]


def test_thread_read_resolve_pause(client, two_tenants):
    cid = two_tenants["a1"]
    r = client.get(f"/api/whatsapp/inbox/conversations/{cid}/messages", headers=two_tenants["a"])
    body = r.get_json()
    assert [m["text"] for m in body["messages"]] == ["no internet"]
    assert body["conversation"]["window_open"] is True and body["has_more"] is False
    assert client.post(f"/api/whatsapp/inbox/conversations/{cid}/read", headers=two_tenants["a"]).get_json()["conversation"]["unread_count"] == 0
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/pause", headers=two_tenants["a"])
    assert r.get_json()["conversation"]["ai_paused"] is True
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/resolve", headers=two_tenants["a"])
    conv = r.get_json()["conversation"]
    assert conv["ai_paused"] is False and conv["needs_attention"] is False


def test_send_text_json_and_errors(client, two_tenants, app):
    cid = two_tenants["a1"]
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/send", headers=two_tenants["a"], json={"type": "text", "text": "hi 🙂"})
    assert r.status_code == 201 and r.get_json()["message"]["sender"] == "admin"
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/send", headers=two_tenants["a"], json={"type": "text", "text": ""})
    assert r.status_code == 400 and r.get_json()["error"] == "empty"
    with app.app_context():
        c = appmod.db.session.get(appmod.WhatsAppConversation, cid)
        c.last_inbound_at = datetime.utcnow() - timedelta(hours=48)
        appmod.db.session.commit()
    r = client.post(f"/api/whatsapp/inbox/conversations/{cid}/send", headers=two_tenants["a"], json={"type": "text", "text": "late"})
    assert r.status_code == 409 and r.get_json()["error"] == "window_closed"


def test_send_sticker_multipart(client, two_tenants, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(wi.requests, "post", lambda url, **kw: FakeResponse(
        body={"id": "MEDIA_UP"} if url.endswith("/media") else {"messages": [{"id": "wamid.ST"}]}))
    buf = io.BytesIO(); Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(buf, "PNG"); buf.seek(0)
    r = client.post(f"/api/whatsapp/inbox/conversations/{two_tenants['a1']}/send", headers=two_tenants["a"],
                    data={"type": "sticker", "file": (buf, "s.png")}, content_type="multipart/form-data")
    assert r.status_code == 201 and r.get_json()["message"]["msg_type"] == "sticker"


def test_media_streams_with_tenant_check(client, two_tenants):
    r = client.get(f"/api/whatsapp/inbox/media/{two_tenants['img']}", headers=two_tenants["a"])
    assert r.status_code == 200 and r.data == b"IMGDATA" and r.mimetype == "image/jpeg"
    assert client.get(f"/api/whatsapp/inbox/media/{two_tenants['img']}", headers=two_tenants["b"]).status_code == 404


def test_tenant_isolation_and_roles(client, two_tenants):
    b_conv = two_tenants["b1"]
    for path in (f"/conversations/{b_conv}/messages",):
        assert client.get("/api/whatsapp/inbox" + path, headers=two_tenants["a"]).status_code == 404
    for action in ("read", "resolve", "pause"):
        assert client.post(f"/api/whatsapp/inbox/conversations/{b_conv}/{action}", headers=two_tenants["a"]).status_code == 404
    assert client.post(f"/api/whatsapp/inbox/conversations/{b_conv}/send", headers=two_tenants["a"],
                       json={"type": "text", "text": "x"}).status_code == 404
    collector = auth_headers(client, "rb_collector", role="collector")
    assert client.get("/api/whatsapp/inbox/summary", headers=collector).status_code == 403
    assert client.get("/api/whatsapp/inbox/summary").status_code == 401


def test_send_template_passes_language_through(client, two_tenants, monkeypatch):
    seen = {}

    def fake_send(appmod_, conv, user_id, kind, **kw):
        seen.update(kw, kind=kind)
        raise wi.SendError("stop", "stop", 400)

    monkeypatch.setattr(wi, "send_admin_message", fake_send)
    client.post(f"/api/whatsapp/inbox/conversations/{two_tenants['a1']}/send", headers=two_tenants["a"],
                json={"type": "template", "template_name": "follow_up", "language": "ar"})
    assert seen["kind"] == "template" and seen["template_language"] == "ar"


def test_pause_uses_inbox_clock(client, two_tenants, monkeypatch, app):
    fixed = datetime(2026, 9, 23, 10, 0, 0)
    monkeypatch.setattr(wi, "_now", lambda: fixed)
    client.post(f"/api/whatsapp/inbox/conversations/{two_tenants['a1']}/pause", headers=two_tenants["a"])
    with app.app_context():
        conv = appmod.db.session.get(appmod.WhatsAppConversation, two_tenants["a1"])
        assert conv.ai_paused_at == fixed and conv.last_admin_reply_at == fixed


def test_oversized_upload_rejected_before_read(client, two_tenants, monkeypatch):
    monkeypatch.setattr(media_convert, "VOICE_MAX_BYTES", 10)
    big = io.BytesIO(b"x" * (1024 * 1024 + 100))
    r = client.post(f"/api/whatsapp/inbox/conversations/{two_tenants['a1']}/send", headers=two_tenants["a"],
                    data={"type": "voice", "file": (big, "v.webm")}, content_type="multipart/form-data")
    assert r.status_code == 413 and r.get_json()["error"] == "too_large"


def test_media_headers_nosniff_and_attachment_for_non_media(client, two_tenants, app):
    r = client.get(f"/api/whatsapp/inbox/media/{two_tenants['img']}", headers=two_tenants["a"])
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "Content-Disposition" not in r.headers
    with app.app_context():
        msg = appmod.db.session.get(appmod.WhatsAppMessage, two_tenants["img"])
        conv = appmod.db.session.get(appmod.WhatsAppConversation, msg.conversation_id)
        doc = wi.record_inbound(appmod, msg.tenant_id, conv, wi.parse_inbound(
            {"id": "wamid.DOC", "type": "document", "document": {"id": "D"}}))
        doc.media_key = storage.save_bytes(b"<html>x</html>", msg.tenant_id, "x.html", "text/html")
        doc.media_mime = "text/html"; doc.media_status = "stored"
        appmod.db.session.commit()
        doc_id = doc.id
    r = client.get(f"/api/whatsapp/inbox/media/{doc_id}", headers=two_tenants["a"])
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert (r.headers.get("Content-Disposition") or "").startswith("attachment")
