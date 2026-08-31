"""RouterOS API adapter for MikrotikServer (self-hosted local PPPoE) and
NetworkDevice (on-demand device-health monitoring, e.g. a core CCR) -- the
two models are independent (see docs/superpowers/specs/2026-09-01-network-
device-health-monitoring-design.md for why), but both are plain RouterOS
routers reached the same way, so they're duck-typed on the same connection
fields (host/username/password/api_port/use_tls/last_checked_at/last_status)
and share this one adapter.

See docs/superpowers/specs/2026-08-12-network-enforcement-design.md, Concept B.

Every /ppp/secret lookup filters by (name, service) together whenever the
server has a service_name configured. RouterOS allows duplicate secret `name`
values across different `service` values -- this happens for real on
infrastructure shared by more than one ISP, so two unrelated ISPs can each
have a subscriber named e.g. "user1". Never look up a secret by name alone
once service_name is set, or an action can land on a different ISP's customer.

Every public function here catches connection/auth/protocol failures and
returns a failure result instead of raising. A router being offline must
never block or crash a billing action that happens to call into this module --
callers decide what to do with a failure result (surface it to staff, log it),
this module only guarantees it never raises out of these calls.
"""
import logging
import ssl
from datetime import datetime

import librouteros
from librouteros.exceptions import LibRouterosError
from librouteros.query import And, Key

logger = logging.getLogger(__name__)

# OSError covers socket-level failures (refused, timeout, unreachable host,
# DNS failure). LibRouterosError covers RouterOS-level failures (bad login,
# malformed protocol, !trap/!fatal replies). Together these are every way a
# call below can fail without it being a bug in this module.
_CONNECTION_ERRORS = (OSError, LibRouterosError)


def _connect(server):
    kwargs = dict(
        host=server.host,
        username=server.username,
        password=server.password,
        port=server.api_port,
        timeout=8,
    )
    if server.use_tls:
        ctx = ssl.create_default_context()
        # RouterOS's API-SSL typically presents a self-signed certificate;
        # this connects over TLS for transport encryption without requiring
        # the tenant to install a real CA-signed cert on their own router.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_wrapper"] = ctx.wrap_socket
    return librouteros.connect(**kwargs)


def _safe_close(api):
    try:
        api.close()
    except Exception:
        pass


def _mark_checked(server, status):
    server.last_checked_at = datetime.utcnow()
    server.last_status = status


def _classify_error(exc):
    """'auth_failed' if we spoke the RouterOS protocol but it rejected us
    (bad credentials, permission trap, ...); 'unreachable' if we never even
    got a socket-level connection (refused, timeout, DNS, ...)."""
    return "auth_failed" if isinstance(exc, LibRouterosError) else "unreachable"


def _secret_where(pppoe_username, service_name):
    name_eq = Key("name") == pppoe_username
    if not service_name:
        return (name_eq,)
    return (And(name_eq, Key("service") == service_name),)


def _find_secret(api, pppoe_username, service_name):
    """Return the matching /ppp/secret row (dict) or None. Raises on
    transport/protocol failure -- callers always run this inside a try."""
    secrets = api.path("ppp", "secret")
    rows = list(secrets.select().where(*_secret_where(pppoe_username, service_name)))
    return rows[0] if rows else None


def test_connection(server):
    """Open a connection and run a trivial read.

    Side effect: sets server.last_checked_at / server.last_status -- caller
    is responsible for committing.

    Returns (ok: bool, message: str).
    """
    try:
        api = _connect(server)
        try:
            list(api.path("system", "identity").select())
        finally:
            _safe_close(api)
        _mark_checked(server, "online")
        return True, "Connected successfully."
    except _CONNECTION_ERRORS as e:
        _mark_checked(server, _classify_error(e))
        logger.warning("Mikrotik test_connection failed for server %s: %s", server.id, e)
        return False, str(e) or e.__class__.__name__


