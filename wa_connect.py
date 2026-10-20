"""WhatsApp automatic connection through the platform Meta app (Embedded
Signup: coexistence or a new API number), alongside the existing manual
per-tenant credentials. See specs/001-whatsapp-auto-connect/.

Like whatsapp_inbox.py, every function that touches the DB takes the app
module (`appmod`) first, so this module never imports app.py at import time.
Functions here do NOT commit unless their docstring says so.
"""
import logging
from datetime import datetime

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


def automatic_available():
    """Automatic connect needs the platform app configured, and a public SaaS
    domain allow-listed in its Login configuration -- so never on-prem."""
    return bool(Config.META_APP_ID and Config.META_APP_SECRET and Config.META_ES_CONFIG_ID) \
        and not onprem.is_onprem()


def coexistence_available():
    return automatic_available() and bool(Config.META_COEXISTENCE_ENABLED)


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
        raise ConnectError(502, 'Could not reach Meta while {}: {}'.format(what, exc))
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
    """Register a new API number with a two-step PIN (never a coexistence
    number -- Meta forbids it). Filled in by US2; returns the PIN to store."""
    return None
