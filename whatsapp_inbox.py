"""WhatsApp inbox: persistence, "needs attention" flags, window, statuses,
media, push notifications, AI hooks and admin sends.

Every function that touches the DB takes the app module (`appmod`) first,
exactly like cs_agent_tools.py, so this module never imports app.py at import
time. Functions here do NOT commit unless their docstring says so -- the caller
owns the transaction. See docs/superpowers/specs/2026-09-23-whatsapp-inbox-design.md.
"""
import io
import logging
import mimetypes
import re
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy.exc import IntegrityError

import media_convert
import storage

WINDOW = timedelta(hours=24)
AI_AUTO_RESUME_AFTER = timedelta(hours=24)
PUSH_THROTTLE = timedelta(minutes=2)

ATTENTION_REASONS = ('ai_failed', 'escalated', 'awaiting_admin', 'unknown_sender',
                     'ai_inactive', 'media_received', 'send_failed')
REASON_LABELS = {
    'ai_failed': "AI couldn't answer",
    'escalated': 'Escalated to a human',
    'awaiting_admin': 'New message (AI paused)',
    'unknown_sender': 'Unknown sender',
    'ai_inactive': 'AI is off',
    'media_received': 'Media received',
    'send_failed': 'Message failed to send',
}
MEDIA_TYPES = ('audio', 'image', 'video', 'document', 'sticker')
FLAG_MEDIA_TYPES = ('image', 'video', 'document', 'location', 'contacts')
PREVIEW_LABELS = {
    'audio': '🎤 Voice note', 'image': '📷 Photo', 'video': '🎬 Video',
    'document': '📄 Document', 'sticker': 'Sticker', 'location': '📍 Location',
    'contacts': '👤 Contact', 'template': 'Template', 'unsupported': 'Unsupported message',
}
STATUS_RANK = {'queued': 0, 'sent': 1, 'delivered': 2, 'read': 3}

# Tests flip this to True so webhook background work (media download, AI
# reply) runs inline and deterministically instead of in a greenlet.
SYNC_BACKGROUND_TASKS = False

_media_pool = None


