"""SaaS-side license server: public trial / activate / refresh endpoints."""
import os
import re
import secrets
import uuid
from datetime import date, datetime

from flask import current_app, jsonify, request

import license as lic
import modules as modmod
from tenancy import superadmin_required

TERMS = ("monthly", "yearly", "lifetime")


def build_payload(row):
    return {"license_id": row.id, "business_name": row.business_name, "machine_id": row.machine_id,
            "trial": bool(row.trial), "base": {"term": row.base_term, "expires_at": row.base_expires_at},
            "modules": row.modules or {}, "issued_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}


def _signing_key():
    return os.environ.get("LICENSE_SIGNING_KEY") or current_app.config.get("LICENSE_SIGNING_KEY")


def signed_license(row):
    return lic.sign(build_payload(row), _signing_key())


def normalize_phone(v):
    """Digits only, international 00 prefix dropped (so +961.. and 00961.. agree)."""
    d = re.sub(r"\D", "", v or "")
    if d.startswith("00"):
        d = d[2:]
    return d[:40]


def _same_phone(a, b):
    a, b = normalize_phone(a), normalize_phone(b)
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 8 and len(b) >= 8 and a[-8:] == b[-8:]


def _body():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _bad_types(d, keys):
    """True if any of `keys` is present, non-null and not a string."""
    return any(d.get(k) is not None and not isinstance(d.get(k), str) for k in keys)


def register(app, appmod):
    db = appmod.db
    Model = appmod.OnpremLicense
    limiter = appmod.limiter

    def _new_key():
        while True:
            key = "SB-" + "-".join(secrets.token_hex(2).upper() for _ in range(3))
            if not Model.query.filter_by(license_key=key).first():
                return key

    def _not_configured():
        return jsonify({"error": "license server not configured"}), 503

    @app.route("/api/licenses/trial", methods=["POST"])
    @limiter.limit("10 per minute")
    def license_trial():
        if not _signing_key():
            return _not_configured()
        d = _body()
        if _bad_types(d, ("business_name", "machine_id", "owner_phone")):
            return jsonify({"error": "invalid field type"}), 400
        name = (d.get("business_name") or "").strip()
        machine = (d.get("machine_id") or "").strip()
        if not name or not machine:
            return jsonify({"error": "business_name and machine_id are required"}), 400
        if len(machine) > 128:
            return jsonify({"error": "machine_id too long"}), 400
        phone = normalize_phone(d.get("owner_phone"))
        if not phone:
            return jsonify({"error": "owner_phone is required"}), 400
        now = datetime.utcnow()
        existing = Model.query.filter_by(machine_id=machine).first()
        if existing:
            # Same machine + same phone + a live, never-superseded trial: hand the
            # same license back so a failed local setup can simply be retried.
            if (existing.trial and not existing.revoked and _same_phone(existing.owner_phone, phone)
                    and existing.base_expires_at >= now.date().isoformat()):
                return jsonify({"license": signed_license(existing)}), 200
            return jsonify({"error": "trial_already_used"}), 409
        for other in Model.query.filter(Model.owner_phone.isnot(None)).all():
            if _same_phone(other.owner_phone, phone):
                return jsonify({"error": "trial_already_used"}), 409
        row = Model(id=str(uuid.uuid4()), license_key=_new_key(), business_name=name[:200],
                    owner_phone=phone, machine_id=machine, trial=True,
                    base_term="trial", base_expires_at=lic.add_term(now.date(), "trial").isoformat(),
                    modules={}, activated_at=now)
        db.session.add(row)
        db.session.commit()
        return jsonify({"license": signed_license(row)}), 201

    @app.route("/api/licenses/activate", methods=["POST"])
    @limiter.limit("10 per minute")
    def license_activate():
        if not _signing_key():
            return _not_configured()
        d = _body()
        if _bad_types(d, ("machine_id", "license_key")):
            return jsonify({"error": "invalid field type"}), 400
        machine = (d.get("machine_id") or "").strip()
        row = Model.query.filter_by(license_key=(d.get("license_key") or "").strip()).first()
        if not row:
            return jsonify({"error": "unknown_license"}), 404
        if row.revoked:
            return jsonify({"error": "revoked"}), 403
        if not machine:
            return jsonify({"error": "machine_id is required"}), 400
        if len(machine) > 128:
            return jsonify({"error": "machine_id too long"}), 400
        if row.machine_id and row.machine_id != machine:
            return jsonify({"error": "license_in_use"}), 409
        row.machine_id = machine
        if not row.activated_at:
            row.activated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"license": signed_license(row)}), 200

    @app.route("/api/licenses/refresh", methods=["POST"])
    @limiter.limit("10 per minute")
    def license_refresh():
        if not _signing_key():
            return _not_configured()
        d = _body()
        if _bad_types(d, ("license_id", "app_version")):
            return jsonify({"error": "invalid field type"}), 400
        machine = d.get("machine_id")
        if not isinstance(machine, str) or not machine.strip() or len(machine) > 128:
            return jsonify({"error": "machine_id is required"}), 400
        row = db.session.get(Model, d.get("license_id") or "")
        if not row:
            return jsonify({"error": "unknown_license"}), 404
        if row.revoked:
            return jsonify({"error": "revoked"}), 403
        if row.machine_id is None or row.machine_id != machine:
            return jsonify({"error": "license_in_use"}), 409
        row.last_refresh_at = datetime.utcnow()
        row.last_app_version = ((d.get("app_version") or "")[:40] or None)
        db.session.commit()
        return jsonify({"license": signed_license(row)}), 200

    # ---- super-admin management API -------------------------------------
    def _iso(dt):
        return dt.isoformat() + "Z" if dt else None

    def row_dict(row):
        if row.revoked:
            status = "revoked"
        elif row.base_expires_at < date.today().isoformat():
            status = "expired"
        elif row.trial:
            status = "trial"
        else:
            status = "active"
        return {"id": row.id, "license_key": row.license_key, "business_name": row.business_name,
                "owner_phone": row.owner_phone, "machine_id": row.machine_id, "trial": bool(row.trial),
                "base_term": row.base_term, "base_expires_at": row.base_expires_at,
                "modules": row.modules or {}, "revoked": bool(row.revoked), "notes": row.notes,
                "created_at": _iso(row.created_at), "activated_at": _iso(row.activated_at),
                "last_refresh_at": _iso(row.last_refresh_at), "last_app_version": row.last_app_version,
                "status": status}

    def _bad(msg):
        return jsonify({"msg": msg}), 400

    def _valid_date(v):
        try:
            return isinstance(v, str) and date.fromisoformat(v).isoformat() == v
        except ValueError:
            return False

    def _phone(d):
        """None if absent/empty, False if too long, else stripped string."""
        v = (d.get("owner_phone") or "").strip()
        if len(v) > 40:
            return False
        return v or None

    @app.route("/api/admin/licenses", methods=["GET"])
    @superadmin_required
    def admin_list_licenses():
        rows = Model.query.order_by(Model.created_at.desc()).all()
        return jsonify([row_dict(r) for r in rows])

    @app.route("/api/admin/licenses", methods=["POST"])
    @superadmin_required
    def admin_create_license():
        d = _body()
        if _bad_types(d, ("business_name", "owner_phone", "notes")):
            return _bad("business_name, owner_phone and notes must be strings")
        name = (d.get("business_name") or "").strip()
        if not name or len(name) > 200:
            return _bad("business_name is required (max 200 chars)")
        phone = _phone(d)
        if phone is False:
            return _bad("owner_phone too long (max 40 chars)")
        if d.get("base_term") not in TERMS:
            return _bad("base_term must be monthly, yearly or lifetime")
        mods_in = d.get("modules") or {}
        if not isinstance(mods_in, dict):
            return _bad("modules must be an object")
        today = date.today()
        mods = {}
        for k, t in mods_in.items():
            if k not in modmod.PAID:
                return _bad("unknown module %r" % k)
            if t not in TERMS:
                return _bad("invalid term for module %r" % k)
            mods[k] = {"term": t, "expires_at": lic.add_term(today, t).isoformat()}
        row = Model(id=str(uuid.uuid4()), license_key=_new_key(), business_name=name,
                    owner_phone=phone, trial=False, base_term=d["base_term"],
                    base_expires_at=lic.add_term(today, d["base_term"]).isoformat(),
                    modules=mods, notes=d.get("notes") or None)
        db.session.add(row)
        db.session.commit()
        return jsonify(row_dict(row)), 201

    @app.route("/api/admin/licenses/<lid>", methods=["PATCH"])
    @superadmin_required
    def admin_patch_license(lid):
        row = db.session.get(Model, lid)
        if not row:
            return jsonify({"msg": "not found"}), 404
        d = _body()
        # validate everything first, then apply
        if _bad_types(d, ("business_name", "owner_phone", "notes")):
            return _bad("business_name, owner_phone and notes must be strings")
        if "machine_id" in d:
            mid = d["machine_id"]
            if not isinstance(mid, str) or not mid.strip() or len(mid.strip()) > 128:
                return _bad("machine_id must be a non-empty string (max 128 chars)")
            mid = mid.strip()
        if "business_name" in d:
            name = (d["business_name"] or "").strip()
            if not name or len(name) > 200:
                return _bad("business_name is required (max 200 chars)")
        if "owner_phone" in d and _phone(d) is False:
            return _bad("owner_phone too long (max 40 chars)")
        if "revoked" in d and not isinstance(d["revoked"], bool):
            return _bad("revoked must be a boolean")
        if "base_term" in d and d["base_term"] not in TERMS:
            return _bad("base_term must be monthly, yearly or lifetime")
        if "base_expires_at" in d and not _valid_date(d["base_expires_at"]):
            return _bad("base_expires_at must be an ISO date (YYYY-MM-DD)")
        mods = None
        if "modules" in d:
            patch = d["modules"]
            if not isinstance(patch, dict):
                return _bad("modules must be an object")
            mods = dict(row.modules or {})
            for k, v in patch.items():
                if k not in modmod.PAID:
                    return _bad("unknown module %r" % k)
                if v is None:
                    mods.pop(k, None)
                    continue
                if not isinstance(v, dict) or v.get("term") not in TERMS or not _valid_date(v.get("expires_at")):
                    return _bad("module %r needs a valid term and expires_at" % k)
                mods[k] = {"term": v["term"], "expires_at": v["expires_at"]}
        if "business_name" in d:
            row.business_name = name
        if "owner_phone" in d:
            row.owner_phone = _phone(d)
        if "notes" in d:
            row.notes = d["notes"] or None
        if "revoked" in d:
            row.revoked = d["revoked"]
        if d.get("unbind_machine") is True:
            row.machine_id = None
        elif "machine_id" in d:
            row.machine_id = mid
        if "base_term" in d:
            row.base_term = d["base_term"]
        if "base_expires_at" in d:
            row.base_expires_at = d["base_expires_at"]
        if mods is not None:
            row.modules = mods
        db.session.commit()
        return jsonify(row_dict(row))

    @app.route("/api/admin/licenses/<lid>/renew", methods=["POST"])
    @superadmin_required
    def admin_renew_license(lid):
        row = db.session.get(Model, lid)
        if not row:
            return jsonify({"msg": "not found"}), 404
        d = _body()
        scope, term = d.get("scope"), d.get("term")
        if term is not None and term not in TERMS:
            return _bad("invalid term")
        today = date.today()
        if scope == "base":
            if row.trial:
                if not term:
                    return _bad("term is required to convert a trial")
                row.trial = False
                row.base_term = term
                row.base_expires_at = lic.renew(row.base_expires_at, term, today)
            else:
                use = term or row.base_term
                if use not in TERMS:
                    return _bad("term is required")
                row.base_term = use
                row.base_expires_at = lic.renew(row.base_expires_at, use, today)
        elif scope in modmod.PAID:
            mods = dict(row.modules or {})
            cur = mods.get(scope) or {}
            use = term or cur.get("term")
            if use not in TERMS:
                return _bad("term is required")
            mods[scope] = {"term": use, "expires_at": lic.renew(cur.get("expires_at"), use, today)}
            row.modules = mods
        else:
            return _bad("scope must be 'base' or a paid module key")
        db.session.commit()
        return jsonify(row_dict(row))

    @app.route("/api/admin/licenses/<lid>/file", methods=["GET"])
    @superadmin_required
    def admin_license_file(lid):
        row = db.session.get(Model, lid)
        if not row:
            return jsonify({"msg": "not found"}), 404
        if not _signing_key():
            return _not_configured()
        if not row.machine_id:
            return jsonify({"msg": "License is not bound to a computer yet. Set its machine ID first."}), 409
        return (signed_license(row), 200, {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": "attachment; filename=\"servicebills-%s.key\"" % row.license_key})
