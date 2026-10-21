"""Per-tenant AI-agent tool secret.

The ElevenLabs tool webhooks (/api/cs-agent/tools/*) are server-to-server
calls with no user session. They used to authenticate with ONE platform-wide
CS_AGENT_SECRET and then trust whatever tenant_id the request named, so any
holder of that secret could read any tenant's customers -- and with the
secret unset there was no check at all. Now each tenant has its own secret
and the tenant is derived FROM it.
"""
import importlib.util
import os

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app as appmod
from tests.test_cs_agent_tools import auth_headers, _setup_customer

MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "migrations", "versions", "18c24f5699dc_cs_agent_tool_secret.py")

LOOKUP = "/api/cs-agent/tools/lookup-customer"


def _tenant_id(username):
    return appmod.User.query.filter_by(username=username).first().tenant_id


def _make_tenant_with_secret(client, username, phone, name):
    """Registers a tenant with one customer, generates its tool secret, and
    returns (tenant_id, customer_id, secret, admin_headers)."""
    hdr = auth_headers(client, username, "pw123")
    tid = _tenant_id(username)
    cust_id, _ = _setup_customer(appmod.app, tid, name, phone)
    res = client.post("/api/cs-agent/tool-secret", headers=hdr)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    return tid, cust_id, body["secret"], hdr


def test_generate_returns_secret_once_and_header_instructions(app, client):
    hdr = auth_headers(client, "ts_gen", "pw123")
    res = client.post("/api/cs-agent/tool-secret", headers=hdr)
    assert res.status_code == 200
    body = res.get_json()
    assert body["secret"] and len(body["secret"]) >= 32
    assert body["header_name"] == "X-CS-Agent-Secret"
    assert body["header_value"] == body["secret"]
    assert body["created_at"]

    # Never shown again: config only says one exists.
    cfg = client.get("/api/cs-agent/config", headers=hdr).get_json()
    assert cfg["has_tool_secret"] is True
    assert cfg["tool_secret_created_at"]
    assert body["secret"] not in str(cfg)
    # Masked chip, same form as the other keys.
    assert cfg["tool_secret"] == body["masked"] == f"{body['secret'][:6]}...{body['secret'][-4:]}"
    assert cfg["tool_secret_header"] == "X-CS-Agent-Secret"

    # Stored hashed, not in plain text.
    with app.app_context():
        s = appmod.CSAgentSettings.query.filter_by(tenant_id=_tenant_id("ts_gen")).first()
        assert s.tool_secret_hash and body["secret"] not in s.tool_secret_hash


def test_tenant_secret_resolves_its_own_tenant_without_tenant_id(app, client):
    tid, cust_id, secret, _ = _make_tenant_with_secret(client, "ts_own", "70111222", "Rami Haddad")
    res = client.get(f"{LOOKUP}?phone=70111222", headers={"X-CS-Agent-Secret": secret})
    assert res.status_code == 200
    assert res.get_json()["primary_customer"]["id"] == cust_id

    # Bearer form works too.
    res = client.get(f"{LOOKUP}?phone=70111222", headers={"Authorization": f"Bearer {secret}"})
    assert res.status_code == 200
    assert res.get_json()["found"] is True

    # A matching tenant_id is fine.
    res = client.get(f"{LOOKUP}?phone=70111222&tenant_id={tid}", headers={"X-CS-Agent-Secret": secret})
    assert res.status_code == 200