def get_secret_status(server, pppoe_username):
    """Look up one customer's /ppp/secret.

    Returns (ok: bool, status_or_message: str). On success, status_or_message
    is one of 'enabled', 'disabled', 'not_found'. On failure it is a
    human-readable error message.
    """
    try:
        api = _connect(server)
        try:
            row = _find_secret(api, pppoe_username, server.service_name)
        finally:
            _safe_close(api)
        if row is None:
            return True, "not_found"
        return True, ("disabled" if row.get("disabled") else "enabled")
    except _CONNECTION_ERRORS as e:
        logger.warning("Mikrotik get_secret_status failed for server %s: %s", server.id, e)
        return False, str(e) or e.__class__.__name__


def set_secret_enabled(server, pppoe_username, enabled):
    """Enable or disable one customer's /ppp/secret -- the live action behind
    the Suspend/Unsuspend buttons. Staff-triggered only; nothing in this
    module calls this on its own (see the spec's "Suspend stays
    staff-confirmed" section).

    Returns (ok: bool, message: str).
    """
    try:
        api = _connect(server)
        try:
            row = _find_secret(api, pppoe_username, server.service_name)
            if row is None:
                scope = f" (service '{server.service_name}')" if server.service_name else ""
                return False, f"No matching secret found for '{pppoe_username}'{scope}."
            api.path("ppp", "secret").update(**{".id": row[".id"], "disabled": not enabled})
        finally:
            _safe_close(api)
        return True, f"Secret {'enabled' if enabled else 'disabled'} successfully."
    except _CONNECTION_ERRORS as e:
        logger.warning("Mikrotik set_secret_enabled failed for server %s: %s", server.id, e)
        return False, str(e) or e.__class__.__name__


def get_active_session(server, pppoe_username):
    """Look up whether a customer currently has a live PPPoE session.

    NOTE: /ppp/active is filtered by name only, not (name, service) -- unlike
    the /ppp/secret functions above. This is a read-only display feature (an
    optional "currently connected" indicator), not a mutating action, so a
    wrong result here is a wrong tooltip, never a wrongly suspended
    connection. get_secret_status / set_secret_enabled remain the
    authoritative, correctly-scoped operations for anything that matters.

    Returns (ok: bool, session_or_message). On success, session_or_message is
    a dict of the active session's fields, or None if not currently
    connected. On failure it is a human-readable error message.
    """
    try:
        api = _connect(server)
        try:
            rows = list(api.path("ppp", "active").select().where(Key("name") == pppoe_username))
        finally:
            _safe_close(api)
        return True, (rows[0] if rows else None)
    except _CONNECTION_ERRORS as e:
        logger.warning("Mikrotik get_active_session failed for server %s: %s", server.id, e)
        return False, str(e) or e.__class__.__name__


def get_device_health(server):
    """Read this device's identity, uptime, and interface list.

    Deliberately generic -- returns whatever interfaces RouterOS reports
    rather than filtering/renaming them. Nobody has confirmed the exact
    interface-to-upstream mapping (VLAN vs physical port) in advance, so
    staff label the raw names via the app UI after seeing a first result,
    rather than this module guessing at a mapping.

    Side effect: sets server.last_checked_at / server.last_status -- caller
    is responsible for committing.

    Returns (ok: bool, health_or_message). On success, health_or_message is
    a dict: {"identity": str, "uptime": str, "interfaces": [{"name": str,
    "running": bool, "disabled": bool}, ...]}. On failure it is a
    human-readable error message.
    """
    try:
        api = _connect(server)
        try:
            identity_rows = list(api.path("system", "identity").select())
            resource_rows = list(api.path("system", "resource").select())
            interface_rows = list(api.path("interface").select())
        finally:
            _safe_close(api)
        health = {
            "identity": identity_rows[0]["name"] if identity_rows else "",
            "uptime": resource_rows[0].get("uptime", "") if resource_rows else "",
            "interfaces": [
                {
                    "name": row.get("name", ""),
                    "running": bool(row.get("running")),
                    "disabled": bool(row.get("disabled")),
                }
                for row in interface_rows
            ],
        }
        _mark_checked(server, "online")
        return True, health
    except _CONNECTION_ERRORS as e:
        _mark_checked(server, _classify_error(e))
        logger.warning("Mikrotik get_device_health failed for server %s: %s", server.id, e)
        return False, str(e) or e.__class__.__name__
