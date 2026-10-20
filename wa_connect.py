"""WhatsApp automatic connection through the platform Meta app (Embedded
Signup: coexistence or a new API number), alongside the existing manual
per-tenant credentials. See specs/001-whatsapp-auto-connect/.

Like whatsapp_inbox.py, every function that touches the DB takes the app
module (`appmod`) first, so this module never imports app.py at import time.
Functions here do NOT commit unless their docstring says so.
"""
import logging
import secrets
from datetime import datetime, timedelta

import requests

import onprem
from config import Config

AUTOMATIC_METHODS = ('coexistence', 'embedded')
STATUSES = ('connected', 'needs_attention', 'disconnected')
GRAPH_TIMEOUT = 10
# Embedded Signup postMessage success event expected for each method.
EXPECTED_ES_EVENT = {
    'coexistence': 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING',
    'embedded': 'FINISH',
}
_PHONE_FIELDS = ('display_phone_number,verified_name,quality_rating,status,code_verification_status,'
                 'platform_type,is_on_biz_app,whatsapp_business_manager_messaging_limit')
# Meta error codes we translate into something a tenant can act on.
_META_ERROR_HINTS = {
    133016: 'Registration limit reached for this number -- try again in 72 hours.',
    190: 'Meta sign-in expired, please try connecting again.',
}


