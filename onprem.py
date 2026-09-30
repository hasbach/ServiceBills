"""On-prem runtime: deployment mode, route-family guard, system info, installed
license state, read-only enforcement and /api/license endpoints.

Operational requirements for on-prem deployments:
  * The license state cache is per-process, but each entry is tagged with the
    InstalledLicense.revision it was computed from, so workers notice changes made
    by other workers on the next call.
  * Every container (app, scheduler, ...) must run with the identical TZ setting:
    expiry is evaluated against the local calendar date (date.today()).
"""
import json
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
                  "/api/licenses/", "/api/internal/", "/api/updates/")
# Exact-match SaaS-only paths (a prefix match would be too broad for SPA routes).
ONPREM_BLOCKED_EXACT = ("/download",)

# Writes to these prefixes stay allowed while the license is read-only.
READONLY_ALLOW = ("/api/login", "/api/logout", "/api/setup", "/api/license", "/api/system/info",
                  "/api/forgot-password", "/api/reset-password", "/api/whatsapp/webhook")
CACHE_SECONDS = 60

_pubkey_cache = None
_state_cache = None  # (timestamp, revision, LicenseState)


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
        if p in ONPREM_BLOCKED_EXACT or any(p == x.rstrip("/") or p.startswith(x) for x in ONPREM_BLOCKED):
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
        row = InstalledLicense(id=1, revoked=False, revision=0)
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
    issued = None
    try:
        issued = datetime.strptime(payload.get("issued_at") or "", "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        pass
    if (issued and now < issued - timedelta(days=1)) or \
            (row.last_seen_at and now < row.last_seen_at - timedelta(days=1)):
        base = payload.get("base") or {}
        return lic.LicenseState("readonly", "clock_rollback", payload, set(),
                                base.get("expires_at"), [])
    if row.last_seen_at is None or now - row.last_seen_at > timedelta(minutes=1):
        _bump_last_seen(max(now, row.last_seen_at) if row.last_seen_at else now)
    return lic.evaluate(payload, date.today(), _machine_id(),
                        release_date=_cfg("APP_RELEASE_DATE"), revoked=bool(row.revoked))


def _db_revision():
    """Current revision of the license row (-1 when there is none). One cheap query;
    lets every worker notice a license change made by another worker."""
    from app import db, InstalledLicense
    with db.session.no_autoflush:
        rev = db.session.query(InstalledLicense.revision).filter_by(id=1).scalar()
    return -1 if rev is None else rev


def current_state():
    global _state_cache
    if not _machine_id():  # never licensed without a machine identity
        return lic.LicenseState("readonly", "machine_mismatch")
    rev = _db_revision()
    c = _state_cache
    if c and time.time() - c[0] < CACHE_SECONDS and c[1] == rev:
        return c[2]
    st = _compute_state()
    _state_cache = (time.time(), rev, st)
    return st


def store_license(text, from_server=False, commit=True):
    from app import db
    payload = lic.verify(text, public_key())
    if not _machine_id() or not payload.get("machine_id") or payload.get("machine_id") != _machine_id():
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
    row.revision = (row.revision or 0) + 1
    if from_server:
        # A license fresh from the server proves the server's clock: forgive an
        # earlier local clock jump by letting last_seen_at move backward, here only.
        row.last_seen_at = datetime.utcnow()
    if not commit:  # caller owns the transaction; state is recomputed after its commit
        db.session.flush()
        invalidate()
        return None
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
            elif (resp.status_code, (resp.json() or {}).get("error")) in (
                    (403, "revoked"), (409, "license_in_use"), (404, "unknown_license")):
                # revoked, moved to another computer (unbind), or deleted server-side
                row.revoked = True
                row.revision = (row.revision or 0) + 1
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


def _setup_required():
    if not is_onprem():
        return False
    from app import Tenant
    return Tenant.query.first() is None


def _setup_required_guard():
    if not is_onprem():
        return None
    p = request.path
    if not p.startswith("/api/") or p.startswith("/api/setup") or p.startswith("/api/system/info"):
        return None
    if _setup_required():
        return jsonify({"setup_required": True}), 409


def _fetch_license_text(data, business_name, owner_phone):
    """Returns (text, error_response). Exactly one is None."""
    mode = data.get("mode")
    if mode == "file":
        text = data.get("license_file")
        if not isinstance(text, str) or not text.strip():
            return None, (jsonify(msg="license_file required"), 400)
        return text, None
    if mode == "trial":
        path, body = "trial", {"business_name": business_name, "owner_phone": owner_phone,
                               "machine_id": _machine_id()}
    elif mode == "activate":
        key = data.get("license_key")
        if not isinstance(key, str) or not key.strip():
            return None, (jsonify(msg="license_key required"), 400)
        path, body = "activate", {"license_key": key, "machine_id": _machine_id()}
    else:
        return None, (jsonify(msg="invalid mode"), 400)
    try:
        resp = requests.post(f"{_cfg('LICENSE_SERVER_URL')}/api/licenses/{path}", json=body, timeout=15)
    except requests.RequestException:
        return None, (jsonify(msg="No internet connection \u2014 the trial needs internet. "
                                  "You can upload a license file instead."), 502)
    try:
        j = resp.json() or {}
    except Exception:
        j = {}
    if resp.status_code in (200, 201) and isinstance(j.get("license"), str):
        return j["license"], None
    code = j.get("error") or "activation_failed"
    if resp.status_code == 409 and code == "trial_already_used":
        return None, (jsonify(msg="trial_already_used"), 409)
    if 400 <= resp.status_code < 500:
        return None, (jsonify(msg=code), 400)
    return None, (jsonify(msg="License server error"), 502)


def update_status():
    """Installer/updater status from the host-mounted state.json, or None."""
    path = _cfg("ONPREM_STATE_FILE") or os.environ.get("ONPREM_STATE_FILE")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
    except (OSError, ValueError) as e:
        log.debug("cannot read update state file %s: %s", path, e)
        return None
    return {"current": data.get("current"), "previous": data.get("previous"),
            "last_update": data.get("last_update")}


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
    info = {
        "deployment_mode": "onprem" if onprem else "saas",
        "app_version": _cfg("APP_VERSION"),
        "setup_required": _setup_required(),
        "license": license_info,
    }
    if onprem:
        info["update"] = update_status()
    return info


def register(app, appmod):
    import modules
    # Insert at the front so the guard runs before _block_suspended_tenants.
    funcs = app.before_request_funcs.setdefault(None, [])
    funcs.insert(0, _deployment_mode_guard)
    funcs.insert(1, _setup_required_guard)
    funcs.insert(2, _readonly_guard)
    modules.license_provider = _license_provider

    if is_onprem() and not Config.MACHINE_ID:
        log.error("DEPLOYMENT_MODE=onprem but MACHINE_ID is not set; license will report machine_mismatch")

    @app.route("/api/system/info", methods=["GET"])
    def api_system_info():
        return jsonify(system_info())

    @app.route("/api/setup/status", methods=["GET"])
    def api_setup_status():
        return jsonify({"setup_required": _setup_required(), "machine_id_present": bool(_machine_id())})

    @app.route("/api/setup", methods=["POST"])
    def api_setup():
        db = appmod.db
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = {}
        if not _setup_required():
            return jsonify(msg="Setup already completed"), 409
        if not _machine_id():
            return jsonify(msg="machine_id_missing"), 400
        vals = {k: data.get(k) for k in ("business_name", "username", "password", "owner_phone")}
        if not all(isinstance(v, str) and v.strip() for v in vals.values()):
            return jsonify(msg="business_name, username, password and owner_phone are required"), 400
        if len(vals["password"]) < 6:
            return jsonify(msg="Password must be at least 6 characters"), 400
        if appmod.User.query.filter_by(username=vals["username"]).first():
            return jsonify(msg="Username already exists"), 400
        text, err = _fetch_license_text(data, vals["business_name"].strip(), vals["owner_phone"].strip())
        if err:
            return err
        try:
            tenant, _user = appmod._create_tenant_with_admin(
                vals["business_name"].strip(), vals["username"], vals["password"], plan="pro")
            # Seed the business profile so the header/receipts show the real name
            # instead of the "Default Business" placeholder.
            db.session.add(appmod.BusinessSettings(
                tenant_id=tenant.id, business_name=vals["business_name"].strip()[:200],
                address="", mobile=vals["owner_phone"].strip()[:20]))
            store_license(text, from_server=data.get("mode") != "file", commit=False)
            # a concurrent setup may have created a tenant since our first check
            if appmod.Tenant.query.filter(appmod.Tenant.id != tenant.id).first() is not None:
                db.session.rollback()
                return jsonify(msg="Setup already completed"), 409
            db.session.commit()
        except lic.LicenseError as e:
            db.session.rollback()
            msg = str(e) if str(e) in ("machine_mismatch", "revoked") else "invalid_license"
            return jsonify(msg=msg), 400
        except Exception:
            db.session.rollback()
            log.exception("setup failed")
            return jsonify(msg="Setup failed"), 500
        invalidate()
        return jsonify(msg="ok", license=license_body()), 201

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
