import pytest

import mcp_server
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import call_tool, connect, enable_connector, mcp, tool_data, tool_error_text


@pytest.fixture
def echo_tool():
    def handler(ctx, args):
        if args.get("boom"):
            raise RuntimeError("kaboom")
        if args.get("bad"):
            raise mcp_server.ToolError("'bad' is not allowed.")
        return {"tenant": ctx.tenant.name, "echo": args.get("text")}
    t = mcp_server.register_tool(mcp_server.Tool(
        name="test_echo", title="Echo", description="test only",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=handler, roles=mcp_server.FINANCE))
    yield t
    mcp_server.TOOLS.pop("test_echo", None)


def _token(client, username="srv_admin", business="Srv Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    access, _, _ = connect(client, hdr)
    return access


def test_no_token_gives_401_with_resource_metadata(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401
    assert 'resource_metadata="http://localhost/.well-known/oauth-protected-resource"' in r.headers["WWW-Authenticate"]


def test_initialize_negotiates_version(client):
    tok = _token(client)
    r = mcp(client, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "1"}})
    res = r.get_json()["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["serverInfo"]["name"] == "servicebills" and "tools" in res["capabilities"]
    r = mcp(client, tok, "initialize", {"protocolVersion": "1999-01-01"})
    assert r.get_json()["result"]["protocolVersion"] == mcp_server.SUPPORTED_VERSIONS[0]


def test_notification_202_and_ping(client):
    tok = _token(client)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 202
    assert mcp(client, tok, "ping").get_json()["result"] == {}


def test_unknown_method_and_bad_version_header(client):
    tok = _token(client)
    assert mcp(client, tok, "resources/list").get_json()["error"]["code"] == -32601
    r = mcp(client, tok, "ping", headers={"MCP-Protocol-Version": "1999-01-01"})
    assert r.status_code == 400


def test_get_is_405(client):
    assert client.get("/mcp").status_code == 405


def test_tools_list_has_read_only_annotations(client, echo_tool):
    tok = _token(client)
    tools = {t["name"]: t for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert tools["test_echo"]["annotations"]["readOnlyHint"] is True
    assert tools["test_echo"]["inputSchema"]["type"] == "object"


def test_tool_call_success_is_audited(client, echo_tool):
    import app as appmod
    tok = _token(client)
    data = tool_data(call_tool(client, tok, "test_echo", {"text": "hi"}))
    assert data == {"tenant": "Srv Biz", "echo": "hi"}
    row = appmod.McpToolCall.query.one()
    assert row.tool == "test_echo" and row.ok is True and '"hi"' in row.args_summary
    assert appmod.McpGrant.query.one().last_used_at is not None


def test_tool_errors(client, echo_tool):
    import app as appmod
    tok = _token(client)
    assert tool_error_text(call_tool(client, tok, "test_echo", {"bad": True})) == "'bad' is not allowed."
    assert "went wrong" in tool_error_text(call_tool(client, tok, "test_echo", {"boom": True}))
    assert [r.ok for r in appmod.McpToolCall.query.order_by(appmod.McpToolCall.id)] == [False, False]
    r = mcp(client, tok, "tools/call", {"name": "nope", "arguments": {}})
    assert r.get_json()["error"]["code"] == -32602


def test_role_gate(client, echo_tool):
    make_tenant(client, "Role Biz", "role_admin")
    enable_connector("role_admin")
    cashier = auth_headers(client, username="role_cashier", role="cashier")
    tok, _, _ = connect(client, cashier)
    msg = tool_error_text(call_tool(client, tok, "test_echo", {"text": "x"}))
    assert "role (cashier)" in msg


def test_module_off_and_suspended_and_revoked(client, echo_tool):
    import app as appmod
    tok = _token(client)
    tenant = appmod.Tenant.query.filter_by(name="Srv Biz").one()
    tenant.module_overrides = {"ai_connector": False}
    appmod.db.session.commit()
    assert mcp(client, tok, "ping").status_code == 403
    tenant.module_overrides = {"ai_connector": True}
    tenant.status = "suspended"
    appmod.db.session.commit()
    assert mcp(client, tok, "ping").status_code == 403
    tenant.status = "active"
    appmod.db.session.commit()
    client.post("/oauth/revoke", data={"token": tok})
    assert mcp(client, tok, "ping").status_code == 401


def test_demotion_applies_immediately(client, echo_tool):
    import app as appmod
    tok = _token(client)
    assert tool_data(call_tool(client, tok, "test_echo", {"text": "a"}))["echo"] == "a"
    user = appmod.User.query.filter_by(username="srv_admin").one()
    user.role = "collector"
    appmod.db.session.commit()
    assert "role (collector)" in tool_error_text(call_tool(client, tok, "test_echo", {"text": "a"}))


def test_tenant_isolation_of_context(client, echo_tool):
    tok_a = _token(client, "iso_a", "Biz A")
    tok_b = _token(client, "iso_b", "Biz B")
    assert tool_data(call_tool(client, tok_a, "test_echo"))["tenant"] == "Biz A"
    assert tool_data(call_tool(client, tok_b, "test_echo"))["tenant"] == "Biz B"
