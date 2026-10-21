from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import call_tool, connect, enable_connector, mcp, tool_data, tool_error_text


def _admin(client, username="ot_admin", business="OT Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    return hdr


def test_list_tickets_default_open(client):
    import app as appmod
    hdr = _admin(client)
    tid = appmod.User.query.filter_by(username="ot_admin").one().tenant_id
    appmod.db.session.add_all([
        appmod.SupportTicket(tenant_id=tid, title="No internet", description="down since 9", status="open", priority="high"),
        appmod.SupportTicket(tenant_id=tid, title="Old", description="fixed", status="closed", priority="low")])
    appmod.db.session.commit()
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_tickets", {}))
    assert [t["title"] for t in d["tickets"]] == ["No internet"]
    d = tool_data(call_tool(client, tok, "list_tickets", {"status": "all"}))
    assert d["total"] == 2


def test_network_status_matches_tree_and_needs_module(client):
    import app as appmod
    hdr = _admin(client)
    tid = appmod.User.query.filter_by(username="ot_admin").one().tenant_id
    appmod.db.session.add(appmod.NetworkDevice(tenant_id=tid, name="CCR", host="10.0.0.1", username="u",
                                               password="pw", device_type="mikrotik_ccr", api_port=8728))
    appmod.db.session.commit()
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "get_network_status", {}))
    assert [x["name"] for x in d["devices"]] == ["CCR"]
    assert "username" not in d["devices"][0] and "host" not in d["devices"][0]
    tenant = appmod.db.session.get(appmod.Tenant, tid)
    tenant.module_overrides = {"ai_connector": True, "network": False}
    appmod.db.session.commit()
    names = {t["name"] for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert "get_network_status" not in names


def test_resellers_admin_only(client):
    import app as appmod
    hdr = _admin(client)
    tid = appmod.User.query.filter_by(username="ot_admin").one().tenant_id
    appmod.db.session.add(appmod.Reseller(tenant_id=tid, name="Resell1", phone="1", type="type1", balance=-5))
    appmod.db.session.commit()
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_resellers", {}))
    assert d["resellers"][0]["name"] == "Resell1" and d["resellers"][0]["customer_count"] == 0
    emp = auth_headers(client, username="ot_employee", role="employee")
    tok_emp, _, _ = connect(client, emp)
    assert "role (employee)" in tool_error_text(call_tool(client, tok_emp, "list_resellers", {}))


def test_upstreams_hide_portal_credentials(client):
    import app as appmod
    hdr = _admin(client)
    tid = appmod.User.query.filter_by(username="ot_admin").one().tenant_id
    appmod.db.session.add(appmod.UpstreamProvider(tenant_id=tid, name="Terra", product="proradius",
                                                  portal_username="me", portal_password="secret", balance=100))
    appmod.db.session.commit()
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_upstreams", {}))
    assert d["upstreams"][0]["name"] == "Terra" and d["upstreams"][0]["balance"] == 100.0
    assert "portal_username" not in d["upstreams"][0] and "portal_url" not in d["upstreams"][0]
