"""Deleting a user who ever connected an AI app (FK-enforced)."""
import pytest
from sqlalchemy import text

from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import call_tool, connect, enable_connector, tool_data


@pytest.fixture
def fk_enforced(app):
    """Turn SQLite FK enforcement on for the shared in-memory connection (the
    pool is a StaticPool, so one connection serves the whole test). SQLite
    ignores the pragma inside a transaction, so commit first. Postgres always
    enforces these FKs; this is what makes the test fail without the fix."""
    import app as appmod
    appmod.db.session.commit()
    raw = appmod.db.engine.raw_connection()
    try:
        raw.cursor().execute("PRAGMA foreign_keys=ON")
        enabled = raw.cursor().execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        raw.close()
    try:
        assert enabled == 1
        yield
    finally:
        # Always switch it back: the connection is shared with later tests.
        appmod.db.session.rollback()
        raw = appmod.db.engine.raw_connection()
        try:
            raw.cursor().execute("PRAGMA foreign_keys=OFF")
        finally:
            raw.close()


def test_deleting_a_user_with_mcp_history_removes_their_mcp_rows(client, fk_enforced):
    import app as appmod
    admin_hdr = make_tenant(client, "Del Biz", "del_admin")
    enable_connector("del_admin")
    cash_hdr = auth_headers(client, username="del_cash", role="cashier")
    admin_access, _, _ = connect(client, admin_hdr)
    cash_access, cash_refresh, cash_cid = connect(client, cash_hdr)

    tool_data(call_tool(client, cash_access, "search_customers", {"query": "zz"}))   # McpToolCall row
    call_tool(client, admin_access, "list_upstreams")
    r = client.post("/oauth/token", data={"grant_type": "refresh_token",
                                          "refresh_token": cash_refresh, "client_id": cash_cid})
    assert r.status_code == 200                                          # McpRotatedRefresh row

    cash = appmod.User.query.filter_by(username="del_cash").one()
    admin = appmod.User.query.filter_by(username="del_admin").one()
    cash_id, admin_id = cash.id, admin.id
    cash_grant_ids = [g.id for g in appmod.McpGrant.query.filter_by(user_id=cash_id)]
    assert cash_grant_ids
    assert appmod.McpToolCall.query.filter_by(user_id=cash_id).count() == 1
    assert appmod.McpRotatedRefresh.query.filter(
        appmod.McpRotatedRefresh.grant_id.in_(cash_grant_ids)).count() == 1

    r = client.delete(f"/api/users/{cash_id}", headers=admin_hdr)
    assert r.status_code == 200, r.get_data(as_text=True)

    appmod.db.session.expire_all()
    assert appmod.db.session.get(appmod.User, cash_id) is None
    assert appmod.McpGrant.query.filter_by(user_id=cash_id).count() == 0
    assert appmod.McpToolCall.query.filter_by(user_id=cash_id).count() == 0
    assert appmod.McpRotatedRefresh.query.filter(
        appmod.McpRotatedRefresh.grant_id.in_(cash_grant_ids)).count() == 0
    # The admin's own connection and audit history are untouched.
    assert appmod.McpGrant.query.filter_by(user_id=admin_id).count() == 1
    assert appmod.McpToolCall.query.filter_by(user_id=admin_id).count() == 1