def _now():
    """Naive UTC now, matching the naive DateTime columns -- avoids the
    deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def media_pool():
    """Small dedicated pool for media downloads -- never the AI-reply pool."""
    global _media_pool
    if _media_pool is None:
        try:
            from gevent.pool import Pool
            _media_pool = Pool(5)
        except ImportError:
            _media_pool = None
    return _media_pool


def run_background(flask_app, fn, pool=None):
    """Run fn() off the request: inline when SYNC_BACKGROUND_TASKS, else in a
    greenlet (through `pool` when given) inside a fresh app context. Never raises."""
    if SYNC_BACKGROUND_TASKS:
        try:
            fn()
        except Exception:
            logging.exception("whatsapp_inbox background task failed")
        return

    def _wrapped():
        with flask_app.app_context():
            try:
                fn()
            except Exception:
                logging.exception("whatsapp_inbox background task failed")

    try:
        import gevent
    except ImportError:
        gevent = None
    if pool is not None and not (gevent is not None and pool.full()):
        pool.spawn(_wrapped)
    elif gevent is not None:
        gevent.spawn(_wrapped)
    else:
        _wrapped()


def digits(phone):
    return re.sub(r'\D', '', str(phone or ''))


def _ts(value):
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return _now()


def parse_inbound(msg):
    raw_type = msg.get('type') or 'unsupported'
    msg_type = 'audio' if raw_type == 'voice' else raw_type
    out = {
        'msg_type': msg_type, 'text': None,
        'wa_message_id': msg.get('id'),
        'reply_to_wa_message_id': (msg.get('context') or {}).get('id'),
        'wa_media_id': None, 'media_mime': None,
        'reaction_emoji': None, 'reaction_target_wa_id': None,
        'created_at': _ts(msg.get('timestamp')),
    }
    if msg_type == 'text':
        out['text'] = (msg.get('text') or {}).get('body')
    elif msg_type == 'button':
        out['text'] = (msg.get('button') or {}).get('text')
    elif msg_type == 'interactive':
        inter = msg.get('interactive') or {}
        out['text'] = (inter.get(inter.get('type') or '') or {}).get('title')
    elif msg_type in MEDIA_TYPES:
        media = msg.get(raw_type) or {}
        out['wa_media_id'] = media.get('id')
        out['media_mime'] = media.get('mime_type')
        out['text'] = media.get('caption') or (media.get('filename') if msg_type == 'document' else None)
    elif msg_type == 'reaction':
        reaction = msg.get('reaction') or {}
        out['reaction_emoji'] = reaction.get('emoji') or ''
        out['reaction_target_wa_id'] = reaction.get('message_id')
    elif msg_type == 'location':
        loc = msg.get('location') or {}
        out['text'] = f"{loc.get('latitude')},{loc.get('longitude')}" + (f" {loc['name']}" if loc.get('name') else '')
    elif msg_type == 'contacts':
        names = [((c.get('name') or {}).get('formatted_name') or '') for c in (msg.get('contacts') or [])]
        out['text'] = ', '.join(n for n in names if n) or None
    else:
        out['msg_type'] = 'unsupported'
    return out


def preview_for(msg_type, text=None, reaction_emoji=None):
    if msg_type == 'reaction':
        return f"Reacted {reaction_emoji}" if reaction_emoji else "Removed reaction"
    label = PREVIEW_LABELS.get(msg_type)
    if text and label:
        return f"{label}: {text}"[:200]
    return (text or label or '')[:200]


def is_duplicate(appmod, tenant_id, wa_message_id):
    if not wa_message_id:
        return False
    return appmod.WhatsAppMessage.query.filter_by(
        tenant_id=tenant_id, wa_message_id=wa_message_id).first() is not None


def _match_customer(appmod, tenant_id, wa_phone):
    if len(wa_phone) < 8:
        return None
    return appmod.Customer.query.filter_by(tenant_id=tenant_id).filter(
        appmod.Customer.phone.like(f"%{wa_phone[-8:]}%")).first()


def upsert_conversation(appmod, tenant_id, wa_phone, contact_name=None):
    wa_phone = digits(wa_phone)
    Conv = appmod.WhatsAppConversation
    conv = Conv.query.filter_by(tenant_id=tenant_id, wa_phone=wa_phone).first()
    if conv is None:
        try:
            with appmod.db.session.begin_nested():
                conv = Conv(tenant_id=tenant_id, wa_phone=wa_phone, last_message_at=_now(),
                            needs_attention=False, ai_paused=False, unread_count=0)
                appmod.db.session.add(conv)
        except IntegrityError:
            # A concurrent webhook delivery created it first.
            conv = Conv.query.filter_by(tenant_id=tenant_id, wa_phone=wa_phone).first()
    if contact_name:
        conv.contact_name = contact_name[:120]
    if conv.customer_id is None:
        cust = _match_customer(appmod, tenant_id, wa_phone)
        if cust:
            conv.customer_id = cust.id
    appmod.db.session.flush()
    return conv


def find_conversation_by_phone(appmod, tenant_id, phone):
    d = digits(phone)
    if not d:
        return None
    Conv = appmod.WhatsAppConversation
    exact = Conv.query.filter_by(tenant_id=tenant_id, wa_phone=d).first()
    if exact or len(d) < 8:
        return exact
    return Conv.query.filter_by(tenant_id=tenant_id).filter(
        Conv.wa_phone.like(f"%{d[-8:]}")).order_by(Conv.last_message_at.desc()).first()


def record_inbound(appmod, tenant_id, conv, parsed):
    msg = appmod.WhatsAppMessage(
        tenant_id=tenant_id, conversation_id=conv.id, direction='in', sender='customer',
        msg_type=parsed['msg_type'], text=parsed.get('text'),
        wa_message_id=parsed.get('wa_message_id'),
        reply_to_wa_message_id=parsed.get('reply_to_wa_message_id'),
        reaction_emoji=parsed.get('reaction_emoji'),
        reaction_target_wa_id=parsed.get('reaction_target_wa_id'),
        wa_media_id=parsed.get('wa_media_id'),
        media_mime=(parsed.get('media_mime') or '').split(';')[0].strip() or None,
        media_status='pending' if parsed.get('wa_media_id') else 'none',
        status='received', created_at=parsed.get('created_at') or _now())
    appmod.db.session.add(msg)
    conv.last_inbound_at = msg.created_at
    conv.last_message_at = msg.created_at
    conv.last_message_preview = preview_for(msg.msg_type, msg.text, msg.reaction_emoji)
    conv.unread_count = (conv.unread_count or 0) + 1
    appmod.db.session.flush()
    return msg


def record_outbound(appmod, conv, *, sender, msg_type, text=None, transcript=None, sent_by_user_id=None,
                    wa_message_id=None, status='sent', error_code=None, error_message=None,
                    media_key=None, media_playback_key=None, media_mime=None,
                    reaction_emoji=None, reaction_target_wa_id=None, reply_to_wa_message_id=None):
    now = _now()
    msg = appmod.WhatsAppMessage(
        tenant_id=conv.tenant_id, conversation_id=conv.id, direction='out', sender=sender,
        sent_by_user_id=sent_by_user_id, msg_type=msg_type, text=text, transcript=transcript,
        wa_message_id=wa_message_id, reply_to_wa_message_id=reply_to_wa_message_id,
        reaction_emoji=reaction_emoji, reaction_target_wa_id=reaction_target_wa_id,
        media_key=media_key, media_playback_key=media_playback_key, media_mime=media_mime,
        media_status='stored' if media_key else 'none', status=status,
        error_code=(str(error_code)[:20] if error_code else None),
        error_message=(str(error_message)[:300] if error_message else None), created_at=now)
    appmod.db.session.add(msg)
    conv.last_message_at = now
    conv.last_message_preview = preview_for(msg_type, text, reaction_emoji)
    appmod.db.session.flush()
    return msg


def flag_attention(conv, reason):
    if reason not in ATTENTION_REASONS:
        raise ValueError(f"unknown attention reason: {reason}")
    if not conv.needs_attention or conv.attention_since is None:
        conv.attention_since = _now()
    conv.needs_attention = True
    conv.attention_reason = reason


def clear_attention(conv):
    conv.needs_attention = False
    conv.attention_reason = None
    conv.attention_since = None


def inbound_attention_reason(*, ai_paused, ai_will_run, has_customer, msg_type):
    if ai_paused:
        return 'awaiting_admin'
    if not ai_will_run:
        return 'ai_inactive'
    if msg_type in FLAG_MEDIA_TYPES:
        return 'media_received'
    if not has_customer:
        return 'unknown_sender'
    return None


def maybe_auto_resume(conv, now=None):
    now = now or _now()
    last = conv.last_admin_reply_at or conv.ai_paused_at
    if conv.ai_paused and last and now - last > AI_AUTO_RESUME_AFTER:
        conv.ai_paused = False
        conv.ai_paused_at = None
        return True
    return False


def window_open(conv, now=None):
    now = now or _now()
    return bool(conv.last_inbound_at and now < conv.last_inbound_at + WINDOW)


def notify_conversation(appmod, conv, body=None, now=None):
    """Web-push this conversation to the tenant's admins, at most once per
    PUSH_THROTTLE per conversation. Commits last_push_at. Never raises."""
    now = now or _now()
    if conv.last_push_at and now - conv.last_push_at < PUSH_THROTTLE:
        return False
    try:
        conv.last_push_at = now
        appmod.db.session.commit()
        title = (conv.customer.name if conv.customer_id and conv.customer else None) or conv.contact_name or f"+{conv.wa_phone}"
        if body is None:
            label = REASON_LABELS.get(conv.attention_reason, 'New WhatsApp message')
            preview = conv.last_message_preview or ''
            body = f"{label}: {preview}" if preview else label
        payload = {'title': title, 'body': body[:180], 'tag': f"wa-conv-{conv.id}",
                   'url': f"/?view=messaging&inbox={conv.id}", 'conversation_id': conv.id}
        appmod.send_push_notification(payload, tenant_id=conv.tenant_id, roles=['admin'], topic='whatsapp_inbox')
    except Exception:
        appmod.db.session.rollback()
        logging.exception("whatsapp_inbox push failed")
        return False
    return True


def apply_status(appmod, tenant_id, status):
    wamid = status.get('id')
    new = status.get('status')
    if not wamid:
        return None, None
    msg = appmod.WhatsAppMessage.query.filter_by(
        tenant_id=tenant_id, wa_message_id=wamid, direction='out').first()
    if msg is None:
        return None, None
    if new == 'failed':
        err = (status.get('errors') or [{}])[0] or {}
        msg.status = 'failed'
        msg.error_code = str(err.get('code') or '')[:20] or None
        msg.error_message = (err.get('title') or err.get('message') or '')[:300] or None
        conv = appmod.db.session.get(appmod.WhatsAppConversation, msg.conversation_id)
        flag_attention(conv, 'send_failed')
        return msg, conv
    if new in STATUS_RANK and msg.status != 'failed' and STATUS_RANK[new] > STATUS_RANK.get(msg.status, -1):
        msg.status = new
    return msg, None


def store_inbound_media(appmod, message_id, access_token, api_version):
    """Download an inbound message's media from Meta into storage (plus an mp3
    playback copy for audio). Commits. Never raises into the caller."""
    import cs_agent_tools  # lazy: cs_agent_tools imports this module lazily too
    msg = appmod.db.session.get(appmod.WhatsAppMessage, message_id)
    if msg is None or not msg.wa_media_id:
        return
    try:
        data, mime = cs_agent_tools.download_meta_media(access_token, msg.wa_media_id, api_version=api_version)
        if not data:
            msg.media_status = 'failed'
            appmod.db.session.commit()
            return
        mime = (mime or msg.media_mime or 'application/octet-stream').split(';')[0].strip()
        ext = mimetypes.guess_extension(mime) or '.bin'
        msg.media_key = storage.save_bytes(data, msg.tenant_id, f"wa-{msg.id}{ext}", mime)
        msg.media_mime = mime
        if msg.msg_type == 'audio':
            try:
                mp3 = media_convert.to_mp3(data)
                msg.media_playback_key = storage.save_bytes(mp3, msg.tenant_id, f"wa-{msg.id}.mp3", 'audio/mpeg')
            except media_convert.ConversionError as e:
                logging.warning(f"whatsapp_inbox: no mp3 playback copy for message {msg.id}: {e}")
        msg.media_status = 'stored'
        appmod.db.session.commit()
    except Exception:
        appmod.db.session.rollback()
        logging.exception(f"whatsapp_inbox: media download failed for message {message_id}")
        msg = appmod.db.session.get(appmod.WhatsAppMessage, message_id)
        if msg is not None:
            msg.media_status = 'failed'
            appmod.db.session.commit()


def flag_attention_for_phone(appmod, tenant_id, phone, reason):
    """Flag an EXISTING conversation for this phone (never creates one -- an
    escalation from the phone channel must not invent a WhatsApp chat).
    Commits and pushes. Returns the conversation or None."""
    conv = find_conversation_by_phone(appmod, tenant_id, phone)
    if conv is None:
        return None
    flag_attention(conv, reason)
    appmod.db.session.commit()
    notify_conversation(appmod, conv)
    return conv


def record_ai_reply(appmod, tenant_id, wa_phone, *, reply_text, text_wamid, text_ok, text_error, voice_result):
    """Record what the AI actually sent. Returns True when the text send failed
    (the conversation is then flagged send_failed). Commits; never raises."""
    try:
        conv = upsert_conversation(appmod, tenant_id, wa_phone)
        record_outbound(appmod, conv, sender='ai', msg_type='text', text=reply_text,
                        wa_message_id=text_wamid if text_ok else None,
                        status='sent' if text_ok else 'failed',
                        error_code=(text_error or {}).get('code'), error_message=(text_error or {}).get('message'))
        if voice_result:
            record_outbound(appmod, conv, sender='ai', msg_type='audio', transcript=reply_text,
                            wa_message_id=voice_result if isinstance(voice_result, str) else None)
        if not text_ok:
            flag_attention(conv, 'send_failed')
        appmod.db.session.commit()
        return not text_ok
    except Exception:
        appmod.db.session.rollback()
        logging.exception("whatsapp_inbox: could not record AI reply")
        return not text_ok


def after_ai_reply(appmod, tenant_id, wa_phone, result):
    """Called after every AI attempt on an inbound message. Flags:
    - ai_failed: the AI raised / returned nothing, OR the tenant has a Gemini
      key but Gemini produced nothing and the rule-based fallback answered;
    - escalated: the (rule-based) AI decided to escalate.
    send_failed was already flagged by record_ai_reply and takes precedence
    (the customer never got the reply). Commits and pushes."""
    try:
        conv = find_conversation_by_phone(appmod, tenant_id, wa_phone)
        if conv is None:
            return
        if result and result.get('inbox_send_failed'):
            pass  # keep send_failed
        elif result is None:
            flag_attention(conv, 'ai_failed')
        elif result.get('escalate'):
            flag_attention(conv, 'escalated')
        elif result.get('gemini_configured') and result.get('ai_source') == 'rules':
            flag_attention(conv, 'ai_failed')
        appmod.db.session.commit()
        if conv.needs_attention:
            notify_conversation(appmod, conv)
    except Exception:
        appmod.db.session.rollback()
        logging.exception("whatsapp_inbox: after_ai_reply failed")


class SendError(Exception):
    def __init__(self, code, message, http_status=502, meta_code=None):
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.http_status = http_status
        # Raw error code from Meta's Graph API, when this SendError wraps one.
        # Kept separate from .code so subclasses (e.g. WindowClosed) can carry
        # a stable public .code while still recording the real Meta code.
        self.meta_code = str(meta_code) if meta_code is not None else None


class WindowClosed(SendError):
    def __init__(self, message="The 24-hour WhatsApp window has closed -- send an approved template instead.",
                meta_code=None):
        super().__init__('window_closed', message, 409, meta_code=meta_code)


def _graph_base(settings):
    return f"https://graph.facebook.com/{settings.api_version or 'v19.0'}/{settings.phone_number_id}"


def _json(res):
    try:
        return res.json() or {}
    except ValueError:
        return {}


def _post_message(settings, payload):
    try:
        res = requests.post(f"{_graph_base(settings)}/messages",
                            json={'messaging_product': 'whatsapp', **payload},
                            headers={'Authorization': f'Bearer {settings.access_token}',
                                     'Content-Type': 'application/json'}, timeout=15)
    except requests.RequestException as e:
        logging.warning("whatsapp_inbox: network error posting message: %s", e)
        raise SendError('network_error', 'Could not reach WhatsApp. Please try again.', 502)
    body = _json(res)
    if not res.ok:
        err = body.get('error') or {}
        code = str(err.get('code') or res.status_code)
        if code == '131047':
            raise WindowClosed(meta_code=code)
        raise SendError(code, err.get('message') or res.text[:300], meta_code=code)
    return ((body.get('messages') or [{}])[0]).get('id')


def _upload_media(settings, data, filename, mime):
    try:
        res = requests.post(f"{_graph_base(settings)}/media",
                            headers={'Authorization': f'Bearer {settings.access_token}'},
                            files={'file': (filename, io.BytesIO(data), mime)},
                            data={'messaging_product': 'whatsapp', 'type': mime}, timeout=30)
    except requests.RequestException as e:
        logging.warning("whatsapp_inbox: network error uploading media: %s", e)
        raise SendError('network_error', 'Could not reach WhatsApp. Please try again.', 502)
    body = _json(res)
    if not res.ok or not body.get('id'):
        err = body.get('error') or {}
        code = str(err.get('code') or res.status_code)
        raise SendError(code, err.get('message') or 'Media upload to WhatsApp failed', meta_code=code)
    return body['id']


def _record_send_failure(appmod, conv, user_id, rec, err):
    """Record a failed outbound admin message and commit, then the caller re-raises `err`.

    Used both for a failed media upload (Task 8 finding #2: an upload failure
    must still leave an auditable failed row) and for a failed message post.
    error_code prefers the raw Meta error code (`meta_code`) over the public
    `.code`, so a WindowClosed still records '131047' while the exception the
    caller sees keeps `.code == 'window_closed'` for the route contract.
    """
    record_outbound(appmod, conv, sender='admin', sent_by_user_id=user_id, status='failed',
                    error_code=err.meta_code or err.code, error_message=err.message, **rec)
    appmod.db.session.commit()


def send_admin_message(appmod, conv, user_id, kind, *, text=None, reply_to=None, target=None, emoji=None,
                       file_bytes=None, template_name=None, body_params=None, header_param=None,
                       template_language=None):
    settings = appmod.WhatsAppSettings.query.filter_by(tenant_id=conv.tenant_id).first()
    if not settings or not settings.access_token or not settings.phone_number_id:
        raise SendError('not_configured', 'WhatsApp Cloud API is not configured for this business.', 400)
    if kind != 'template' and not window_open(conv):
        raise WindowClosed()

    to = conv.wa_phone
    rec = {'msg_type': kind, 'text': None}
    if kind == 'text':
        text = (text or '').strip()
        if not text:
            raise SendError('empty', 'Message is empty.', 400)
        payload = {'to': to, 'type': 'text', 'text': {'body': text[:4096]}}
        if reply_to:
            payload['context'] = {'message_id': reply_to}
        rec.update(text=text[:4096], reply_to_wa_message_id=reply_to)
    elif kind == 'reaction':
        if not target:
            raise SendError('missing_target', 'Pick a message to react to.', 400)
        payload = {'to': to, 'type': 'reaction', 'reaction': {'message_id': target, 'emoji': emoji or ''}}
        rec.update(reaction_emoji=emoji or '', reaction_target_wa_id=target)
    elif kind == 'voice':
        if not file_bytes:
            raise SendError('empty', 'No recording received.', 400)
        if len(file_bytes) > media_convert.VOICE_MAX_BYTES:
            raise SendError('too_large', 'Voice notes are limited to 16 MB.', 413)
        if not media_convert.ffmpeg_available():
            raise SendError('voice_unavailable', 'Voice notes are unavailable: ffmpeg is not installed on the server.', 503)
        try:
            ogg = media_convert.to_ogg_opus(file_bytes)
        except media_convert.ConversionError as e:
            logging.warning("whatsapp_inbox: voice conversion failed: %s", e)
            raise SendError('bad_audio', 'Could not convert the recording.', 400)
        try:
            media_id = _upload_media(settings, ogg, 'voice.ogg', 'audio/ogg')
        except SendError as e:
            _record_send_failure(appmod, conv, user_id, rec, e)
            raise
        playback_key = None
        try:
            playback_key = storage.save_bytes(media_convert.to_mp3(ogg), conv.tenant_id, 'voice.mp3', 'audio/mpeg')
        except media_convert.ConversionError:
            pass
        payload = {'to': to, 'type': 'audio', 'audio': {'id': media_id}}
        rec.update(msg_type='audio', media_key=storage.save_bytes(ogg, conv.tenant_id, 'voice.ogg', 'audio/ogg'),
                   media_playback_key=playback_key, media_mime='audio/ogg')
    elif kind == 'sticker':
        if not file_bytes:
            raise SendError('empty', 'No sticker file received.', 400)
        if len(file_bytes) > media_convert.STICKER_SOURCE_MAX_BYTES:
            raise SendError('too_large', 'Sticker source images are limited to 5 MB.', 413)
        try:
            webp = media_convert.to_sticker_webp(file_bytes)
        except media_convert.ConversionError as e:
            raise SendError('bad_image', str(e), 400)
        try:
            media_id = _upload_media(settings, webp, 'sticker.webp', 'image/webp')
        except SendError as e:
            _record_send_failure(appmod, conv, user_id, rec, e)
            raise
        payload = {'to': to, 'type': 'sticker', 'sticker': {'id': media_id}}
        rec.update(media_key=storage.save_bytes(webp, conv.tenant_id, 'sticker.webp', 'image/webp'), media_mime='image/webp')
    elif kind == 'template':
        if not template_name:
            raise SendError('missing_template', 'Pick a template.', 400)
        params = [str(p) for p in (body_params or []) if str(p).strip()]
        tpl = appmod.build_meta_template_payload(
            settings=settings, template_name=template_name,
            default_language=template_language or settings.template_language or 'en',
            user_body_params=params, user_header_params=header_param or None)
        if template_language:
            # The stored definition's language would otherwise win -- the admin
            # picked a specific (name, language) pair.
            tpl['language'] = {'code': template_language}
        payload = {'to': to, 'type': 'template', 'template': tpl}
        rec.update(text=f"[Template: {template_name}]" + (" " + " | ".join(params) if params else ""))
    else:
        raise SendError('bad_kind', f'Unsupported message type: {kind}', 400)

    try:
        wamid = _post_message(settings, payload)
    except SendError as e:
        _record_send_failure(appmod, conv, user_id, rec, e)
        raise
    msg = record_outbound(appmod, conv, sender='admin', sent_by_user_id=user_id, wa_message_id=wamid,
                          status='sent', **rec)
    now = _now()
    conv.last_admin_reply_at = now
    conv.ai_paused = True
    conv.ai_paused_at = now
    conv.unread_count = 0
    clear_attention(conv)
    appmod.db.session.commit()
    return msg
