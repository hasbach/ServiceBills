import pytest

import mcp_server
from mcp_server import ToolError
from mcp_tools import amount_arg, safe_message
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import (call_tool, connect, enable_connector, enable_writes, mcp,
                               tool_data, tool_error_text)


def _admin(client, username="wf_admin", business="WF Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    return hdr


def test_switch_defaults_off_and_admin_can_toggle(client):
    hdr = _admin(client)
    assert client.get("/api/mcp/grants", headers=hdr).get_json()["writes_enabled"] is False
    r = client.put("/api/mcp/settings", headers=hdr, json={"writes_enabled": True})
    assert r.status_code == 200 and r.get_json() == {"writes_enabled": True}
    assert client.get("/api/mcp/grants", headers=hdr).get_json()["writes_enabled"] is True


def test_settings_rejects_non_admin_and_bad_value(client):
    hdr = _admin(client)
    assert client.put("/api/mcp/settings", headers=hdr, json={"writes_enabled": "yes"}).status_code == 400
    fin = auth_headers(client, username="wf_fin", role="finance")
    assert client.put("/api/mcp/settings", headers=fin, json={"writes_enabled": True}).status_code == 403


def test_audit_column_defaults_false(client):
    import app as appmod
    assert appmod.McpToolCall.__table__.c.wrote.default.arg is False
    assert appmod.BusinessSettings.__table__.name == "business_settings"


CALLS = []


def _fake_write(ctx, args):
    CALLS.append(dict(args))
    return {"ok": True, "args": args}


@pytest.fixture
def fake_tools(monkeypatch):
    CALLS.clear()
    monkeypatch.setitem(mcp_server.TOOLS, "zz_write", mcp_server.Tool(
        name="zz_write", title="Fake write", description="test",
        input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
        handler=_fake_write, roles=mcp_server.DESK, writes=True, dedupe=True))
    monkeypatch.setitem(mcp_server.TOOLS, "zz_undo", mcp_server.Tool(
        name="zz_undo", title="Fake undo", description="test",
        input_schema={"type": "object", "properties": {}},
        handler=_fake_write, roles=mcp_server.ADMIN, writes=True, destructive=True))


def _names(client, tok):
    return {t["name"]: t for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}


def test_write_tools_hidden_and_refused_while_switch_off(client, fake_tools):
    hdr = _admin(client)
    tok, _, _ = connect(client, hdr)
    assert "zz_write" not in _names(client, tok)
    assert "turned off for this business" in tool_error_text(call_tool(client, tok, "zz_write", {"n": 1}))
    assert CALLS == []
    instr = mcp(client, tok, "initialize", {"protocolVersion": "2025-06-18"}).get_json()["result"]["instructions"]
    assert "You cannot change anything" in instr


def test_switch_on_lists_with_annotations_and_role_filter(client, fake_tools):
    hdr = _admin(client)
    enable_writes("wf_admin")
    tok, _, _ = connect(client, hdr)
    tools = _names(client, tok)
    ann = tools["zz_write"]["annotations"]
    assert ann["readOnlyHint"] is False and ann["destructiveHint"] is False and ann["idempotentHint"] is False
    assert "confirm_duplicate" in tools["zz_write"]["inputSchema"]["properties"]
    assert tools["zz_undo"]["annotations"]["destructiveHint"] is True
    assert tools["search_customers"]["annotations"]["readOnlyHint"] is True
    instr = mcp(client, tok, "initialize", {"protocolVersion": "2025-06-18"}).get_json()["result"]["instructions"]
    assert "You cannot change anything" not in instr and "confirm amounts" in instr
    fin = auth_headers(client, username="wf_fin2", role="finance")
    tok_fin, _, _ = connect(client, fin)
    names = _names(client, tok_fin)
    assert "zz_write" in names and "zz_undo" not in names
    assert "role (finance)" in tool_error_text(call_tool(client, tok_fin, "zz_undo", {}))


def test_dedupe_guard(client, fake_tools):
    import app as appmod
    hdr = _admin(client)
    enable_writes("wf_admin")
    tok, _, _ = connect(client, hdr)
    assert tool_data(call_tool(client, tok, "zz_write", {"n": 1}))["ok"] is True
    msg = tool_error_text(call_tool(client, tok, "zz_write", {"n": 1}))
    assert "already made" in msg and "confirm_duplicate" in msg
    assert tool_data(call_tool(client, tok, "zz_write", {"n": 2}))["ok"] is True
    assert tool_data(call_tool(client, tok, "zz_write", {"n": 1, "confirm_duplicate": True}))["ok"] is True
    assert CALLS == [{"n": 1}, {"n": 2}, {"n": 1}]   # confirm_duplicate is stripped
    rows = appmod.McpToolCall.query.filter_by(tool="zz_write").all()
    assert len(rows) == 4 and all(r.wrote for r in rows)
    assert appmod.McpToolCall.query.filter_by(tool="search_customers").count() == 0


def test_failed_call_does_not_block_retry(client, fake_tools, monkeypatch):
    hdr = _admin(client)
    enable_writes("wf_admin")
    tok, _, _ = connect(client, hdr)

    def boom(ctx, args):
        raise mcp_server.ToolError("Day 2026-11-06 is closed.")
    monkeypatch.setattr(mcp_server.TOOLS["zz_write"], "handler", boom)
    assert "closed" in tool_error_text(call_tool(client, tok, "zz_write", {"n": 5}))
    monkeypatch.setattr(mcp_server.TOOLS["zz_write"], "handler", _fake_write)
    assert tool_data(call_tool(client, tok, "zz_write", {"n": 5}))["ok"] is True


def test_safe_message_filters_internal_text():
    assert safe_message({"message": "Day 2026-11-06 is closed — ask an admin to reopen it."}).startswith("Day")
    assert safe_message({"error": "Category 'Fuel' not found."}) == "Category 'Fuel' not found."
    assert safe_message({"error": "'phone'"}) is None
    assert safe_message({"error": "(psycopg2.errors.UniqueViolation) duplicate key"}) is None
    assert safe_message({"error": "Traceback (most recent call last):\n ..."}) is None
    assert safe_message({"error": "x" * 250}) is None
    assert safe_message(["not a dict"]) is None


def test_amount_arg():
    assert amount_arg({"a": "12.5"}, "a") == 12.5
    assert amount_arg({}, "a", required=False) is None
    assert amount_arg({"a": 0}, "a", allow_zero=True) == 0.0
    for bad in (0, -1, "abc", True, 1.234):
        with pytest.raises(ToolError):
            amount_arg({"a": bad}, "a")
    with pytest.raises(ToolError):
        amount_arg({}, "a")


def test_dedupe_long_args_do_not_collide(client, fake_tools):
    # args_summary is capped at 500 chars and keys are sorted, so a long text
    # field sorting before the distinguishing one ('memo' < 'n', like 'notes'
    # < 'subscription_plan_id' on update_customer) would push it past the cut;
    # without the hash tail these two would look identical.
    import app as appmod
    hdr = _admin(client)
    enable_writes("wf_admin")
    tok, _, _ = connect(client, hdr)
    notes = "x" * 600
    assert tool_data(call_tool(client, tok, "zz_write", {"memo": notes, "n": 1}))["ok"] is True
    assert tool_data(call_tool(client, tok, "zz_write", {"memo": notes, "n": 2}))["ok"] is True
    assert "already made" in tool_error_text(call_tool(client, tok, "zz_write", {"memo": notes, "n": 2}))
    summaries = [r.args_summary for r in appmod.McpToolCall.query.filter_by(tool="zz_write").all()]
    assert all(len(s) <= 500 for s in summaries) and summaries[0] != summaries[1]
    assert summaries[0].startswith('{"memo": "xxx')
    assert mcp_server._summarize({"n": 1}) == '{"n": 1}'


def test_write_5xx_does_not_claim_nothing_changed(client, monkeypatch):
    import app as appmod
    hdr = _admin(client)
    enable_writes("wf_admin")
    tok, _, _ = connect(client, hdr)

    def boom(*a, **k):
        raise RuntimeError("secret internal detail")
    monkeypatch.setitem(appmod.app.view_functions, "update_customer", boom)
    text = tool_error_text(call_tool(client, tok, "update_customer", {"customer_id": 1, "notes": "x"}))
    assert text == ("ServiceBills hit an error (500). "
                    "Check in ServiceBills whether it was saved before trying again.")

    def soft_503(*a, **k):
        return appmod.jsonify({"error": "secret internal detail"}), 503
    monkeypatch.setitem(appmod.app.view_functions, "update_customer", soft_503)
    text = tool_error_text(call_tool(client, tok, "update_customer", {"customer_id": 1, "notes": "y"}))
    assert "hit an error (503)" in text and "whether it was saved" in text
    assert "Nothing was changed" not in text and "secret" not in text
