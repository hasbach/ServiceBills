"""Symmetric encryption for secrets at rest (per-tenant WhatsApp credentials).

Uses Fernet with the key from FERNET_KEY. If the key is unset (local dev), values
pass through unchanged (with the understanding that prod MUST set FERNET_KEY).
Decrypt falls back to returning the raw value if it isn't valid ciphertext, so a
database that still holds pre-encryption plaintext keeps working during rollout.
"""
import os
import sqlalchemy.types as types


def _fernet():
    key = os.environ.get("FERNET_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value):
    if not value:
        return value
    f = _fernet()
    return f.encrypt(value.encode()).decode() if f else value


def decrypt(value):
    if not value:
        return value
    f = _fernet()
    if f is None:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except Exception:
        return value  # value predates encryption; return as-is


class EncryptedString(types.TypeDecorator):
    """Transparently encrypts on write and decrypts on read. Stored as TEXT."""
    impl = types.Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)
