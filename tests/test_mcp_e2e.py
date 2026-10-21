"""The whole connector path the way Claude.ai drives it: 401 -> discovery ->
registration -> authorize -> consent -> token -> initialize -> tools."""
from tests.conftest import make_tenant
from tests.mcp_helpers import (REDIRECT, approve, exchange, mcp, pkce_pair, query_of, tool_data,
                               call_tool, enable_connector)


def test_full_connector_flow(client):
    hdr = make_tenant(client, "E2E Biz", "e2e_admin")
    enable_connector("e2e_admin")

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    prm_url = r.headers["WWW-Authenticate"].split('resource_metadata="')[1].split('"')[0]
    prm = client.get(prm_url.replace("http://localhost", "")).get_json()
    asm = client.get("/.well-known/oauth-authorization-server").get_json()
    assert prm["authorization_servers"] == [asm["issuer"]]

    reg = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]}).get_json()
    verifier, challenge = pkce_pair()
    params = {"client_id": reg["client_id"], "redirect_uri": REDIRECT, "response_type": "code",
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
              "resource": prm["resource"]}
    consent_url = client.get("/oauth/authorize", query_string=params).headers["Location"]
    consent_params = query_of(consent_url)
    redirect_url = approve(client, hdr, consent_params).get_json()["redirect_url"]
    q = query_of(redirect_url)
    assert q["state"] == "xyz"
    tokens = exchange(client, q["code"], reg["client_id"], verifier).get_json()

    init = mcp(client, tokens["access_token"], "initialize",
               {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}})
    assert init.get_json()["result"]["protocolVersion"] == "2025-11-25"
    tools = {t["name"] for t in mcp(client, tokens["access_token"], "tools/list").get_json()["result"]["tools"]}
    assert {"search_customers", "get_customer", "list_customers", "list_payments", "get_cash_flow",
            "get_financial_summary", "list_tickets", "get_network_status", "list_resellers"} <= tools
    assert "list_upstreams" in tools   # the free bundle includes upstream_sync
    d = tool_data(call_tool(client, tokens["access_token"], "list_customers", {}))
    assert d["total"] == 0
