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


# ---- US1: connect flow (POST /api/whatsapp/connect/complete) ----------------

class GraphResp:
    def __init__(self, body=None, status=200):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body if body is not None else {}
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeGraph:
    """Routes wa_connect's Graph calls by (METHOD, path) and records them."""

    def __init__(self, waba="WABA_NEW", pnid="PN_NEW", is_on_biz_app=True, overrides=None):
        self.calls = []
        self.routes = {
            ("GET", "oauth/access_token"): GraphResp({"access_token": "BIZ_TOKEN"}),
            ("GET", "debug_token"): GraphResp({"data": {"is_valid": True, "granular_scopes": [
                {"scope": "whatsapp_business_management", "target_ids": [waba]},
                {"scope": "whatsapp_business_messaging", "target_ids": [waba]}]}}),
            ("GET", pnid): GraphResp({"id": pnid, "display_phone_number": "+961 70 000 001",
                                      "verified_name": "Delta Net", "quality_rating": "GREEN",
                                      "status": "CONNECTED", "platform_type": "CLOUD_API",
                                      "is_on_biz_app": is_on_biz_app,
                                      "whatsapp_business_manager_messaging_limit": "TIER_250"}),
            ("POST", pnid + "/register"): GraphResp({"success": True}),
            ("POST", waba + "/subscribed_apps"): GraphResp({"success": True}),
            ("GET", waba): GraphResp({"id": waba, "name": "Delta Net WABA", "primary_funding_id": "F1"}),
        }
        self.routes.update(overrides or {})

    def _route(self, method, url, **kw):
        path = url.split("graph.facebook.com/", 1)[1].split("/", 1)[1]
        self.calls.append((method, path, kw))
        resp = self.routes.get((method, path))
        if resp is None:
            raise AssertionError(f"unexpected Graph call {method} {path}")
        return resp

    def get(self, url, **kw):
        return self._route("GET", url, **kw)

    def post(self, url, **kw):
        return self._route("POST", url, **kw)

    def delete(self, url, **kw):
        return self._route("DELETE", url, **kw)

    def paths(self):
        return [(m, p) for m, p, _ in self.calls]


def _pro(app, tid):
    with app.app_context():
        appmod.db.session.get(appmod.Tenant, tid).plan = "pro"
        appmod.db.session.commit()


def _fresh_tenant(app, client, name, user, plan="pro"):
    from tests.conftest import make_tenant
    hdr = make_tenant(client, name, user)
    with app.app_context():
        tid = appmod.User.query.filter_by(username=user).first().tenant_id
        appmod.db.session.get(appmod.Tenant, tid).plan = plan
        appmod.db.session.commit()
    return hdr, tid


def _install(monkeypatch, graph):
    monkeypatch.setattr(wa_connect.requests, "get", graph.get)
    monkeypatch.setattr(wa_connect.requests, "post", graph.post)
    monkeypatch.setattr(wa_connect.requests, "delete", graph.delete)


COEX_BODY = {"method": "coexistence", "code": "CODE1", "waba_id": "WABA_NEW",
             "phone_number_id": "PN_NEW", "business_id": "BIZ1",
             "es_event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"}


def test_connect_coexistence_success(app, client, platform_meta, monkeypatch):
    hdr, tid = _fresh_tenant(app, client, "Biz C1", "c1_admin")
    graph = FakeGraph()
    _install(monkeypatch, graph)
    r = client.post("/api/whatsapp/connect/complete", headers=hdr, json=COEX_BODY)
    assert r.status_code == 200, r.get_json()
    conn = r.get_json()["connection"]
    assert conn["method"] == "coexistence" and conn["status"] == "connected"
    assert conn["display_phone_number"] == "+961 70 000 001"
    assert conn["meta_business_name"] == "Delta Net WABA"
    assert "access_token" not in conn

    paths = graph.paths()
    exchange = next(kw for m, p, kw in graph.calls if p == "oauth/access_token")
    assert exchange["params"]["client_id"] == "PLATFORM_APP"
    assert exchange["params"]["client_secret"] == PLATFORM_SECRET
    assert exchange["params"]["code"] == "CODE1"
    assert "redirect_uri" not in exchange["params"]
    assert ("GET", "debug_token") in paths
    assert ("POST", "WABA_NEW/subscribed_apps") in paths
    assert not any(p.endswith("/register") for _, p in paths)   # forbidden for coexistence

    with app.app_context():
        s = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first()
        assert s.access_token == "BIZ_TOKEN"
        assert s.phone_number_id == "PN_NEW" and s.business_account_id == "WABA_NEW"
        assert s.meta_business_id == "BIZ1"
        assert s.mode == "api" and s.enabled is True
        assert s.connection_method == "coexistence" and s.connection_status == "connected"
        assert s.auto_reply_enabled is False       # FR-013
        assert s.app_id is None and s.app_secret is None
        assert s.quality_rating == "GREEN" and s.messaging_limit_tier == "TIER_250"
        assert s.payment_method_status == "present"
        assert s.token_obtained_at and s.connected_at
        assert s.connected_by_user_id == appmod.User.query.filter_by(username="c1_admin").first().id
        events = [e.event for e in appmod.WhatsAppConnectionEvent.query.filter_by(tenant_id=tid)]
        assert events == ["connect"]


