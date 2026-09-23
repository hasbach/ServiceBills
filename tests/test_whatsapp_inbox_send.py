import io
import json
from datetime import datetime, timedelta

import pytest
from PIL import Image

import app as appmod
import media_convert
import storage
import whatsapp_inbox as wi
from tests.inbox_helpers import FakeResponse, setup_wa_tenant


@pytest.fixture
def conv_env(app, client, monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())
    calls = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        if url.endswith("/media"):
            return FakeResponse(body={"id": "MEDIA_UP"})
        return FakeResponse(body={"messages": [{"id": f"wamid.SENT{len(calls)}"}]})

    monkeypatch.setattr(wi.requests, "post", fake_post)
    hdr, tid = setup_wa_tenant(app, client, "Biz Send", "send_admin", "PNID_S")
    with app.app_context():
        conv = wi.upsert_conversation(appmod, tid, "96170123456")
        conv.last_inbound_at = datetime.utcnow() - timedelta(hours=1)
        wi.flag_attention(conv, "ai_failed")
        conv.unread_count = 3
        appmod.db.session.commit()
        conv_id = conv.id
        uid = appmod.User.query.filter_by(username="send_admin").first().id
    return {"calls": calls, "conv_id": conv_id, "uid": uid, "tid": tid}


def _conv(conv_env):
    return appmod.db.session.get(appmod.WhatsAppConversation, conv_env["conv_id"])


def test_text_send_pauses_ai_and_clears_attention(app, conv_env):
    with app.app_context():
        conv = _conv(conv_env)
        m = wi.send_admin_message(appmod, conv, conv_env["uid"], "text", text="On it 👍", reply_to="wamid.IN1")
        body = conv_env["calls"][0]["json"]
        assert body == {"messaging_product": "whatsapp", "to": "96170123456", "type": "text",
                        "text": {"body": "On it 👍"}, "context": {"message_id": "wamid.IN1"}}
        assert m.sender == "admin" and m.sent_by_user_id == conv_env["uid"] and m.wa_message_id == "wamid.SENT1"
        assert conv.ai_paused and conv.last_admin_reply_at and not conv.needs_attention and conv.unread_count == 0


def test_reaction_send(app, conv_env):
    with app.app_context():
        m = wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "reaction", target="wamid.IN1", emoji="❤️")
        assert conv_env["calls"][0]["json"]["reaction"] == {"message_id": "wamid.IN1", "emoji": "❤️"}
        assert m.msg_type == "reaction" and m.reaction_target_wa_id == "wamid.IN1"


def test_sticker_png_is_converted_and_uploaded(app, conv_env):
    buf = io.BytesIO(); Image.new("RGBA", (300, 200), (0, 0, 255, 255)).save(buf, "PNG")
    with app.app_context():
        m = wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "sticker", file_bytes=buf.getvalue())
        upload = conv_env["calls"][0]
        assert upload["url"].endswith("/PNID_S/media")
        assert upload["files"]["file"][2] == "image/webp"
        assert conv_env["calls"][1]["json"]["sticker"] == {"id": "MEDIA_UP"}
        stored = Image.open(io.BytesIO(storage.read_bytes(m.media_key)))
        assert stored.size == (512, 512)


@pytest.mark.skipif(not media_convert.ffmpeg_available(), reason="ffmpeg not installed")
def test_voice_is_converted_to_ogg_and_sent(app, conv_env):
    from tests.test_media_convert import _wav_bytes
    with app.app_context():
        m = wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "voice", file_bytes=_wav_bytes())
        assert conv_env["calls"][0]["files"]["file"][2] == "audio/ogg"
        assert conv_env["calls"][1]["json"]["audio"] == {"id": "MEDIA_UP"}
        assert m.msg_type == "audio" and storage.read_bytes(m.media_key)[:4] == b"OggS"


def test_voice_without_ffmpeg_is_503(app, conv_env, monkeypatch):
    monkeypatch.setattr(media_convert, "ffmpeg_available", lambda: False)
    monkeypatch.setattr(media_convert.shutil, "which", lambda n: None)
    with app.app_context():
        with pytest.raises(wi.SendError) as e:
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "voice", file_bytes=b"x")
        assert e.value.code == "voice_unavailable" and e.value.http_status == 503


