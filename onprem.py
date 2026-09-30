"""On-prem runtime: deployment mode, route-family guard, system info.

Later tasks extend this module (license state, setup wizard).
"""
import logging

from flask import current_app, jsonify, request

from config import Config

log = logging.getLogger(__name__)

# Route families that exist only in the SaaS deployment.
ONPREM_BLOCKED = ("/api/register", "/api/admin/", "/api/billing/", "/api/stripe/",
                  "/api/licenses/", "/api/internal/")


def _cfg(key):
    try:
        return current_app.config.get(key, getattr(Config, key, None))
    except RuntimeError:  # no app context
        return getattr(Config, key, None)


def is_onprem():
    """True when running as an on-prem install. Read at call time."""
    return (_cfg("DEPLOYMENT_MODE") or "saas").strip().lower() == "onprem"


def _saas_blocked(path):
    """Paths that exist only on-prem (setup wizard, local license)."""
    return (path == "/api/setup" or path.startswith("/api/setup/")
            or path == "/api/license" or path.startswith("/api/license/"))


def _deployment_mode_guard():
    p = request.path
    if is_onprem():
        if any(p == x.rstrip("/") or p.startswith(x) for x in ONPREM_BLOCKED):
            return jsonify({"error": "not found"}), 404
    elif _saas_blocked(p):
        return jsonify({"error": "not found"}), 404


def system_info():
    onprem = is_onprem()
    license_info = None
    if onprem and not _cfg("MACHINE_ID"):
        license_info = {"state": "readonly", "reason": "machine_mismatch",
                        "base_expires_at": None, "trial": False}
    return {
        "deployment_mode": "onprem" if onprem else "saas",
        "app_version": _cfg("APP_VERSION"),
        "setup_required": False,
        "license": license_info,
    }


def register(app, appmod):
    # Insert at the front so the guard runs before _block_suspended_tenants.
    app.before_request_funcs.setdefault(None, []).insert(0, _deployment_mode_guard)

    if is_onprem() and not Config.MACHINE_ID:
        log.error("DEPLOYMENT_MODE=onprem but MACHINE_ID is not set; license will report machine_mismatch")

    @app.route("/api/system/info", methods=["GET"])
    def api_system_info():
        return jsonify(system_info())
