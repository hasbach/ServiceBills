"""On-prem runtime: deployment mode, route-family guard, system info, installed
license state, read-only enforcement and /api/license endpoints.

Later tasks extend this module (setup wizard).
"""
import logging
import os
import time
from datetime import date, datetime, timedelta

import requests
from flask import current_app, jsonify, request
from flask_jwt_extended import jwt_required

import license as lic
from config import Config

log = logging.getLogger(__name__)

# Route families that exist only in the SaaS deployment.
ONPREM_BLOCKED = ("/api/register", "/api/admin/", "/api/billing/", "/api/stripe/",
                  "/api/licenses/", "/api/internal/")

# Writes to these prefixes stay allowed while the license is read-only.
READONLY_ALLOW = ("/api/login", "/api/logout", "/api/setup", "/api/license", "/api/system/info")
CACHE_SECONDS = 60

_pubkey_cache = None
_state_cache = None  # (timestamp, LicenseState)


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


# ---------------------------------------------------------------- license state

def public_key():
    global _pubkey_cache
    k = current_app.config.get("LICENSE_PUBLIC_KEY")
    if k:
        return k
    if _pubkey_cache is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "license_pubkey.pem")
        with open(path) as f:
            _pubkey_cache = f.read()
    return _pubkey_cache


def invalidate():
    global _state_cache
    _state_cache = None


def _machine_id():
    return _cfg("MACHINE_ID")


def _row(create=False):
    from app import db, InstalledLicense
    row = db.session.get(InstalledLicense, 1)
    if row is None and create:
        row = InstalledLicense(id=1, revoked=False)
        db.session.add(row)
    return row


def _bump_last_seen(ts):
    """Write last_seen_at on an independent connection, never touching db.session."""
    from app import db, InstalledLicense
    try:
        t = InstalledLicense.__table__
        with db.engine.begin() as conn:
            conn.execute(t.update().where(t.c.id == 1).values(last_seen_at=ts))
    except Exception as e:
        log.warning("could not update license last_seen_at: %s", e)


def _compute_state():
    from app import db
    with db.session.no_autoflush:  # never flush pending objects of the calling request
        row = _row()
    if row is None or not row.license_text:
        return lic.evaluate(None, date.today(), _machine_id())
    try:
        payload = lic.verify(row.license_text, public_key())
    except lic.LicenseError:
        return lic.LicenseState("readonly", "invalid_signature")
    now = datetime.utcnow()
    if row.last_seen_at and now < row.last_seen_at - timedelta(days=1):
        base = payload.get("base") or {}
        return lic.LicenseState("readonly", "clock_rollback", payload, set(),
                                base.get("expires_at"), [])
    if row.last_seen_at is None or now - row.last_seen_at > timedelta(minutes=1):
        _bump_last_seen(max(now, row.last_seen_at) if row.last_seen_at else now)
    return lic.evaluate(payload, date.today(), _machine_id(),
                        release_date=_cfg("APP_RELEASE_DATE"), revoked=bool(row.revoked))


def current_state():
    global _state_cache
    if _state_cache and time.time() - _state_cache[0] < CACHE_SECONDS:
        return _state_cache[1]
    st = _compute_state()
    _state_cache = (time.time(), st)
    return st


def store_license(text, from_server=False):
    from app import db
    payload = lic.verify(text, public_key())
    if payload.get("machine_id") != _machine_id():
        raise lic.LicenseError("machine_mismatch")
    row = _row(create=True)
    if row.revoked and not from_server and row.license_text:
        try:
            old = lic.verify(row.license_text, public_key())
        except lic.LicenseError:
            old = {}
        if old.get("license_id") == payload.get("license_id"):
            raise lic.LicenseError("revoked")
    row.license_text = text
    row.revoked = False
    db.session.commit()
    invalidate()
    return current_state()


def refresh_license():
    """Best-effort refresh against the license server. Never raises."""
    from app import db
    try:
        row = _row()
        if row is None or not row.license_text:
            return
        payload = lic.verify(row.license_text, public_key())
        try:
            resp = requests.post(
                f"{_cfg('LICENSE_SERVER_URL')}/api/licenses/refresh",
                json={"license_id": payload.get("license_id"), "machine_id": _machine_id(),
                      "app_version": _cfg("APP_VERSION")},
                timeout=15)
            if resp.status_code == 200:
                store_license(resp.json()["license"], from_server=True)
                row = _row()
                row.last_refresh_error = None
                row.last_refresh_at = datetime.utcnow()
            elif resp.status_code == 403 and (resp.json() or {}).get("error") == "revoked":
                row.revoked = True
                row.last_refresh_error = None
                row.last_refresh_at = datetime.utcnow()
            else:
                row.last_refresh_error = f"{resp.status_code}: {(resp.text or '')[:200]}"[:500]
        except Exception as e:
            row = _row()
            row.last_refresh_error = f"{type(e).__name__}: {str(e)[:200]}"[:500]
        db.session.commit()
    except Exception:
        log.exception("license refresh failed")
        try:
            db.session.rollback()
        except Exception:
            pass
    finally:
        invalidate()


