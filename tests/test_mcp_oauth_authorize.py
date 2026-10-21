from tests.conftest import make_tenant
from tests.mcp_helpers import (REDIRECT, approve, authorize_params, enable_connector,
                               pkce_pair, query_of, register_client)


def _setup(client, username="ox_admin"):
    hdr = make_tenant(client, "OX Biz", username)
    enable_connector(username)
    cid = register_client(client)
    _, challenge = pkce_pair()
    return hdr, cid, authorize_params(cid, challenge)


def test_authorize_redirects_to_consent_page(client):
    _, _, params = _setup(client)
    r = client.get("/oauth/authorize", query_string=params)
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert loc.startswith("/oauth/consent?")
    assert query_of(loc)["client_id"] == params["client_id"]


def test_authorize_unknown_client_or_redirect_is_not_redirected(client):
    _, _, params = _setup(client)
    r = client.get("/oauth/authorize", query_string={**params, "client_id": "nope"})
    assert r.status_code == 400 and "Location" not in r.headers
    r = client.get("/oauth/authorize", query_string={**params, "redirect_uri": "https://evil.example/cb"})
    assert r.status_code == 400 and "Location" not in r.headers


def test_authorize_without_pkce_redirects_back_with_error(client):
    _, _, params = _setup(client)
    bad = {k: v for k, v in params.items() if k != "code_challenge"}
    r = client.get("/oauth/authorize", query_string=bad)
    assert r.status_code == 302
    q = query_of(r.headers["Location"])
    assert r.headers["Location"].startswith(REDIRECT)
    assert q["error"] == "invalid_request" and q["state"] == "st8"


def test_authorize_wrong_resource_rejected(client):
    _, _, params = _setup(client)
    r = client.get("/oauth/authorize", query_string={**params, "resource": "https://other.example/mcp"})
    assert query_of(r.headers["Location"])["error"] == "invalid_target"


def test_consent_info(client):
    hdr, _, params = _setup(client)
    r = client.get("/api/mcp/oauth/consent-info", query_string=params, headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["client_name"] == "Claude" and j["tenant_name"] == "OX Biz"
    assert j["username"] == "ox_admin" and j["redirect_host"] == "claude.ai"


def test_consent_info_module_off(client):
    hdr = make_tenant(client, "Off Biz", "off_admin")   # free plan, no override
    cid = register_client(client)
    _, challenge = pkce_pair()
    r = client.get("/api/mcp/oauth/consent-info", query_string=authorize_params(cid, challenge), headers=hdr)
    assert r.status_code == 403
    assert "not part of this business's plan" in r.get_json()["error"]


def test_approve_allow_creates_grant_with_code(client):
    import app as appmod
    hdr, cid, params = _setup(client)
    r = approve(client, hdr, params)
    assert r.status_code == 200
    q = query_of(r.get_json()["redirect_url"])
    assert q["state"] == "st8" and q["iss"] == "http://localhost" and q["code"]
    g = appmod.McpGrant.query.one()
    assert g.client_id == cid and g.code_challenge == params["code_challenge"]
    assert g.access_token_hash is None and g.code_hash != q["code"]   # hash, not the code


def test_approve_deny(client):
    import app as appmod
    hdr, _, params = _setup(client)
    r = approve(client, hdr, params, decision="deny")
    q = query_of(r.get_json()["redirect_url"])
    assert q["error"] == "access_denied" and q["state"] == "st8"
    assert appmod.McpGrant.query.count() == 0


def test_approve_requires_login(client):
    _, _, params = _setup(client)
    r = client.post("/api/mcp/oauth/approve", json={**params, "decision": "allow"})
    assert r.status_code == 401