def test_tenant_a_secret_cannot_read_tenant_b(app, client, monkeypatch):
    monkeypatch.setitem(app.config, "CS_AGENT_SECRET", "legacy_global_xyz")
    tid_a, _, secret_a, _ = _make_tenant_with_secret(client, "ts_a", "70333444", "Tenant A Cust")
    tid_b, cust_b, _, _ = _make_tenant_with_secret(client, "ts_b", "70555666", "Tenant B Cust")
    h = {"X-CS-Agent-Secret": secret_a}

    # Query-string tenant_id naming B -> rejected.
    assert client.get(f"{LOOKUP}?phone=70555666&tenant_id={tid_b}", headers=h).status_code == 401
    # JSON body tenant_id naming B -> rejected, on every tool endpoint.
    for path in ("/api/cs-agent/tools/lookup-customer",
                 "/api/cs-agent/tools/customer-status",
                 "/api/cs-agent/tools/get-customer-status",
                 "/api/cs-agent/tools/network-diagnostic",
                 "/api/cs-agent/tools/send-payment-link",
                 "/api/cs-agent/tools/escalate",
                 "/api/cs-agent/tools/escalate-to-human"):
        res = client.post(path, json={"tenant_id": tid_b, "customer_id": cust_b, "phone": "70555666"},
                          headers=h)
        assert res.status_code == 401, path

    # Without tenant_id, A's secret only ever sees A's data.
    res = client.get(f"{LOOKUP}?phone=70555666", headers=h)
    assert res.status_code == 200
    assert res.get_json()["found"] is False
    res = client.post("/api/cs-agent/tools/customer-status", json={"customer_id": cust_b}, headers=h)
    assert res.status_code == 404


def test_global_secret_no_longer_works_for_tenant_with_own_secret(app, client, monkeypatch):
    monkeypatch.setitem(app.config, "CS_AGENT_SECRET", "legacy_global_xyz")
    tid, _, _, _ = _make_tenant_with_secret(client, "ts_legacy_off", "70777888", "Legacy Off")
    res = client.get(f"{LOOKUP}?phone=70777888&tenant_id={tid}",
                     headers={"X-CS-Agent-Secret": "legacy_global_xyz"})
    assert res.status_code == 401


def test_legacy_global_secret_still_works_for_tenant_without_own_secret(app, client, monkeypatch):
    """Temporary fallback so DeltaNet's existing agent keeps working until the
    owner pastes its new per-tenant secret into ElevenLabs."""
    monkeypatch.setitem(app.config, "CS_AGENT_SECRET", "legacy_global_xyz")
    auth_headers(client, "ts_legacy_on", "pw123")
    tid = _tenant_id("ts_legacy_on")
    _setup_customer(app, tid, "Legacy On", "70999000")
    res = client.get(f"{LOOKUP}?phone=70999000&tenant_id={tid}",
                     headers={"X-CS-Agent-Secret": "legacy_global_xyz"})
    assert res.status_code == 200
    assert res.get_json()["found"] is True
    # Wrong global secret -> 401; global secret without tenant_id -> 401.
    assert client.get(f"{LOOKUP}?phone=70999000&tenant_id={tid}",
                      headers={"X-CS-Agent-Secret": "nope"}).status_code == 401
    assert client.get(f"{LOOKUP}?phone=70999000",
                      headers={"X-CS-Agent-Secret": "legacy_global_xyz"}).status_code == 401


def test_no_secret_is_401(app, client, monkeypatch):
    monkeypatch.setitem(app.config, "CS_AGENT_SECRET", "legacy_global_xyz")
    tid, _, _, _ = _make_tenant_with_secret(client, "ts_nosecret", "70121212", "No Secret")
    assert client.get(f"{LOOKUP}?phone=70121212&tenant_id={tid}").status_code == 401
    assert client.get(f"{LOOKUP}?phone=70121212").status_code == 401
    assert client.get(f"{LOOKUP}?phone=70121212",
                      headers={"X-CS-Agent-Secret": "csat_not_a_real_secret"}).status_code == 401


def test_unset_global_secret_no_longer_opens_endpoints(app, client, monkeypatch):
    """Before: CS_AGENT_SECRET unset (production default) + any tenant_id =
    full access with no credentials at all."""
    monkeypatch.setitem(app.config, "CS_AGENT_SECRET", "")
    auth_headers(client, "ts_unset", "pw123")
    tid = _tenant_id("ts_unset")
    cust_id, _ = _setup_customer(app, tid, "Unset Global", "70343434")
    assert client.get(f"{LOOKUP}?phone=70343434&tenant_id={tid}").status_code == 401
    assert client.post("/api/cs-agent/tools/customer-status",
                       json={"tenant_id": tid, "customer_id": cust_id}).status_code == 401
    assert client.get(f"{LOOKUP}?phone=70343434&tenant_id={tid}",
                      headers={"X-CS-Agent-Secret": ""}).status_code == 401


