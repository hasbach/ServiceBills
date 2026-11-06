from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import (call_tool, connect, enable_connector, enable_writes, mcp, tool_data,
                               tool_error_text)


def _setup(client, username="wc_admin", business="WC Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    enable_writes(username)
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "Basic", "price": 10, "billing_cycle": "monthly"})
    plan = r.get_json()["plan"]["id"]
    tok, _, _ = connect(client, hdr)
    return hdr, plan, tok


def test_list_plans(client):
    _, plan, tok = _setup(client)
    d = tool_data(call_tool(client, tok, "list_plans", {}))
    assert d["plans"][0]["id"] == plan and d["plans"][0]["price"] == 10.0


def test_create_update_and_never_touch_balance(client):
    import app as appmod
    _, plan, tok = _setup(client)
    d = tool_data(call_tool(client, tok, "create_customer", {
        "name": "Ali Hassan", "phone": "03123456", "subscription_plan_id": plan}))
    cid = d["customer"]["id"]
    assert d["customer"]["name"] == "Ali Hassan" and "added" in d["result"].lower()
    c = appmod.db.session.get(appmod.Customer, cid)
    before = float(c.balance)
    d = tool_data(call_tool(client, tok, "update_customer", {
        "customer_id": cid, "address": "Hamra", "balance": 999}))
    assert d["customer"]["address"] == "Hamra"
    assert float(appmod.db.session.get(appmod.Customer, cid).balance) == before
    tools = {t["name"]: t for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert "balance" not in tools["update_customer"]["inputSchema"]["properties"]


def test_create_requires_fields_before_calling(client):
    import app as appmod
    _, plan, tok = _setup(client)
    assert "'name' is required" in tool_error_text(call_tool(client, tok, "create_customer", {
        "phone": "03123456", "subscription_plan_id": plan}))
    assert appmod.Customer.query.count() == 0


def test_cancel_activate_renew(client):
    import app as appmod
    _, plan, tok = _setup(client)
    cid = tool_data(call_tool(client, tok, "create_customer", {
        "name": "Rana", "phone": "70111222", "subscription_plan_id": plan}))["customer"]["id"]
    assert "'reason' is required" in tool_error_text(call_tool(client, tok, "cancel_subscription",
                                                                {"customer_id": cid}))
    d = tool_data(call_tool(client, tok, "cancel_subscription", {"customer_id": cid, "reason": "moved away"}))
    assert d["customer"]["active"] is False
    d = tool_data(call_tool(client, tok, "activate_subscription", {"customer_id": cid}))
    assert d["customer"]["active"] is True
    old = appmod.db.session.get(appmod.Customer, cid).subscription_expiry_date
    d = tool_data(call_tool(client, tok, "renew_subscription", {"customer_id": cid}))
    assert appmod.db.session.get(appmod.Customer, cid).subscription_expiry_date > old
    # second identical renew within 2 minutes is refused by the dedupe guard
    assert "already made" in tool_error_text(call_tool(client, tok, "renew_subscription", {"customer_id": cid}))


def test_other_tenant_customer_not_found(client):
    _, plan_a, tok_a = _setup(client, "wa_a", "Biz WA")
    cid = tool_data(call_tool(client, tok_a, "create_customer", {
        "name": "A cust", "phone": "03999999", "subscription_plan_id": plan_a}))["customer"]["id"]
    _, _, tok_b = _setup(client, "wb_b", "Biz WB")
    assert "not found" in tool_error_text(call_tool(client, tok_b, "update_customer",
                                                    {"customer_id": cid, "notes": "x"})).lower()


def test_employee_cannot_see_or_use_desk_tools(client):
    _setup(client)
    emp = auth_headers(client, username="wc_emp", role="employee")
    tok, _, _ = connect(client, emp)
    names = {t["name"] for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert "create_customer" not in names
    assert "role (employee)" in tool_error_text(call_tool(client, tok, "renew_subscription", {"customer_id": 1}))
