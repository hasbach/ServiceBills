"""The AI's own voice replies are stored so admins can play them in the inbox
(previously only a transcript row was recorded, with no audio)."""
from unittest.mock import MagicMock

import app as appmod
import cs_agent_tools
import storage
import whatsapp_inbox as wi
from tests.conftest import make_tenant
from tests.inbox_helpers import FakeResponse, seed_customer


def _tid(username):
    return appmod.User.query.filter_by(username=username).first().tenant_id


def _local_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_backend", storage.LocalBackend())


def test_record_ai_reply_stores_voice_audio(app, client, monkeypatch, tmp_path):
    _local_storage(monkeypatch, tmp_path)
    make_tenant(client, "Biz AIV", "aiv_admin")
    with app.app_context():
        tid = _tid("aiv_admin")
        wi.record_ai_reply(appmod, tid, "96170100500", reply_text="hello", text_wamid="wamid.T",
                           text_ok=True, text_error=None, voice_result="wamid.V", voice_audio=b"ID3fake-mp3")
        audio = appmod.WhatsAppMessage.query.filter_by(sender="ai", msg_type="audio").one()
        assert audio.media_status == "stored" and audio.media_mime == "audio/mpeg"
        assert storage.read_bytes(audio.media_key) == b"ID3fake-mp3"
        assert audio.transcript == "hello" and audio.wa_message_id == "wamid.V"


def test_record_ai_reply_voice_row_survives_storage_failure(app, client, monkeypatch):
    make_tenant(client, "Biz AIV2", "aiv2_admin")

    def boom(*a, **kw):
        raise OSError("R2 down")

    monkeypatch.setattr(storage, "save_bytes", boom)
    with app.app_context():
        tid = _tid("aiv2_admin")
        failed = wi.record_ai_reply(appmod, tid, "96170100600", reply_text="hi", text_wamid="wamid.T2",
                                    text_ok=True, text_error=None, voice_result="wamid.V2", voice_audio=b"x")
        assert failed is False
        audio = appmod.WhatsAppMessage.query.filter_by(sender="ai", msg_type="audio").one()
        assert audio.media_status == "none" and audio.wa_message_id == "wamid.V2"
        assert appmod.WhatsAppMessage.query.filter_by(sender="ai", msg_type="text").count() == 1


def test_handle_ai_reply_passes_tts_audio_to_inbox(app, client, monkeypatch, tmp_path):
    _local_storage(monkeypatch, tmp_path)
    hdr = make_tenant(client, "Biz AIV3", "aiv3_admin")
    seed_customer(client, hdr, "70777001", name="Voice Cust")
    ids = iter(["wamid.TEXT", "wamid.VOICE"])  # Meta gives every message its own id
    monkeypatch.setattr(cs_agent_tools.requests, "post",
                        lambda url, **kw: FakeResponse(body={"id": "MEDIA1"} if url.endswith("/media")
                                                       else {"messages": [{"id": next(ids)}]}))
    monkeypatch.setattr(cs_agent_tools, "synthesize_speech_elevenlabs", lambda *a, **kw: b"ID3-tts-audio")
    settings = MagicMock()
    settings.access_token = "tok"
    settings.phone_number_id = "PN"
    settings.api_version = "v19.0"
    with app.app_context():
        tid = _tid("aiv3_admin")
        cust = appmod.Customer.query.filter_by(tenant_id=tid).first()
        cs_agent_tools.handle_whatsapp_cs_ai_reply(appmod, tid, "96170777001", cust, "رصيدي",
                                                   is_voice=True, settings=settings)
        audio = appmod.WhatsAppMessage.query.filter_by(sender="ai", msg_type="audio").one()
        assert storage.read_bytes(audio.media_key) == b"ID3-tts-audio"


def test_voice_audio_not_stored_when_voice_send_failed(app, client, monkeypatch, tmp_path):
    _local_storage(monkeypatch, tmp_path)
    make_tenant(client, "Biz AIV4", "aiv4_admin")
    with app.app_context():
        tid = _tid("aiv4_admin")
        wi.record_ai_reply(appmod, tid, "96170100700", reply_text="hi", text_wamid="wamid.T4",
                           text_ok=True, text_error=None, voice_result=False, voice_audio=b"ID3")
        assert appmod.WhatsAppMessage.query.filter_by(sender="ai", msg_type="audio").count() == 0
