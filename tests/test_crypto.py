def test_encrypt_decrypt_roundtrip(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    import crypto
    ct = crypto.encrypt("secret-token")
    assert ct != "secret-token"          # actually encrypted
    assert crypto.decrypt(ct) == "secret-token"


def test_passthrough_without_key(monkeypatch):
    monkeypatch.delenv("FERNET_KEY", raising=False)
    import crypto
    assert crypto.encrypt("x") == "x"
    assert crypto.decrypt("x") == "x"


def test_whatsapp_token_encrypted_at_rest(app, monkeypatch):
    """The stored ciphertext must differ from the plaintext; the ORM returns plaintext."""
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    from app import db, Tenant, WhatsAppSettings
    from sqlalchemy import text
    with app.app_context():
        t = Tenant(name="T", slug="t")
        db.session.add(t)
        db.session.commit()
        s = WhatsAppSettings(tenant_id=t.id, mode="api", access_token="TOP-SECRET")
        db.session.add(s)
        db.session.commit()
        sid = s.id
        # Raw column value is ciphertext...
        raw = db.session.execute(
            text("SELECT access_token FROM whats_app_settings WHERE id=:i"), {"i": sid}
        ).scalar()
        assert raw != "TOP-SECRET"
        # ...but the ORM decrypts transparently on read.
        db.session.expire_all()
        assert db.session.get(WhatsAppSettings, sid).access_token == "TOP-SECRET"