def test_rotation_invalidates_old_secret(app, client):
    _, _, old, hdr = _make_tenant_with_secret(client, "ts_rotate", "70565656", "Rotate Me")
    new = client.post("/api/cs-agent/tool-secret", headers=hdr).get_json()["secret"]
    assert new != old
    assert client.get(f"{LOOKUP}?phone=70565656", headers={"X-CS-Agent-Secret": old}).status_code == 401
    assert client.get(f"{LOOKUP}?phone=70565656", headers={"X-CS-Agent-Secret": new}).status_code == 200


def test_revoke_removes_secret(app, client):
    _, _, secret, hdr = _make_tenant_with_secret(client, "ts_revoke", "70787878", "Revoke Me")
    assert client.delete("/api/cs-agent/tool-secret", headers=hdr).status_code == 200
    assert client.get(f"{LOOKUP}?phone=70787878", headers={"X-CS-Agent-Secret": secret}).status_code == 401
    cfg = client.get("/api/cs-agent/config", headers=hdr).get_json()
    assert cfg["has_tool_secret"] is False
    assert cfg["tool_secret"] == ""


def test_tool_secret_endpoint_requires_admin_jwt(app, client):
    hdr = auth_headers(client, "ts_admin_owner", "pw123")
    _, _, secret, _ = _make_tenant_with_secret(client, "ts_admin_owner", "70909090", "Admin Owner")
    # No JWT, or the tool secret itself, cannot mint a new secret.
    assert client.post("/api/cs-agent/tool-secret").status_code == 401
    assert client.post("/api/cs-agent/tool-secret",
                       headers={"X-CS-Agent-Secret": secret}).status_code == 401
    # Non-admin user of the same tenant -> 403.
    staff = auth_headers(client, "ts_staff", "pw123", role="cashier")
    assert client.post("/api/cs-agent/tool-secret", headers=staff).status_code == 403
    assert client.delete("/api/cs-agent/tool-secret", headers=staff).status_code == 403
    assert hdr  # admin headers still valid


def test_jwt_path_unchanged(app, client, monkeypatch):
    monkeypatch.setitem(app.config, "CS_AGENT_SECRET", "")
    hdr = auth_headers(client, "ts_jwt", "pw123")
    cust_id, _ = _setup_customer(app, _tenant_id("ts_jwt"), "Jwt Path", "70232323")
    res = client.get(f"{LOOKUP}?phone=70232323", headers=hdr)
    assert res.status_code == 200
    assert res.get_json()["primary_customer"]["id"] == cust_id


def _load_migration():
    spec = importlib.util.spec_from_file_location("cs_tool_secret_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_targets_the_models_real_table():
    """A migration aimed at a guessed table name (Flask-SQLAlchemy snake_cases
    CamelCase models) broke a production deploy; pin it to the model."""
    mod = _load_migration()
    assert mod.TABLE == appmod.CSAgentSettings.__table__.name
    model_cols = set(appmod.CSAgentSettings.__table__.columns.keys())
    assert {name for name, _, _ in mod._COLUMNS} <= model_cols


def test_migration_up_and_down(tmp_path):
    mod = _load_migration()
    assert mod.down_revision == "6bf7151ee83f"
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE tenant (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE cs_agent_settings (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL)")
        ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
        with Operations.context(ctx):
            mod.upgrade()
            mod.upgrade()  # idempotent: inspect() skips existing columns
        insp = sa.inspect(conn)
        cols = {c["name"] for c in insp.get_columns("cs_agent_settings")}
        assert {"tool_secret_hash", "tool_secret_created_at", "tool_secret_hint"} <= cols
        assert any(ix["name"] == "ix_cs_agent_settings_tool_secret_hash"
                   for ix in insp.get_indexes("cs_agent_settings"))
        with Operations.context(ctx):
            mod.downgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("cs_agent_settings")}
        assert "tool_secret_hash" not in cols


def test_single_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(MIGRATION), ".."))
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
