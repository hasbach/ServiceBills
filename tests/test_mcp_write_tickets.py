from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import (call_tool, connect, enable_connector, enable_writes, mcp, tool_data,
                               tool_error_text)


def _setup(client):
    hdr = make_tenant(client, "WT Biz", "wt_admin")
    enable_connector("wt_admin")
    enable_writes("wt_admin")
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "Basic", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    tok, _, _ = connect(client, hdr)
    cid = tool_data(call_tool(client, tok, "create_customer", {
        "name": "Tick", "phone": "03444555", "subscription_plan_id": plan}))["customer"]["id"]
    return hdr, tok, cid


def test_employee_opens_and_updates_ticket(client):
    import app as appmod
    _, _, cid = _setup(client)
    emp = auth_headers(client, username="wt_emp", role="employee")
    tok, _, _ = connect(client, emp)
    d = tool_data(call_tool(client, tok, "create_ticket", {"customer_id": cid, "title": "No internet"}))
    tid = d["ticket"]["id"]
    t = appmod.db.session.get(appmod.SupportTicket, tid)
    assert t.priority == "medium" and t.status == "open"
    d = tool_data(call_tool(client, tok, "update_ticket", {"ticket_id": tid, "status": "resolved"}))
    assert d["ticket"]["status"] == "resolved"
    assert "must be one of" in tool_error_text(call_tool(client, tok, "update_ticket",
                                                         {"ticket_id": tid, "status": "done"}))


def test_write_through_mcp_end_to_end_and_switch_off_again(client):
    import app as appmod
    hdr, tok, cid = _setup(client)
    r = mcp(client, tok, "tools/call", {"name": "create_ticket",
                                         "arguments": {"customer_id": cid, "title": "Slow"}})
    assert r.status_code == 200 and r.get_json()["result"]["isError"] is False
    row = appmod.McpToolCall.query.filter_by(tool="create_ticket").one()
    assert row.wrote is True and row.ok is True
    client.put("/api/mcp/settings", headers=hdr, json={"writes_enabled": False})
    names = {t["name"] for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert "create_ticket" not in names and "search_customers" in names
    assert "turned off" in tool_error_text(call_tool(client, tok, "create_ticket",
                                                     {"customer_id": cid, "title": "Again"}))
