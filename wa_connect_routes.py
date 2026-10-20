"""Admin API for connecting WhatsApp automatically through the platform Meta
app (Embedded Signup) and for submitting the standard templates -- see
specs/001-whatsapp-auto-connect/contracts/http-api.md. Registered from app.py
via register_wa_connect_routes(app, appmod) so app.py doesn't grow further."""
import re
from functools import wraps

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

import plans
import wa_connect
import wa_standard_templates as wst
from config import Config
from tenancy import current_tenant, current_tenant_id, require_module, tenant_query

EVENTS_SHOWN = 20


def register_wa_connect_routes(app, appmod):
    db = appmod.db

    def plan_allows_api():
        return bool(plans.limits(current_tenant().plan)["whatsapp_api"])

    def automatic_route(fn):
        """Admin-only, whatsapp module, platform app configured, plan allows API."""
        @wraps(fn)
        @jwt_required()
        @appmod.admin_required()
        @require_module('whatsapp')
        def wrapper(*args, **kwargs):
            if not wa_connect.automatic_available(current_tenant()):
                return jsonify({'error': 'Automatic WhatsApp connection is not available.'}), 404
            if not plan_allows_api():
                return jsonify({'error': 'WhatsApp API requires an upgraded plan.'}), 402
            return fn(*args, **kwargs)
        return wrapper

    def current_user_id():
        user = appmod.User.query.filter_by(username=get_jwt_identity()).first()
        return user.id if user else None

    def status_payload(settings):
        events = []
        if settings is not None:
            events = [e.to_dict() for e in tenant_query(appmod.WhatsAppConnectionEvent)
                      .order_by(appmod.WhatsAppConnectionEvent.created_at.desc(),
                                appmod.WhatsAppConnectionEvent.id.desc())
                      .limit(EVENTS_SHOWN)]
        view = (settings or appmod.WhatsAppSettings(connection_method='manual',
                                                    connection_status='disconnected')).connection_view()
        return {'connection': view, 'events': events}

    @app.route('/api/whatsapp/connect/config', methods=['GET'])
    @jwt_required()
    @appmod.admin_required()
    @require_module('whatsapp')
    def wa_connect_config():
        available = wa_connect.automatic_available(current_tenant())
        return jsonify({
            'automatic_available': available,
            'coexistence_available': wa_connect.coexistence_available(current_tenant()),
            'app_id': Config.META_APP_ID if available else None,
            'graph_version': Config.META_GRAPH_VERSION,
            'es_config_id': Config.META_ES_CONFIG_ID if available else None,
            'es_coex_config_id': Config.META_ES_COEX_CONFIG_ID if available else None,
            # Embedded Signup v4 still documents the coexistence flow with these
            # extras; confirmed (or replaced) in the Meta spike, research.md R-2.
            'coex_extras': {'featureType': 'whatsapp_business_app_onboarding', 'sessionInfoVersion': '3'},
            'plan_allows_api': plan_allows_api(),
        }), 200

    @app.route('/api/whatsapp/connect/status', methods=['GET'])
    @jwt_required()
    @appmod.admin_required()
    @require_module('whatsapp')
    def wa_connect_status():
        return jsonify(status_payload(tenant_query(appmod.WhatsAppSettings).first())), 200

    @app.route('/api/whatsapp/connect/complete', methods=['POST'])
    @automatic_route
    def wa_connect_complete():
        data = request.get_json(silent=True) or {}
        method = data.get('method')
        if method not in wa_connect.EXPECTED_ES_EVENT:
            return jsonify({'error': 'Unknown connection method.'}), 400
        if method == 'coexistence' and not wa_connect.coexistence_available(current_tenant()):
            return jsonify({'error': 'Connecting a WhatsApp Business app number is not available.'}), 404
        if data.get('es_event') != wa_connect.EXPECTED_ES_EVENT[method]:
            return jsonify({'error': 'The Meta sign-in finished a different flow than the one you chose.'}), 400
        tid, uid = current_tenant_id(), current_user_id()
        try:
            settings = wa_connect.complete_connection(
                appmod, tid, uid, method=method, code=data.get('code'), waba_id=data.get('waba_id'),
                phone_number_id=data.get('phone_number_id'), business_id=data.get('business_id'))
        except wa_connect.ConnectError as exc:
            db.session.rollback()
            wa_connect.log_event(appmod, tid, 'connect_failed', source='user', user_id=uid,
                                 method=method, detail=exc.message)
            db.session.commit()
            return jsonify({'error': exc.message, 'meta_code': exc.meta_code}), exc.status_code
        return jsonify({'connection': settings.connection_view()}), 200

    @app.route('/api/whatsapp/connect/refresh', methods=['POST'])
    @jwt_required()
    @appmod.admin_required()
    @require_module('whatsapp')
    def wa_connect_refresh():
        settings = tenant_query(appmod.WhatsAppSettings).first()
        if settings is None or not wa_connect.is_automatic(settings) or not settings.access_token:
            return jsonify({'error': 'There is no automatic WhatsApp connection to check.'}), 400
        wa_connect.check_health(appmod, settings, source='user')
        return jsonify(status_payload(settings)), 200

    @app.route('/api/whatsapp/connect/test-message', methods=['POST'])
    @jwt_required()
    @appmod.admin_required()
    @require_module('whatsapp')
    def wa_connect_test_message():
        settings = tenant_query(appmod.WhatsAppSettings).first()
        if settings is None or not settings.access_token or not settings.phone_number_id:
            return jsonify({'error': 'Connect WhatsApp first.'}), 400
        if wa_connect.blocks_sends(settings):
            return jsonify({'error': wa_connect.NEEDS_ATTENTION_SEND_ERROR}), 409
        raw = re.sub(r'\D', '', str((request.get_json(silent=True) or {}).get('to') or ''))
        to = appmod.normalize_whatsapp_phone(raw) if len(raw) >= 7 else ''
        if not to:
            return jsonify({'error': 'Enter the WhatsApp number to send the test to, with country code.'}), 400
        try:
            wa_connect.send_test_message(settings, to)
        except wa_connect.ConnectError as exc:
            wa_connect.note_send_error(appmod, settings, exc.meta_code, exc.message)
            return jsonify({'error': exc.message, 'meta_code': exc.meta_code}), exc.status_code
        return jsonify({'message': 'Test message sent to +{}.'.format(to)}), 200

    @app.route('/api/whatsapp/connect/disconnect', methods=['POST'])
    @jwt_required()
    @appmod.admin_required()
    @require_module('whatsapp')
    def wa_connect_disconnect():
        # Deliberately not gated on the platform app or the plan: a tenant
        # must always be able to stop sending.
        data = request.get_json(silent=True) or {}
        if data.get('confirm') is not True:
            return jsonify({'error': 'Confirm the disconnect.'}), 400
        next_method = data.get('next_method')
        if next_method not in (None, 'manual'):
            return jsonify({'error': 'Unknown next method.'}), 400
        settings = tenant_query(appmod.WhatsAppSettings).first()
        if settings is None:
            return jsonify({'error': 'WhatsApp is not configured.'}), 400
        wa_connect.disconnect(appmod, settings, current_user_id(), next_method=next_method)
        return jsonify(status_payload(settings)), 200

    # ---- standard templates (FR-015..FR-015d) -----------------------------

    def api_settings_or_error():
        settings = tenant_query(appmod.WhatsAppSettings).first()
        if not settings or settings.mode != 'api' or not settings.access_token or not settings.business_account_id:
            return None, (jsonify({'error': 'Connect WhatsApp (API mode) before submitting templates.'}), 400)
        if not plan_allows_api():
            return None, (jsonify({'error': 'WhatsApp API requires an upgraded plan.'}), 402)
        return settings, None

    @app.route('/api/whatsapp/standard-templates', methods=['GET'])
    @jwt_required()
    @appmod.admin_required()
    @require_module('whatsapp')
    def wa_standard_templates_list():
        settings = tenant_query(appmod.WhatsAppSettings).first()
        existing = {t.name: t for t in tenant_query(appmod.WhatsAppTemplate).all()}
        items = []
        for item in wst.catalog_view():
            # Show the name the tenant's settings currently send, so an already
            # configured template is recognised as existing.
            current = getattr(settings, item['settings_field'], None) if settings else None
            name = current or item['name']
            row = existing.get(name)
            item['name'] = name
            item['existing'] = {'status': row.status, 'id': row.id} if row else None
            items.append(item)
        return jsonify({'templates': items}), 200

    @app.route('/api/whatsapp/standard-templates/submit', methods=['POST'])
    @jwt_required()
    @appmod.admin_required()
    @require_module('whatsapp')
    def wa_standard_templates_submit():
        settings, error = api_settings_or_error()
        if error:
            return error
        data = request.get_json(silent=True) or {}
        language = (data.get('language') or settings.template_language or 'en').strip()
        base_url = appmod._public_base_url(settings.tenant_id)
        results = []
        for item in data.get('items') or []:
            key = item.get('key')
            entry = wst.CATALOG.get(key)
            name = (item.get('name') or (entry or {}).get('default_name') or '').strip()
            if entry is None:
                results.append({'key': key, 'ok': False, 'error': 'Unknown standard template.'})
                continue
            if item.get('use_existing'):
                setattr(settings, entry['settings_field'], name)
                results.append({'key': key, 'ok': True, 'status': 'EXISTING'})
                continue
            problems = wst.validate_item(key, item.get('header'), item.get('body'), item.get('footer'),
                                         item.get('buttons'))
            if not problems and not re.fullmatch(r'[a-z0-9_]{1,512}', name):
                problems = ['Template name may only use lowercase letters, numbers and underscores.']
            if problems:
                results.append({'key': key, 'ok': False, 'error': ' '.join(problems)})
                continue
            components, param_map = wst.to_meta_components(
                key, item.get('header'), item.get('body'), item.get('footer'), item.get('buttons'),
                base_url=base_url)
            row, meta_error = appmod._create_template_on_meta(
                settings, name, language, entry['category'], components,
                standard_key=key, param_map=param_map)
            if meta_error:
                results.append({'key': key, 'ok': False, 'error': meta_error})
                continue
            setattr(settings, entry['settings_field'], name)
            db.session.flush()
            results.append({'key': key, 'ok': True, 'status': row.status, 'id': row.id})
        db.session.commit()
        return jsonify({'results': results}), 200
