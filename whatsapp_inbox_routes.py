"""Admin API for the WhatsApp inbox -- see
docs/superpowers/specs/2026-09-23-whatsapp-inbox-design.md. Registered from
app.py via register_inbox_routes(app, appmod) so app.py doesn't grow further."""
from functools import wraps

from flask import jsonify, request, Response
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity

import media_convert
import storage
import whatsapp_inbox as wi
from tenancy import tenant_query

PAGE_SIZE = 30
THREAD_PAGE = 50
MULTIPART_OVERHEAD = 1024 * 1024  # form fields + multipart framing on top of the file


def register_inbox_routes(app, appmod):
    db = appmod.db
    Conv = appmod.WhatsAppConversation
    Msg = appmod.WhatsAppMessage

    def inbox_admin(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            if 'admin' not in appmod._jwt_roles():
                return jsonify(msg="Admins only!"), 403
            return fn(*args, **kwargs)
        return wrapper

    def _conv_or_404(conv_id):
        return tenant_query(Conv).filter_by(id=conv_id).first_or_404()

    def _customer_summary(conv):
        c = conv.customer
        if not c:
            return None
        plan = getattr(c, 'subscription_plan', None)
        return {'id': c.id, 'name': c.name, 'phone': c.phone,
                'status': 'active' if c.is_subscription_active else 'inactive',
                'balance': c.balance, 'plan': getattr(plan, 'name', None),
                'upstream_status': c.upstream_last_status}

    def _conv_payload(conv):
        d = conv.to_dict()
        d['window_open'] = wi.window_open(conv)
        return d

    @app.route('/api/whatsapp/inbox/summary', methods=['GET'])
    @inbox_admin
    def inbox_summary():
        base = tenant_query(Conv)
        return jsonify({
            'needs_attention': base.filter(Conv.needs_attention.is_(True)).count(),
            'unread': base.filter(Conv.unread_count > 0).count(),
            'voice_available': media_convert.ffmpeg_available(),
            'push_configured': bool(appmod.VAPID_PUBLIC_KEY and appmod.VAPID_PRIVATE_KEY),
        })

    @app.route('/api/whatsapp/inbox/conversations', methods=['GET'])
    @inbox_admin
    def inbox_conversations():
        flt = request.args.get('filter', 'attention')
        q = tenant_query(Conv)
        if flt == 'attention':
            q = q.filter(Conv.needs_attention.is_(True))
        elif flt == 'unread':
            q = q.filter(Conv.unread_count > 0)
        term = (request.args.get('q') or '').strip()
        if term:
            like = f"%{term}%"
            q = q.outerjoin(appmod.Customer, Conv.customer_id == appmod.Customer.id).filter(
                db.or_(Conv.contact_name.ilike(like), Conv.wa_phone.like(f"%{wi.digits(term) or term}%"),
                       appmod.Customer.name.ilike(like)))
        try:
            offset = int(request.args.get('offset', 0) or 0)
        except (TypeError, ValueError):
            offset = 0
        offset = max(offset, 0)
        rows = q.order_by(Conv.last_message_at.desc()).offset(offset).limit(PAGE_SIZE + 1).all()
        return jsonify({'conversations': [c.to_dict() for c in rows[:PAGE_SIZE]], 'has_more': len(rows) > PAGE_SIZE})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/messages', methods=['GET'])
    @inbox_admin
    def inbox_messages(conv_id):
        conv = _conv_or_404(conv_id)
        q = tenant_query(Msg).filter_by(conversation_id=conv.id)
        before = request.args.get('before', type=int)
        if before:
            q = q.filter(Msg.id < before)
        rows = q.order_by(Msg.id.desc()).limit(THREAD_PAGE + 1).all()
        page = list(reversed(rows[:THREAD_PAGE]))
        payload = _conv_payload(conv)
        payload['customer'] = _customer_summary(conv)
        return jsonify({'conversation': payload, 'messages': [m.to_dict() for m in page],
                        'has_more': len(rows) > THREAD_PAGE})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/read', methods=['POST'])
    @inbox_admin
    def inbox_read(conv_id):
        conv = _conv_or_404(conv_id)
        conv.unread_count = 0
        db.session.commit()
        return jsonify({'conversation': _conv_payload(conv)})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/resolve', methods=['POST'])
    @inbox_admin
    def inbox_resolve(conv_id):
        conv = _conv_or_404(conv_id)
        wi.clear_attention(conv)
        conv.ai_paused = False
        conv.ai_paused_at = None
        db.session.commit()
        return jsonify({'conversation': _conv_payload(conv)})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/pause', methods=['POST'])
    @inbox_admin
    def inbox_pause(conv_id):
        conv = _conv_or_404(conv_id)
        conv.ai_paused = True
        conv.ai_paused_at = wi._now()
        conv.last_admin_reply_at = conv.ai_paused_at  # auto-resume clock starts now
        db.session.commit()
        return jsonify({'conversation': _conv_payload(conv)})

    @app.route('/api/whatsapp/inbox/conversations/<int:conv_id>/send', methods=['POST'])
    @inbox_admin
    def inbox_send(conv_id):
        conv = _conv_or_404(conv_id)
        user = appmod.User.query.filter_by(username=get_jwt_identity()).first()
        if request.files:
            # Overall cap checked before anything is read into memory.
            if (request.content_length or 0) > media_convert.VOICE_MAX_BYTES + MULTIPART_OVERHEAD:
                return jsonify({'error': 'too_large', 'msg': 'Upload is too large.'}), 413
            kind = request.form.get('type')
            upload = request.files.get('file')
            kwargs = {'file_bytes': upload.read() if upload else None}
        else:
            data = request.get_json(silent=True) or {}
            kind = data.get('type')
            kwargs = {'text': data.get('text'), 'reply_to': data.get('reply_to'),
                      'target': data.get('target'), 'emoji': data.get('emoji'),
                      'template_name': data.get('template_name'), 'body_params': data.get('body_params'),
                      'header_param': data.get('header_param'),
                      'template_language': data.get('language')}
        try:
            msg = wi.send_admin_message(appmod, conv, user.id if user else None, kind, **kwargs)
        except wi.SendError as e:
            return jsonify({'error': e.code, 'msg': e.message}), e.http_status
        return jsonify({'message': msg.to_dict()}), 201

    @app.route('/api/whatsapp/inbox/media/<int:message_id>', methods=['GET'])
    @inbox_admin
    def inbox_media(message_id):
        msg = tenant_query(Msg).filter_by(id=message_id).first_or_404()
        use_playback = request.args.get('variant') == 'playback' and msg.media_playback_key
        key = msg.media_playback_key if use_playback else msg.media_key
        if not key:
            return jsonify({'error': 'media_unavailable'}), 404
        try:
            data = storage.read_bytes(key)
        except FileNotFoundError:
            return jsonify({'error': 'media_unavailable'}), 404
        mime = 'audio/mpeg' if use_playback else (msg.media_mime or 'application/octet-stream')
        resp = Response(data, mimetype=mime)
        resp.headers['Cache-Control'] = 'private, max-age=300'
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        if not mime.startswith(('image/', 'audio/', 'video/')):
            resp.headers['Content-Disposition'] = 'attachment'
        return resp