def _license_provider(tenant):
    if not is_onprem():
        return None
    st = current_state()
    if st.state == "valid":
        return {"modules": {k: {"expires_at": "9999-12-31"} for k in st.active_modules}}
    return {"modules": {}}


def _readonly_guard():
    if not is_onprem() or request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    p = request.path
    if not p.startswith("/api/") or any(p == a or p.startswith(a + "/") for a in READONLY_ALLOW):
        return None
    from app import Tenant
    if Tenant.query.first() is None:  # setup phase
        return None
    st = current_state()
    if st.state == "readonly":
        return jsonify({"license_readonly": True, "reason": st.reason}), 403


def license_body():
    st = current_state()
    payload = st.payload or {}
    row = _row()
    base = payload.get("base")
    mods = {k: {"term": v.get("term"), "expires_at": v.get("expires_at"), "active": k in st.active_modules}
            for k, v in (payload.get("modules") or {}).items()}
    return {"state": st.state, "reason": st.reason,
            "business_name": payload.get("business_name"),
            "trial": bool(payload.get("trial")),
            "base": ({"term": base.get("term"), "expires_at": base.get("expires_at")} if base else None),
            "modules": mods, "warnings": st.warnings,
            "last_refresh_at": row.last_refresh_at.isoformat() if row and row.last_refresh_at else None,
            "last_refresh_error": row.last_refresh_error if row else None,
            "machine_id": _machine_id()}


def system_info():
    onprem = is_onprem()
    license_info = None
    if onprem:
        if not _cfg("MACHINE_ID"):
            license_info = {"state": "readonly", "reason": "machine_mismatch",
                            "base_expires_at": None, "trial": False}
        else:
            st = current_state()
            license_info = {"state": st.state, "reason": st.reason,
                            "base_expires_at": st.base_expires_at,
                            "trial": bool((st.payload or {}).get("trial"))}
    return {
        "deployment_mode": "onprem" if onprem else "saas",
        "app_version": _cfg("APP_VERSION"),
        "setup_required": False,
        "license": license_info,
    }


def register(app, appmod):
    import modules
    # Insert at the front so the guard runs before _block_suspended_tenants.
    funcs = app.before_request_funcs.setdefault(None, [])
    funcs.insert(0, _deployment_mode_guard)
    funcs.insert(1, _readonly_guard)
    modules.license_provider = _license_provider

    if is_onprem() and not Config.MACHINE_ID:
        log.error("DEPLOYMENT_MODE=onprem but MACHINE_ID is not set; license will report machine_mismatch")

    @app.route("/api/system/info", methods=["GET"])
    def api_system_info():
        return jsonify(system_info())

    @app.route("/api/license", methods=["GET"])
    @jwt_required()
    @appmod.admin_required()
    def api_license_get():
        return jsonify(license_body())

    @app.route("/api/license", methods=["POST"])
    @jwt_required()
    @appmod.admin_required()
    def api_license_set():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = {}
        for k in ("license_file", "license_key"):
            if k in data and data[k] is not None and not isinstance(data[k], str):
                return jsonify(msg="invalid_license"), 400
        try:
            if data.get("license_file"):
                store_license(data["license_file"])
            elif data.get("license_key"):
                try:
                    resp = requests.post(
                        f"{_cfg('LICENSE_SERVER_URL')}/api/licenses/activate",
                        json={"license_key": data["license_key"], "machine_id": _machine_id()},
                        timeout=15)
                except requests.RequestException:
                    return jsonify(msg="Could not reach the license server. Upload a license file instead."), 502
                if resp.status_code != 200:
                    try:
                        code = resp.json().get("error") or "activation_failed"
                    except Exception:
                        code = "activation_failed"
                    return jsonify(msg=code), 400
                store_license(resp.json()["license"], from_server=True)
            else:
                return jsonify(msg="license_key or license_file required"), 400
        except lic.LicenseError as e:
            msg = str(e) if str(e) in ("machine_mismatch", "revoked") else "invalid_license"
            return jsonify(msg=msg), 400
        return jsonify(license_body())

    @app.route("/api/license/refresh", methods=["POST"])
    @jwt_required()
    @appmod.admin_required()
    def api_license_refresh():
        refresh_license()
        return jsonify(license_body())
