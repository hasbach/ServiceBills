"""Models + migration for the MCP connector. The migration is run directly
through Alembic Operations on a throwaway SQLite file, like
tests/test_inbox_migration.py (the full chain can't be walked on SQLite)."""
import importlib.util
import os
from datetime import datetime, timedelta

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.conftest import make_tenant

MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "migrations", "versions", "9af8c7a2648e_add_mcp_connector_tables.py")


def _load():
    spec = importlib.util.spec_from_file_location("mcp_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mcp_migration_up_and_down(tmp_path):
    mod = _load()
    assert mod.down_revision == "18c24f5699dc"
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE tenant (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
        ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
        with Operations.context(ctx):
            mod.upgrade()
            mod.upgrade()  # idempotent: guarded by inspect()
        insp = sa.inspect(conn)
        assert {"mcp_client", "mcp_grant", "mcp_tool_call", "mcp_rotated_refresh"} <= set(insp.get_table_names())
        grant_cols = {c["name"] for c in insp.get_columns("mcp_grant")}
        assert {"access_token_hash", "refresh_token_hash",
                "code_hash", "code_challenge", "code_used_at", "revoked_at"} <= grant_cols
        assert "prev_refresh_token_hash" not in grant_cols   # replaced by mcp_rotated_refresh
        rot_cols = {c["name"] for c in insp.get_columns("mcp_rotated_refresh")}
        assert rot_cols == {"id", "tenant_id", "grant_id", "token_hash", "created_at"}
        rot_ix = {i["name"]: i for i in insp.get_indexes("mcp_rotated_refresh")}
        assert {"ix_mcp_rotated_refresh_tenant_id", "ix_mcp_rotated_refresh_grant_id",
                "ix_mcp_rotated_refresh_token_hash"} <= set(rot_ix)
        assert rot_ix["ix_mcp_rotated_refresh_token_hash"]["unique"]
        with Operations.context(ctx):
            mod.downgrade()
        insp = sa.inspect(conn)
        assert not ({"mcp_client", "mcp_grant", "mcp_tool_call", "mcp_rotated_refresh"}
                    & set(insp.get_table_names()))


def test_mcp_models_registered_and_serialize(app, client):
    import app as appmod
    make_tenant(client, "Biz MCP", "mcp_admin")
    user = appmod.User.query.filter_by(username="mcp_admin").first()
    appmod.db.session.add(appmod.McpClient(client_id="cid1", client_name="Claude",
                                           redirect_uris=["https://claude.ai/cb"]))
    g = appmod.McpGrant(tenant_id=user.tenant_id, user_id=user.id, client_id="cid1",
                        access_token_hash="a" * 64, access_expires_at=datetime.utcnow() + timedelta(hours=1))
    appmod.db.session.add(g)
    appmod.db.session.commit()
    d = g.to_dict("Claude")
    assert d["client_name"] == "Claude" and d["username"] == "mcp_admin"
    assert "access_token_hash" not in d and "refresh_token_hash" not in d
    assert appmod.McpGrant in appmod.TENANT_OWNED_MODELS
    assert appmod.McpToolCall in appmod.TENANT_OWNED_MODELS
    assert appmod.McpRotatedRefresh in appmod.TENANT_OWNED_MODELS
    order = appmod._TENANT_DELETE_ORDER
    assert order[0] is appmod.McpRotatedRefresh
    assert order.index(appmod.McpRotatedRefresh) < order.index(appmod.McpToolCall) < order.index(appmod.McpGrant)
    assert "access_token_hash" in appmod._EXPORT_COLUMN_DENYLIST[appmod.McpGrant]
    assert "prev_refresh_token_hash" not in appmod._EXPORT_COLUMN_DENYLIST[appmod.McpGrant]
    assert appmod._EXPORT_COLUMN_DENYLIST[appmod.McpRotatedRefresh] == {"token_hash"}
