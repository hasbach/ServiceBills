"""WhatsApp automatic connection through the platform Meta app (Embedded
Signup: coexistence or a new API number), alongside the existing manual
per-tenant credentials. See specs/001-whatsapp-auto-connect/.

Like whatsapp_inbox.py, every function that touches the DB takes the app
module (`appmod`) first, so this module never imports app.py at import time.
Functions here do NOT commit unless their docstring says so.
"""
import logging

import onprem
from config import Config

AUTOMATIC_METHODS = ('coexistence', 'embedded')
STATUSES = ('connected', 'needs_attention', 'disconnected')


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
