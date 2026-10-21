"""Shared helpers for the MCP connector tests."""
import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlparse

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "http://localhost/mcp"


def enable_connector(username):
    """Switch the ai_connector module on for `username`'s tenant."""
    from app import db, Tenant, User
    user = User.query.filter_by(username=username).first()
    tenant = db.session.get(Tenant, user.tenant_id)
    tenant.module_overrides = {**(tenant.module_overrides or {}), "ai_connector": True}
    db.session.commit()
    return tenant


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def register_client(client, name="Claude", redirect=REDIRECT):
    r = client.post("/oauth/register", json={"client_name": name, "redirect_uris": [redirect]})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["client_id"]


def authorize_params(client_id, challenge, redirect=REDIRECT, state="st8"):
    return {"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
            "scope": "read", "resource": RESOURCE}


def approve(client, jwt_headers, params, decision="allow"):
    return client.post("/api/mcp/oauth/approve", headers=jwt_headers,
                       json={**params, "decision": decision})


def query_of(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def exchange(client, code, client_id, verifier, redirect=REDIRECT):
    return client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": redirect, "code_verifier": verifier})


def connect(client, jwt_headers):
    """Full OAuth dance as the user behind jwt_headers.
    Returns (access_token, refresh_token, client_id)."""
    cid = register_client(client)
    verifier, challenge = pkce_pair()
    r = approve(client, jwt_headers, authorize_params(cid, challenge))
    assert r.status_code == 200, r.get_data(as_text=True)
    code = query_of(r.get_json()["redirect_url"])["code"]
    t = exchange(client, code, cid, verifier)
    assert t.status_code == 200, t.get_data(as_text=True)
    j = t.get_json()
    return j["access_token"], j["refresh_token"], cid


def mcp(client, token, method, params=None, msg_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    h = {"Authorization": f"Bearer {token}", **(headers or {})}
    return client.post("/mcp", json=body, headers=h)


def call_tool(client, token, name, arguments=None):
    r = mcp(client, token, "tools/call", {"name": name, "arguments": arguments or {}})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["result"]


def tool_data(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def tool_error_text(result):
    assert result["isError"] is True, result
    return result["content"][0]["text"]
