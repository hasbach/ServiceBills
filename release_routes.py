"""SaaS-side release metadata API and installer download redirect."""
import hmac
import os
import re
from datetime import date, datetime

from flask import current_app, jsonify, redirect, request

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _semver_key(v):
    return tuple(int(x) for x in v.split("."))


def _cfg(key):
    return os.environ.get(key) or current_app.config.get(key)


def register(app, appmod):
    db = appmod.db
    Model = appmod.Release
    limiter = appmod.limiter

    def _row(r):
        return {"version": r.version, "release_date": r.release_date, "notes": r.notes,
                "min_upgrade_from": r.min_upgrade_from}

    @app.route("/api/internal/releases", methods=["POST"])
    def publish_release():
        secret = _cfg("RELEASE_PUBLISH_SECRET")
        if not secret:
            return jsonify({"error": "release publishing not configured"}), 503
        supplied = request.headers.get("X-Release-Secret", "")
        # Compare as UTF-8 bytes: str compare_digest raises TypeError on non-ASCII input.
        if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(silent=True)
        d = d if isinstance(d, dict) else {}
        version, rdate = d.get("version"), d.get("release_date")
        notes, minup = d.get("notes"), d.get("min_upgrade_from")
        if not isinstance(version, str) or not SEMVER.match(version):
            return jsonify({"error": "invalid version"}), 400
        if minup is not None and (not isinstance(minup, str) or not SEMVER.match(minup)):
            return jsonify({"error": "invalid min_upgrade_from"}), 400
        if notes is not None and not isinstance(notes, str):
            return jsonify({"error": "invalid notes"}), 400
        try:
            if not isinstance(rdate, str) or len(rdate) != 10:
                raise ValueError
            date.fromisoformat(rdate)
        except ValueError:
            return jsonify({"error": "invalid release_date"}), 400
        row = Model.query.filter_by(version=version).first()
        created = row is None
        if created:
            row = Model(version=version, created_at=datetime.utcnow())
            db.session.add(row)
        row.release_date, row.notes, row.min_upgrade_from = rdate, notes, minup
        db.session.commit()
        return jsonify(_row(row)), 201 if created else 200

    @app.route("/api/updates/latest", methods=["GET"])
    @limiter.limit("60 per minute")
    def latest_release():
        rows = Model.query.all()
        if not rows:
            return jsonify({"error": "no_release"}), 404
        return jsonify(_row(max(rows, key=lambda r: _semver_key(r.version)))), 200

    @app.route("/download", methods=["GET"])
    def download_installer():
        # Trim what was pasted into the Render dashboard; a stray newline would
        # otherwise make the redirect header raise (500).
        url = (_cfg("INSTALLER_URL") or "").strip()
        if (not url.startswith(("https://", "http://")) or any(c.isspace() for c in url)
                or url.rstrip("/").split("://", 1)[1].split("/", 1)[0] == request.host):
            if url:
                current_app.logger.error("INSTALLER_URL is not a usable external http(s) URL: %r", url[:200])
            return jsonify({"error": "installer not available"}), 503
        return redirect(url, code=302)
