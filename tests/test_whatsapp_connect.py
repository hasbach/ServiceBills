"""WhatsApp automatic connection (Embedded Signup) -- see
specs/001-whatsapp-auto-connect/. This file starts with the foundation slice:
schema/backfill, the platform app-identity resolver, settings GET/POST guards
and webhook verify/HMAC for automatic vs manual tenants."""
import importlib.util
import os

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app as appmod
import wa_connect
from config import Config
from tests.inbox_helpers import setup_wa_tenant, signed_post, wa_payload

PLATFORM_SECRET = "platform-app-secret"
MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "migrations", "versions", "6bf7151ee83f_whatsapp_auto_connect.py")


@pytest.fixture
def platform_meta(monkeypatch):
    monkeypatch.setattr(Config, "META_APP_ID", "PLATFORM_APP")
    monkeypatch.setattr(Config, "META_APP_SECRET", PLATFORM_SECRET)
    monkeypatch.setattr(Config, "META_ES_CONFIG_ID", "CFG1")
    monkeypatch.setattr(Config, "META_WEBHOOK_VERIFY_TOKEN", "platform-verify")


def _make_automatic(app, tid, method="coexistence", status="connected"):
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.connection_method = method
        s.connection_status = status
        s.app_id = None
        s.app_secret = None
        s.business_account_id = "WABA_AUTO"
        appmod.db.session.commit()


# ---- migration ------------------------------------------------------------

def _load_migration():
    spec = importlib.util.spec_from_file_location("wa_auto_connect_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_adds_columns_backfills_and_downgrades(tmp_path):
    mod = _load_migration()
    assert mod.down_revision == "58a2f084761c"
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with engine.begin() as conn:
        for ddl in ("CREATE TABLE tenant (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE user (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE whats_app_settings (id INTEGER PRIMARY KEY, tenant_id INTEGER, "
                    "phone_number_id VARCHAR(100), access_token TEXT)",
                    "CREATE TABLE whatsapp_template (id INTEGER PRIMARY KEY, tenant_id INTEGER, name VARCHAR(200))"):
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql("INSERT INTO whats_app_settings (id, tenant_id, phone_number_id, access_token) "
                             "VALUES (1, 1, 'PN1', 'tok'), (2, 2, NULL, NULL), (3, 3, '', 'tok')")
        ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
        with Operations.context(ctx):
            mod.upgrade()
        insp = sa.inspect(conn)
        cols = {c["name"] for c in insp.get_columns("whats_app_settings")}
        assert {"connection_method", "connection_status", "status_reason", "meta_business_id",
                "meta_business_name", "display_phone_number", "verified_name", "quality_rating",
                "messaging_limit_tier", "payment_method_status", "registration_pin",
                "token_obtained_at", "connected_at", "connected_by_user_id",
                "last_health_check_at"} <= cols
        assert {"standard_key", "param_map"} <= {c["name"] for c in insp.get_columns("whatsapp_template")}
        assert "whatsapp_connection_event" in insp.get_table_names()
        rows = dict((r[0], (r[1], r[2])) for r in conn.exec_driver_sql(
            "SELECT id, connection_method, connection_status FROM whats_app_settings"))
        assert rows[1] == ("manual", "connected")
        assert rows[2] == ("manual", "disconnected")
        assert rows[3] == ("manual", "disconnected")

        with Operations.context(ctx):
            mod.downgrade()
        insp = sa.inspect(conn)
        assert "whatsapp_connection_event" not in insp.get_table_names()
        assert "connection_method" not in {c["name"] for c in insp.get_columns("whats_app_settings")}
        assert "param_map" not in {c["name"] for c in insp.get_columns("whatsapp_template")}


def test_new_settings_row_defaults_to_manual_disconnected(app, client):
    _, tid = setup_wa_tenant(app, client, "Biz Def", "def_admin", "PN_DEF")
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        assert s.connection_method == "manual"
        assert s.connection_status == "disconnected"
        assert appmod.WhatsAppConnectionEvent in appmod.TENANT_OWNED_MODELS
        assert appmod.WhatsAppConnectionEvent in appmod._TENANT_DELETE_ORDER


# ---- resolver -------------------------------------------------------------

def test_app_identity_manual_uses_tenant_values(app, client, platform_meta):
    _, tid = setup_wa_tenant(app, client, "Biz M", "m_admin", "PN_M", secret="tenant-secret")
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        s.app_id = "TENANT_APP"
        assert wa_connect.wa_app_identity(s) == ("TENANT_APP", "tenant-secret")


@pytest.mark.parametrize("method", ["coexistence", "embedded"])
def test_app_identity_automatic_uses_platform_values(app, client, platform_meta, method):
    _, tid = setup_wa_tenant(app, client, "Biz A", "a_admin", "PN_A")
    _make_automatic(app, tid, method)
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        assert wa_connect.wa_app_identity(s) == ("PLATFORM_APP", PLATFORM_SECRET)


def test_automatic_available_flags(app, platform_meta, monkeypatch):
    assert wa_connect.automatic_available() is True
    monkeypatch.setattr(Config, "META_COEXISTENCE_ENABLED", False)
    assert wa_connect.coexistence_available() is False
    monkeypatch.setattr(Config, "META_ES_CONFIG_ID", None)
    assert wa_connect.automatic_available() is False


def test_automatic_unavailable_on_prem(app, platform_meta, monkeypatch):
    monkeypatch.setattr(wa_connect.onprem, "is_onprem", lambda: True)
    assert wa_connect.automatic_available() is False


# ---- settings GET/POST guards ---------------------------------------------

def test_get_settings_blanks_secrets_for_automatic_tenant(app, client, platform_meta):
    hdr, tid = setup_wa_tenant(app, client, "Biz G", "g_admin", "PN_G", secret="sek", token="tok")
    _make_automatic(app, tid)
    body = client.get("/api/whatsapp-settings", headers=hdr).get_json()
    s = body["settings"]
    assert s["access_token"] == "" and s["app_secret"] == "" and s["app_id"] == ""
    assert body["connection"]["method"] == "coexistence"
    assert body["connection"]["status"] == "connected"
    assert "access_token" not in body["connection"]


def test_get_settings_manual_tenant_unchanged(app, client):
    hdr, tid = setup_wa_tenant(app, client, "Biz GM", "gm_admin", "PN_GM", secret="sek", token="tok")
    body = client.get("/api/whatsapp-settings", headers=hdr).get_json()
    assert body["settings"]["access_token"] == "tok"
    assert body["settings"]["app_secret"] == "sek"
    assert body["connection"]["method"] == "manual"


def test_post_settings_ignores_credentials_for_automatic_tenant(app, client, platform_meta, monkeypatch):
    hdr, tid = setup_wa_tenant(app, client, "Biz P", "p_admin", "PN_P", token="real-token")
    _make_automatic(app, tid)
    calls = []
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: calls.append(a) or None)
    r = client.post("/api/whatsapp-settings", headers=hdr, json={
        "phone_number_id": "HIJACK", "business_account_id": "HIJACK", "app_id": "X",
        "app_secret": "X", "access_token": "", "api_version": "v1.0",
        "webhook_verify_token": "x", "forwarding_mobile": "96170999999"})
    assert r.status_code == 200
    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        assert s.phone_number_id == "PN_P"
        assert s.business_account_id == "WABA_AUTO"
        assert s.access_token == "real-token"
        assert s.app_secret is None and s.app_id is None
        assert s.api_version == "v19.0"
        assert s.forwarding_mobile == "96170999999"   # non-credential fields still save
    assert calls == []   # no subscribed_apps call for automatic tenants