def test_connect_rejects_waba_not_granted(app, client, platform_meta, monkeypatch):
    hdr, tid = _fresh_tenant(app, client, "Biz C2", "c2_admin")
    graph = FakeGraph(overrides={("GET", "debug_token"): GraphResp({"data": {"granular_scopes": [
        {"scope": "whatsapp_business_management", "target_ids": ["SOMEONE_ELSES_WABA"]}]}})})
    _install(monkeypatch, graph)
    r = client.post("/api/whatsapp/connect/complete", headers=hdr, json=COEX_BODY)
    assert r.status_code == 403
    assert not any(p.endswith("subscribed_apps") for _, p in graph.paths())


def test_connect_rejects_number_owned_by_other_tenant(app, client, platform_meta, monkeypatch):
    setup_wa_tenant(app, client, "Biz Owner", "owner_admin", "PN_NEW")
    hdr, tid = _fresh_tenant(app, client, "Biz C3", "c3_admin")
    _install(monkeypatch, FakeGraph())
    r = client.post("/api/whatsapp/connect/complete", headers=hdr, json=COEX_BODY)
    assert r.status_code == 409
    assert "already connected" in r.get_json()["error"]


def test_connect_failure_leaves_settings_unchanged(app, client, platform_meta, monkeypatch):
    hdr, tid = setup_wa_tenant(app, client, "Biz C4", "c4_admin", "PN_OLD", secret="old-sec", token="old-tok")
    _pro(app, tid)
    with app.app_context():
        before = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first().to_dict()
    graph = FakeGraph(overrides={("POST", "WABA_NEW/subscribed_apps"): GraphResp(
        {"error": {"message": "Boom", "code": 100}}, status=400)})
    _install(monkeypatch, graph)
    r = client.post("/api/whatsapp/connect/complete", headers=hdr, json=COEX_BODY)
    assert r.status_code == 502
    with app.app_context():
        after = appmod.WhatsAppSettings.query.filter_by(tenant_id=tid).first().to_dict()
        assert after == before
        events = appmod.WhatsAppConnectionEvent.query.filter_by(tenant_id=tid).all()
        assert [e.event for e in events] == ["connect_failed"]
        assert "Boom" in events[0].detail


def test_connect_coexistence_requires_business_app_number(app, client, platform_meta, monkeypatch):
    hdr, tid = _fresh_tenant(app, client, "Biz C5", "c5_admin")
    _install(monkeypatch, FakeGraph(is_on_biz_app=False))
    r = client.post("/api/whatsapp/connect/complete", headers=hdr, json=COEX_BODY)
    assert r.status_code == 502
    assert "coexistence" in r.get_json()["error"]


def test_connect_gates(app, client, platform_meta, monkeypatch):
    _install(monkeypatch, FakeGraph())
    hdr, _ = _fresh_tenant(app, client, "Biz C6", "c6_admin", plan="free")
    assert client.post("/api/whatsapp/connect/complete", headers=hdr, json=COEX_BODY).status_code == 402
    hdr2, _ = _fresh_tenant(app, client, "Biz C7", "c7_admin")
    bad = dict(COEX_BODY, es_event="FINISH")
    assert client.post("/api/whatsapp/connect/complete", headers=hdr2, json=bad).status_code == 400
    monkeypatch.setattr(Config, "META_APP_ID", None)
    assert client.post("/api/whatsapp/connect/complete", headers=hdr2, json=COEX_BODY).status_code == 404


def test_connect_requires_admin(app, client, platform_meta, monkeypatch):
    from tests.conftest import auth_headers
    _install(monkeypatch, FakeGraph())
    _fresh_tenant(app, client, "Biz C8", "c8_admin")
    fin = auth_headers(client, "c8_finance", role="finance")
    assert client.post("/api/whatsapp/connect/complete", headers=fin, json=COEX_BODY).status_code == 403


def test_connect_repeat_same_number_is_idempotent(app, client, platform_meta, monkeypatch):
    hdr, tid = _fresh_tenant(app, client, "Biz C9", "c9_admin")
    _install(monkeypatch, FakeGraph())
    assert client.post("/api/whatsapp/connect/complete", headers=hdr, json=COEX_BODY).status_code == 200
    assert client.post("/api/whatsapp/connect/complete", headers=hdr, json=COEX_BODY).status_code == 200
    with app.app_context():
        events = [e.event for e in appmod.WhatsAppConnectionEvent.query.filter_by(tenant_id=tid)
                  .order_by(appmod.WhatsAppConnectionEvent.id)]
        assert events == ["connect", "reconnect"]


def test_connect_config_and_status_routes(app, client, platform_meta, monkeypatch):
    hdr, tid = _fresh_tenant(app, client, "Biz C10", "c10_admin")
    cfg = client.get("/api/whatsapp/connect/config", headers=hdr).get_json()
    assert cfg["automatic_available"] is True and cfg["coexistence_available"] is True
    assert cfg["app_id"] == "PLATFORM_APP" and cfg["es_config_id"] == "CFG1"
    assert cfg["plan_allows_api"] is True
    assert PLATFORM_SECRET not in str(cfg)
    _install(monkeypatch, FakeGraph())
    client.post("/api/whatsapp/connect/complete", headers=hdr, json=COEX_BODY)
    st = client.get("/api/whatsapp/connect/status", headers=hdr).get_json()
    assert st["connection"]["status"] == "connected"
    assert st["events"][0]["event"] == "connect"


def test_connect_config_when_unavailable(app, client, monkeypatch):
    monkeypatch.setattr(Config, "META_APP_ID", None)
    hdr, _ = _fresh_tenant(app, client, "Biz C11", "c11_admin")
    cfg = client.get("/api/whatsapp/connect/config", headers=hdr).get_json()
    assert cfg["automatic_available"] is False and cfg["app_id"] is None