def test_window_closed_blocks_free_form_but_allows_template(app, conv_env, monkeypatch):
    monkeypatch.setattr(appmod, "build_meta_template_payload",
                        lambda **kw: {"name": kw["template_name"], "language": {"code": "en"}})
    with app.app_context():
        conv = _conv(conv_env)
        conv.last_inbound_at = datetime.utcnow() - timedelta(hours=30)
        appmod.db.session.commit()
        with pytest.raises(wi.WindowClosed):
            wi.send_admin_message(appmod, conv, conv_env["uid"], "text", text="hi")
        assert conv_env["calls"] == []
        m = wi.send_admin_message(appmod, conv, conv_env["uid"], "template", template_name="follow_up", body_params=["Rami"])
        assert conv_env["calls"][0]["json"]["type"] == "template"
        assert m.msg_type == "template" and m.text == "[Template: follow_up] Rami"


def test_meta_131047_maps_to_window_closed_and_records_failed_row(app, conv_env, monkeypatch):
    monkeypatch.setattr(wi.requests, "post", lambda url, **kw: FakeResponse(
        ok=False, status_code=400, body={"error": {"code": 131047, "message": "Re-engagement message"}}))
    with app.app_context():
        with pytest.raises(wi.WindowClosed) as e:
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "text", text="hi")
        assert e.value.code == "window_closed" and e.value.http_status == 409
        failed = appmod.WhatsAppMessage.query.filter_by(sender="admin").first()
        assert failed.status == "failed" and failed.error_code == "131047"
        assert _conv(conv_env).ai_paused is False


def test_network_error_on_text_send_is_recorded_and_returns_502(app, conv_env, monkeypatch):
    def raise_conn_error(url, **kw):
        raise wi.requests.ConnectionError("boom")
    monkeypatch.setattr(wi.requests, "post", raise_conn_error)
    with app.app_context():
        with pytest.raises(wi.SendError) as e:
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "text", text="hi")
        assert e.value.code == "network_error" and e.value.http_status == 502
        failed = appmod.WhatsAppMessage.query.filter_by(sender="admin").first()
        assert failed.status == "failed" and failed.error_code == "network_error"


def test_sticker_upload_meta_error_is_recorded_as_failed_row(app, conv_env, monkeypatch):
    buf = io.BytesIO(); Image.new("RGBA", (300, 200), (0, 0, 255, 255)).save(buf, "PNG")
    monkeypatch.setattr(wi.requests, "post", lambda url, **kw: FakeResponse(
        ok=False, status_code=400, body={"error": {"code": 190, "message": "Invalid token"}}))
    with app.app_context():
        with pytest.raises(wi.SendError) as e:
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "sticker", file_bytes=buf.getvalue())
        assert e.value.code == "190" and e.value.meta_code == "190"
        failed = appmod.WhatsAppMessage.query.filter_by(sender="admin").first()
        assert failed.status == "failed" and failed.msg_type == "sticker" and failed.error_code == "190"


def test_empty_text_and_unknown_kind_rejected(app, conv_env):
    with app.app_context():
        with pytest.raises(wi.SendError) as e:
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "text", text="   ")
        assert e.value.http_status == 400
        with pytest.raises(wi.SendError):
            wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "gif")


def test_template_explicit_language_overrides_definition(app, conv_env, monkeypatch):
    seen = {}

    def fake_build(**kw):
        seen.update(kw)
        return {"name": kw["template_name"], "language": {"code": "en"}}  # definition's language

    monkeypatch.setattr(appmod, "build_meta_template_payload", fake_build)
    with app.app_context():
        wi.send_admin_message(appmod, _conv(conv_env), conv_env["uid"], "template",
                              template_name="follow_up", template_language="ar")
        assert seen["default_language"] == "ar"
        assert conv_env["calls"][0]["json"]["template"]["language"] == {"code": "ar"}