# ---- webhook --------------------------------------------------------------

def test_webhook_get_accepts_platform_verify_token(app, client, platform_meta):
    r = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=platform-verify&hub.challenge=42")
    assert r.status_code == 200 and r.get_data(as_text=True) == "42"
    r = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=42")
    assert r.status_code == 403


def test_webhook_get_without_platform_token_configured(app, client, monkeypatch):
    monkeypatch.setattr(Config, "META_WEBHOOK_VERIFY_TOKEN", None)
    r = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=&hub.challenge=42")
    assert r.status_code == 403


def test_webhook_automatic_tenant_verified_with_platform_secret(app, client, platform_meta):
    _, tid = setup_wa_tenant(app, client, "Biz W", "w_admin", "PN_W")
    _make_automatic(app, tid)
    payload = wa_payload("PN_W", statuses=[])
    assert signed_post(client, payload, PLATFORM_SECRET).status_code == 200
    assert signed_post(client, payload, "s3cret").status_code == 401


def test_webhook_manual_tenant_keeps_own_secret(app, client, platform_meta):
    setup_wa_tenant(app, client, "Biz WM", "wm_admin", "PN_WM", secret="tenant-own")
    payload = wa_payload("PN_WM", statuses=[])
    assert signed_post(client, payload, "tenant-own").status_code == 200
    assert signed_post(client, payload, PLATFORM_SECRET).status_code == 401


def test_webhook_template_status_automatic_uses_platform_secret(app, client, platform_meta):
    _, tid = setup_wa_tenant(app, client, "Biz T", "t_admin", "PN_T")
    _make_automatic(app, tid)
    payload = {"entry": [{"id": "WABA_AUTO", "changes": [{"field": "message_template_status_update",
               "value": {"event": "APPROVED", "message_template_name": "x", "message_template_language": "en"}}]}]}
    assert signed_post(client, payload, PLATFORM_SECRET).status_code == 200
    assert signed_post(client, payload, "s3cret").status_code == 401


def test_webhook_skips_disconnected_automatic_tenant(app, client, platform_meta):
    _, tid = setup_wa_tenant(app, client, "Biz D", "d_admin", "PN_D")
    _make_automatic(app, tid, status="disconnected")
    # Not routable: skipped before any signature check, so even a bad signature is 200.
    assert signed_post(client, wa_payload("PN_D", statuses=[]), "wrong").status_code == 200


def test_webhook_prefers_routable_row_when_pnid_shared(app, client, platform_meta):
    # An old disconnected automatic row must not shadow the tenant now holding the number.
    _, old_tid = setup_wa_tenant(app, client, "Biz Old", "old_admin", "PN_SHARED")
    _make_automatic(app, old_tid, status="disconnected")
    setup_wa_tenant(app, client, "Biz New", "new_admin", "PN_SHARED", secret="new-secret")
    assert signed_post(client, wa_payload("PN_SHARED", statuses=[]), "new-secret").status_code == 200