class ConnectError(Exception):
    """A connect attempt failed; `message` is safe to show the tenant."""

    def __init__(self, status_code, message, meta_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.meta_code = meta_code


def is_automatic(settings):
    return (getattr(settings, 'connection_method', None) or 'manual') in AUTOMATIC_METHODS


def wa_app_identity(settings):
    """(app_id, app_secret) that owns this tenant's WhatsApp connection: the
    tenant's own Meta app for manual configuration, the platform app for an
    Embedded Signup connection. Used for webhook HMAC and app-scoped uploads."""
    if is_automatic(settings):
        return Config.META_APP_ID, Config.META_APP_SECRET
    return settings.app_id, settings.app_secret


def tenant_allowed(tenant):
    """META_AUTO_CONNECT_TENANTS (comma-separated tenant ids or slugs) limits
    automatic connect to chosen tenants while the platform app is still in
    App Review; empty means every tenant."""
    allowed = {x.strip().lower() for x in (Config.META_AUTO_CONNECT_TENANTS or '').split(',') if x.strip()}
    if not allowed:
        return True
    if tenant is None:
        return False
    return str(tenant.id) in allowed or (tenant.slug or '').lower() in allowed


def automatic_available(tenant=None):
    """Automatic connect needs the platform app configured, and a public SaaS
    domain allow-listed in its Login configuration -- so never on-prem. With
    `tenant`, also applies the META_AUTO_CONNECT_TENANTS rollout list."""
    configured = bool(Config.META_APP_ID and Config.META_APP_SECRET and Config.META_ES_CONFIG_ID) \
        and not onprem.is_onprem()
    return configured and (tenant is None or tenant_allowed(tenant))


def coexistence_available(tenant=None):
    return automatic_available(tenant) and bool(Config.META_COEXISTENCE_ENABLED)


def is_routable(settings):
    """Whether webhook events for this settings row should be processed. A
    disconnected automatic connection no longer owns its number/WABA."""
    return not (is_automatic(settings) and settings.connection_status == 'disconnected')


def pick_routable(rows):
    """First routable row of webhook tenant candidates, or None."""
    for row in rows:
        if is_routable(row):
            return row
    return None


def graph_url(path, version=None):
    return 'https://graph.facebook.com/{}/{}'.format(version or Config.META_GRAPH_VERSION, path.lstrip('/'))


def log_event(appmod, tenant_id, event, *, source, user_id=None, method=None,
              from_status=None, to_status=None, detail=None):
    """Add a WhatsAppConnectionEvent (caller commits)."""
    ev = appmod.WhatsAppConnectionEvent(
        tenant_id=tenant_id, user_id=user_id, event=event, method=method,
        from_status=from_status, to_status=to_status, source=source,
        detail=(detail or None) and str(detail)[:500])
    appmod.db.session.add(ev)
    return ev


def set_connection_status(appmod, settings, status, reason, source, *, user_id=None, alert=True):
    """Move a connection to `status` (no-op if unchanged): records a
    status_change event and, for needs_attention, pushes an alert to the
    tenant's admins. Caller commits. Returns True if the status changed."""
    if status not in STATUSES:
        raise ValueError('unknown connection status: {}'.format(status))
    previous = settings.connection_status
    settings.status_reason = (reason or None) and str(reason)[:300]
    if previous == status:
        return False
    settings.connection_status = status
    log_event(appmod, settings.tenant_id, 'status_change', source=source, user_id=user_id,
              method=settings.connection_method, from_status=previous, to_status=status,
              detail=reason)
    if alert and status == 'needs_attention':
        try:
            appmod.send_push_notification(
                {'title': 'WhatsApp needs attention',
                 'body': reason or 'Reconnect WhatsApp in Settings > Messaging.',
                 'url': '/settings'},
                tenant_id=settings.tenant_id, roles=['admin'], topic='whatsapp_inbox')
        except Exception:
            logging.exception('WhatsApp connection alert push failed for tenant_id=%s', settings.tenant_id)
    return True


# ---- Embedded Signup onboarding (research.md R-3) --------------------------

def _meta_error(resp):
    try:
        err = (resp.json() or {}).get('error') or {}
    except ValueError:
        err = {}
    code = err.get('code')
    message = _META_ERROR_HINTS.get(code) or err.get('error_user_msg') or err.get('message') \
        or 'Meta returned HTTP {}'.format(resp.status_code)
    return code, message


def _graph(method, path, *, what, token=None, params=None, json=None):
    headers = {'Authorization': 'Bearer {}'.format(token)} if token else None
    try:
        resp = getattr(requests, method)(graph_url(path), headers=headers, params=params, json=json,
                                         timeout=GRAPH_TIMEOUT)
    except requests.RequestException as exc:
        # Never echo str(exc): it contains the request URL, and the code
        # exchange / debug_token URLs carry the platform app secret.
        logging.warning('Meta request failed while %s: %s', what, type(exc).__name__)
        raise ConnectError(502, 'Could not reach Meta while {} ({}). Please try again.'.format(
            what, type(exc).__name__))
    if not resp.ok:
        code, message = _meta_error(resp)
        raise ConnectError(502, 'Meta rejected the request while {}: {}'.format(what, message), code)
    try:
        return resp.json() or {}
    except ValueError:
        return {}


def _granted_wabas(token):
    """WABA ids this business token can manage, per Meta's debug_token."""
    body = _graph('get', 'debug_token', what='verifying access',
                  params={'input_token': token,
                          'access_token': '{}|{}'.format(Config.META_APP_ID, Config.META_APP_SECRET)})
    for scope in (body.get('data') or {}).get('granular_scopes') or []:
        if scope.get('scope') == 'whatsapp_business_management':
            return {str(t) for t in scope.get('target_ids') or []}
    return set()


def _owned_elsewhere(appmod, tenant_id, phone_number_id, waba_id):
    S = appmod.WhatsAppSettings
    rows = S.query.filter(S.tenant_id != tenant_id).filter(
        (S.phone_number_id == phone_number_id) | (S.business_account_id == waba_id)).all()
    return any(is_routable(r) for r in rows)


def _has_connection(settings):
    if settings is None:
        return False
    if settings.connection_status in ('connected', 'needs_attention'):
        return True
    return bool(settings.access_token and settings.phone_number_id)


def complete_connection(appmod, tenant_id, user_id, *, method, code, waba_id, phone_number_id,
                        business_id=None):
    """Finish an Embedded Signup connection for `tenant_id`. Every Meta call
    runs before anything is written, so a failure leaves the tenant's existing
    configuration untouched (FR-008). Commits on success; raises ConnectError
    (with nothing written) on failure."""
    if method not in AUTOMATIC_METHODS:
        raise ConnectError(400, 'Unknown connection method.')
    if not (code and waba_id and phone_number_id):
        raise ConnectError(400, 'The Meta sign-in did not return a WhatsApp account and phone number.')
    waba_id, phone_number_id = str(waba_id), str(phone_number_id)

    token = _graph('get', 'oauth/access_token', what='signing in', params={
        'client_id': Config.META_APP_ID, 'client_secret': Config.META_APP_SECRET, 'code': code,
    }).get('access_token')
    if not token:
        raise ConnectError(400, 'Meta sign-in did not return an access token, please try again.')
    if waba_id not in _granted_wabas(token):
        raise ConnectError(403, 'This WhatsApp account was not shared with the platform during sign-in.')
    if _owned_elsewhere(appmod, tenant_id, phone_number_id, waba_id):
        raise ConnectError(409, 'This WhatsApp number is already connected to another account.')

    number = _graph('get', phone_number_id, token=token, what='reading the phone number',
                    params={'fields': _PHONE_FIELDS})
    if method == 'coexistence' and not number.get('is_on_biz_app'):
        raise ConnectError(502, 'This number is not a WhatsApp Business app number eligible for '
                                'coexistence. Try the "New number" option instead.')
    S = appmod.WhatsAppSettings
    settings = S.query.filter_by(tenant_id=tenant_id).first()
    pin = register_number(phone_number_id, token, settings) if method == 'embedded' else None
    _graph('post', '{}/subscribed_apps'.format(waba_id), token=token, what='subscribing to messages')
    waba = _graph('get', waba_id, token=token, what='reading the WhatsApp account',
                  params={'fields': 'name,primary_funding_id'})

    if settings is None:
        settings = S(tenant_id=tenant_id)
        appmod.db.session.add(settings)
    previous_method = settings.connection_method or 'manual'
    if not _has_connection(settings):
        event = 'connect'
    elif previous_method != method:
        event = 'method_change'
    else:
        event = 'reconnect'

    now = datetime.utcnow()
    if method == 'coexistence' and previous_method != 'coexistence':
        # The tenant answers from the phone app now; a canned reply would talk over them.
        settings.auto_reply_enabled = False
    settings.access_token = token
    settings.phone_number_id = phone_number_id
    settings.business_account_id = waba_id
    settings.app_id = None
    settings.app_secret = None
    settings.api_version = Config.META_GRAPH_VERSION
    settings.mode = 'api'
    settings.enabled = True
    if pin:
        settings.registration_pin = pin
    settings.connection_method = method
    settings.connection_status = 'connected'
    settings.status_reason = None
    settings.meta_business_id = business_id or settings.meta_business_id
    settings.meta_business_name = waba.get('name')
    settings.display_phone_number = number.get('display_phone_number')
    settings.verified_name = number.get('verified_name')
    settings.quality_rating = number.get('quality_rating')
    settings.messaging_limit_tier = number.get('whatsapp_business_manager_messaging_limit')
    # primary_funding_id is only documented for shared credit lines, so its
    # absence doesn't prove a card is missing (research.md R-3 step 7).
    settings.payment_method_status = 'present' if waba.get('primary_funding_id') else 'unknown'
    settings.token_obtained_at = now
    settings.connected_at = now
    settings.connected_by_user_id = user_id
    settings.last_health_check_at = now
    detail = number.get('display_phone_number') or phone_number_id
    if event == 'method_change':
        detail = '{} (was {})'.format(detail, previous_method)
    log_event(appmod, tenant_id, event, source='user', user_id=user_id, method=method,
              to_status='connected', detail=detail)
    appmod.db.session.commit()
    return settings


def register_number(phone_number_id, token, settings):
    """Register a new API number on Cloud API with a two-step-verification
    PIN (never a coexistence number -- Meta forbids it). A reconnect reuses
    the stored PIN, since changing it needs the old one. Returns the PIN."""
    pin = (settings.registration_pin if settings is not None else None) or \
        '{:06d}'.format(secrets.randbelow(10 ** 6))
    try:
        _graph('post', '{}/register'.format(phone_number_id), token=token, what='registering the number',
               json={'messaging_product': 'whatsapp', 'pin': pin})
    except ConnectError as exc:
        if exc.meta_code != 133016:
            exc.message += (' If this number is still active in the WhatsApp app, try the coexistence '
                            'option ("Use my WhatsApp Business app number") or a different number.')
        raise
    return pin


TEST_TEMPLATE = 'hello_world'   # Meta's sample template on newly created WhatsApp accounts


def send_test_message(settings, to_digits):
    """Send Meta's sample template to `to_digits` with the tenant's own
    connection (FR: "send a test message from Settings"). Raises ConnectError."""
    try:
        return _graph('post', '{}/messages'.format(settings.phone_number_id), token=settings.access_token,
                      what='sending the test message',
                      json={'messaging_product': 'whatsapp', 'to': to_digits, 'type': 'template',
                            'template': {'name': TEST_TEMPLATE, 'language': {'code': 'en_US'}}})
    except ConnectError as exc:
        exc.message += (' The test uses Meta\'s sample "{}" template; if your WhatsApp account no longer has it, '
                        'submit the standard templates and test with a real payment instead.').format(TEST_TEMPLATE)
        raise


# ---- health, reconnect, disconnect (US4, contracts/webhook-events.md) ------

NEEDS_ATTENTION_SEND_ERROR = 'WhatsApp connection needs attention -- reconnect in Settings.'
OFFBOARDED_REASON = 'The WhatsApp Business app on the phone was re-registered -- waiting for it to reconnect.'
OFFBOARDED_GRACE_MINUTES = 15
_DISCONNECT_REASONS = {
    'PRIMARY_INACTIVITY': 'WhatsApp Business app not opened for ~14 days, so Meta disconnected the number. '
                          'Open the app on the phone, then reconnect.',
    'COMPANION_INACTIVITY': 'The number was disconnected because a linked device was inactive for ~30 days.',
    'ACCOUNT_DISCONNECTED': 'The number was disconnected from the platform in the WhatsApp Business app.',
    'BUSINESS_DOWNGRADE': 'The number was moved off the WhatsApp Business app.',
    'CHANGE_NUMBER': 'The phone number was changed in the WhatsApp Business app.',
    'USER_RE_REGISTERED': 'The number was registered again on another phone.',
}
# account_update event -> (new status or None to keep, reason, push alert?)
_ACCOUNT_EVENTS = {
    'PARTNER_APP_UNINSTALLED': ('needs_attention', 'Access to WhatsApp was removed in Meta. Reconnect.', True),
    'PARTNER_REMOVED': ('needs_attention', 'The WhatsApp account was unshared from the platform. Reconnect.', True),
    'ACCOUNT_DELETED': ('needs_attention', 'The WhatsApp account was deleted in Meta.', True),
    'DISABLED_UPDATE': ('needs_attention', 'Meta disabled this WhatsApp account.', True),
    'ACCOUNT_OFFBOARDED': ('needs_attention', OFFBOARDED_REASON, False),
    'ACCOUNT_RECONNECTED': ('connected', None, False),
    'ACCOUNT_RESTRICTION': (None, 'Meta restricted this WhatsApp account -- check WhatsApp Manager.', True),
    'ACCOUNT_VIOLATION': (None, 'Meta reported a policy violation on this WhatsApp account.', True),
}
_QUALITY_EVENTS = {'FLAGGED': 'RED', 'UNFLAGGED': 'GREEN'}


def blocks_sends(settings):
    """An automatic connection known to be broken: don't call Meta at all."""
    return is_automatic(settings) and settings.connection_status == 'needs_attention'


def is_auth_error(meta_code):
    """Graph errors meaning the token no longer grants access."""
    return str(meta_code) in ('190', '200', '10')


def _alert_admins(appmod, settings, title, body):
    try:
        appmod.send_push_notification({'title': title, 'body': body, 'url': '/settings'},
                                      tenant_id=settings.tenant_id, roles=['admin'], topic='whatsapp_inbox')
    except Exception:
        logging.exception('WhatsApp connection alert push failed for tenant_id=%s', settings.tenant_id)


def note_send_error(appmod, settings, meta_code, message):
    """A send got a Graph error: an auth failure means the connection is
    broken. Commits when it changes the status."""
    if settings is None or not is_auth_error(meta_code):
        return False
    changed = set_connection_status(appmod, settings, 'needs_attention',
                                    'Meta refused to send: {}'.format(message or meta_code), 'send_error')
    if changed:
        appmod.db.session.commit()
    return changed


def handle_account_update(appmod, settings, value):
    """Apply one account_update webhook value. Caller commits."""
    event = value.get('event')
    rule = _ACCOUNT_EVENTS.get(event)
    if rule is None:
        logging.info('WhatsApp account_update %s for tenant_id=%s: no action', event, settings.tenant_id)
        return
    status, reason, alert = rule
    if event == 'PARTNER_REMOVED':
        why = (value.get('disconnection_info') or {}).get('reason')
        reason = _DISCONNECT_REASONS.get(why, reason)
    if status is None:
        settings.status_reason = reason
        log_event(appmod, settings.tenant_id, 'status_change', source='webhook',
                  method=settings.connection_method, from_status=settings.connection_status,
                  to_status=settings.connection_status, detail='{}: {}'.format(event, reason))
        if alert:
            _alert_admins(appmod, settings, 'WhatsApp account warning', reason)
        return
    set_connection_status(appmod, settings, status, reason, 'webhook', alert=alert)


def handle_quality_update(appmod, settings, value):
    """Apply one phone_number_quality_update webhook value. Caller commits."""
    event = value.get('event')
    if value.get('current_limit'):
        settings.messaging_limit_tier = value['current_limit']
    if event in _QUALITY_EVENTS:
        settings.quality_rating = _QUALITY_EVENTS[event]
    if event in ('FLAGGED', 'DOWNGRADE'):
        _alert_admins(appmod, settings, 'WhatsApp quality warning',
                      'Meta {} your WhatsApp number ({}). Check message quality in WhatsApp Manager.'.format(
                          'flagged' if event == 'FLAGGED' else 'lowered the messaging limit of',
                          settings.display_phone_number or settings.phone_number_id))


def check_health(appmod, settings, source='health_check'):
    """Ask Meta for the number with the stored token and update status and
    display fields. Commits. Returns True when healthy."""
    settings.last_health_check_at = datetime.utcnow()
    try:
        number = _graph('get', settings.phone_number_id, token=settings.access_token,
                        what='checking the connection', params={'fields': _PHONE_FIELDS})
    except ConnectError as exc:
        if settings.connection_status != 'needs_attention' or settings.status_reason != OFFBOARDED_REASON:
            set_connection_status(appmod, settings, 'needs_attention', exc.message, source)
        appmod.db.session.commit()
        return False
    for field, key in (('display_phone_number', 'display_phone_number'), ('verified_name', 'verified_name'),
                       ('quality_rating', 'quality_rating'),
                       ('messaging_limit_tier', 'whatsapp_business_manager_messaging_limit')):
        if number.get(key):
            setattr(settings, field, number[key])
    set_connection_status(appmod, settings, 'connected', None, source)
    appmod.db.session.commit()
    return True


def alert_if_stuck_offboarded(appmod, settings):
    """ACCOUNT_OFFBOARDED usually clears itself within minutes (ACCOUNT_RECONNECTED).
    Alert once if it hasn't after OFFBOARDED_GRACE_MINUTES. Commits."""
    if settings.connection_status != 'needs_attention' or settings.status_reason != OFFBOARDED_REASON:
        return False
    Ev = appmod.WhatsAppConnectionEvent
    since = Ev.query.filter_by(tenant_id=settings.tenant_id, event='status_change', to_status='needs_attention') \
        .order_by(Ev.created_at.desc(), Ev.id.desc()).first()
    if since is None or since.created_at > datetime.utcnow() - timedelta(minutes=OFFBOARDED_GRACE_MINUTES):
        return False
    if Ev.query.filter(Ev.tenant_id == settings.tenant_id, Ev.event == 'alert', Ev.created_at >= since.created_at).first():
        return False
    reason = 'The WhatsApp Business app number has not reconnected. Open the app on the phone, then reconnect.'
    log_event(appmod, settings.tenant_id, 'alert', source='health_check', method=settings.connection_method,
              detail=reason)
    _alert_admins(appmod, settings, 'WhatsApp needs attention', reason)
    appmod.db.session.commit()
    return True


def run_health_checks(appmod):
    """Hourly job body: every live automatic connection."""
    S = appmod.WhatsAppSettings
    rows = S.query.filter(S.connection_method.in_(AUTOMATIC_METHODS),
                          S.connection_status != 'disconnected').all()
    for settings in rows:
        try:
            if not (settings.access_token and settings.phone_number_id):
                continue
            check_health(appmod, settings)
            alert_if_stuck_offboarded(appmod, settings)
        except Exception:
            appmod.db.session.rollback()
            logging.exception('WhatsApp health check failed for tenant_id=%s', settings.tenant_id)


def disconnect(appmod, settings, user_id, next_method=None):
    """Stop automatic sending and forget the credentials; message history is
    kept. Never deregisters the number (forbidden for coexistence, and it
    would strand an embedded number). Commits."""
    if is_automatic(settings) and settings.access_token and settings.business_account_id:
        try:
            resp = requests.delete(graph_url('{}/subscribed_apps'.format(settings.business_account_id)),
                                   headers={'Authorization': 'Bearer {}'.format(settings.access_token)},
                                   timeout=GRAPH_TIMEOUT)
            if not resp.ok:
                logging.warning('WhatsApp disconnect: unsubscribe failed for tenant_id=%s: %s',
                                settings.tenant_id, _meta_error(resp)[1])
        except requests.RequestException as exc:
            logging.warning('WhatsApp disconnect: unsubscribe failed for tenant_id=%s: %s', settings.tenant_id, exc)
    previous = settings.connection_status
    method = settings.connection_method or 'manual'
    settings.access_token = None
    settings.registration_pin = None
    settings.token_obtained_at = None
    if method == 'manual':
        settings.app_secret = None
    settings.mode = 'deeplink'
    settings.connection_status = 'disconnected'
    settings.status_reason = None
    if next_method == 'manual':
        # The tenant is switching to their own Meta app; the settings form
        # only accepts credentials for manual connections.
        settings.connection_method = 'manual'
    log_event(appmod, settings.tenant_id, 'disconnect', source='user', user_id=user_id, method=method,
              from_status=previous, to_status='disconnected',
              detail='switching to manual configuration' if next_method == 'manual' and method != 'manual' else None)
    appmod.db.session.commit()
    return settings
