"""ServiceBills on-prem license: format, Ed25519 signing, evaluation, terms.

Pure module (no Flask, no DB). See
docs/superpowers/specs/2026-09-30-onprem-runtime-license-design.md.
"""
import base64
import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dateutil.relativedelta import relativedelta

PREFIX = "SERVICEBILLS-LICENSE-1."
WARN_DAYS = 7


class LicenseError(Exception):
    pass


def _b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def generate_keypair():
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


def sign(payload, private_pem):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return PREFIX + _b64e(body) + "." + _b64e(key.sign(body))


def verify(text, public_pem):
    try:
        if not text or not text.startswith(PREFIX):
            raise ValueError("prefix")
        body_s, sig_s = text[len(PREFIX):].strip().split(".")
        body, sig = _b64d(body_s), _b64d(sig_s)
        serialization.load_pem_public_key(public_pem.encode()).verify(sig, body)
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("payload")
        return payload
    except (ValueError, InvalidSignature, TypeError) as e:
        raise LicenseError("invalid_signature") from e


def add_term(start, term):
    if term == "monthly":
        return start + relativedelta(months=1)
    if term == "yearly":
        return start + relativedelta(years=1)
    if term == "lifetime":
        return start + relativedelta(years=10)
    if term == "trial":
        return start + timedelta(days=30)
    raise ValueError(f"unknown term {term!r}")


def renew(current_expires_at, term, today):
    start = today
    if current_expires_at:
        start = max(today, date.fromisoformat(current_expires_at))
    return add_term(start, term).isoformat()


@dataclass
class LicenseState:
    state: str
    reason: str = None
    payload: dict = None
    active_modules: set = field(default_factory=set)
    base_expires_at: str = None
    warnings: list = field(default_factory=list)


def _readonly(reason, payload=None):
    base = (payload or {}).get("base") or {}
    return LicenseState("readonly", reason, payload, set(), base.get("expires_at"), [])


def evaluate(payload, today, machine_id, release_date=None, revoked=False):
    if payload is None:
        return _readonly("no_license")
    if revoked:
        return _readonly("revoked", payload)
    if payload.get("machine_id") != machine_id:
        return _readonly("machine_mismatch", payload)
    base_exp = ((payload.get("base") or {}).get("expires_at")) or ""
    t = today.isoformat()
    if base_exp < t:
        return _readonly("expired", payload)
    if release_date and not payload.get("trial") and release_date > base_exp:
        return _readonly("version_not_covered", payload)
    active, warnings = set(), []
    soon = (today + timedelta(days=WARN_DAYS)).isoformat()
    if base_exp <= soon:
        warnings.append({"scope": "base", "expires_at": base_exp})
    for key, v in (payload.get("modules") or {}).items():
        exp = (v or {}).get("expires_at") or ""
        if exp >= t:
            active.add(key)
            if exp <= soon:
                warnings.append({"scope": key, "expires_at": exp})
    return LicenseState("valid", None, payload, active, base_exp, warnings)
