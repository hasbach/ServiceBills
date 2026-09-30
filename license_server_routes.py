"""SaaS-side license server: public trial / activate / refresh endpoints."""
import os
import secrets
import uuid
from datetime import datetime

from flask import current_app, jsonify, request

import license as lic


def build_payload(row):
    return {"license_id": row.id, "business_name": row.business_name, "machine_id": row.machine_id,
            "trial": bool(row.trial), "base": {"term": row.base_term, "expires_at": row.base_expires_at},
            "modules": row.modules or {}, "issued_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}


def _signing_key():
    return os.environ.get("LICENSE_SIGNING_KEY") or current_app.config.get("LICENSE_SIGNING_KEY")


def signed_license(row):
    return lic.sign(build_payload(row), _signing_key())


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
        d = request.get_json(silent=True) or {}
        name = (d.get("business_name") or "").strip()
        machine = (d.get("machine_id") or "").strip()
        if not name or not machine:
            return jsonify({"error": "business_name and machine_id are required"}), 400
        if Model.query.filter_by(machine_id=machine).first():
            return jsonify({"error": "trial_already_used"}), 409
        now = datetime.utcnow()
        row = Model(id=str(uuid.uuid4()), license_key=_new_key(), business_name=name[:200],
                    owner_phone=(d.get("owner_phone") or None), machine_id=machine, trial=True,
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
        d = request.get_json(silent=True) or {}
        machine = (d.get("machine_id") or "").strip()
        row = Model.query.filter_by(license_key=(d.get("license_key") or "").strip()).first()
        if not row:
            return jsonify({"error": "unknown_license"}), 404
        if row.revoked:
            return jsonify({"error": "revoked"}), 403
        if not machine:
            return jsonify({"error": "machine_id is required"}), 400
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
        d = request.get_json(silent=True) or {}
        row = db.session.get(Model, d.get("license_id") or "")
        if not row:
            return jsonify({"error": "unknown_license"}), 404
        if row.revoked:
            return jsonify({"error": "revoked"}), 403
        if row.machine_id != d.get("machine_id"):
            return jsonify({"error": "license_in_use"}), 409
        row.last_refresh_at = datetime.utcnow()
        row.last_app_version = (d.get("app_version") or None)
        db.session.commit()
        return jsonify({"license": signed_license(row)}), 200
