from datetime import datetime, timedelta

from tests.conftest import make_tenant
from tests.mcp_helpers import (approve, authorize_params, connect, enable_connector, exchange,
                               pkce_pair, query_of, register_client)


def _code(client, username="tk_admin"):
    hdr = make_tenant(client, "TK Biz", username)
    enable_connector(username)
    cid = register_client(client)
    verifier, challenge = pkce_pair()
    r = approve(client, hdr, authorize_params(cid, challenge))
    return hdr, cid, verifier, query_of(r.get_json()["redirect_url"])["code"]


def test_code_exchange_success(client):
    import app as appmod
    _, cid, verifier, code = _code(client)
    r = exchange(client, code, cid, verifier)
    assert r.status_code == 200
    j = r.get_json()
    assert j["token_type"] == "Bearer" and j["expires_in"] == 3600 and j["scope"] == "read"
    assert r.headers["Cache-Control"] == "no-store"
    g = appmod.McpGrant.query.one()
    assert g.code_used_at is not None and g.access_token_hash and g.access_token_hash != j["access_token"]


def test_wrong_verifier_rejected(client):
    _, cid, _, code = _code(client)
    other_verifier, _ = pkce_pair()
    r = exchange(client, code, cid, other_verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


def test_code_is_single_use_and_replay_revokes(client):
    import app as appmod
    _, cid, verifier, code = _code(client)
    assert exchange(client, code, cid, verifier).status_code == 200
    r = exchange(client, code, cid, verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    assert appmod.McpGrant.query.one().revoked_at is not None


def test_expired_code_rejected(client):
    import app as appmod
    _, cid, verifier, code = _code(client)
    g = appmod.McpGrant.query.one()
    g.code_expires_at = datetime.utcnow() - timedelta(seconds=1)
    appmod.db.session.commit()
    assert exchange(client, code, cid, verifier).status_code == 400


def test_wrong_client_or_redirect_rejected(client):
    _, cid, verifier, code = _code(client)
    assert exchange(client, code, "other-client", verifier).status_code == 400
    assert exchange(client, code, cid, verifier, redirect="https://claude.ai/other").status_code == 400


def test_refresh_rotates_and_reuse_revokes(client):
    import app as appmod
    hdr = make_tenant(client, "RF Biz", "rf_admin")
    enable_connector("rf_admin")
    access, refresh, cid = connect(client, hdr)
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
    assert r.status_code == 200
    new = r.get_json()
    assert new["refresh_token"] != refresh and new["access_token"] != access
    # replaying the rotated refresh token revokes the whole grant
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
    assert r.status_code == 400
    assert appmod.McpGrant.query.one().revoked_at is not None
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": new["refresh_token"], "client_id": cid})
    assert r.status_code == 400


def test_revoke_endpoint(client):
    import app as appmod
    hdr = make_tenant(client, "RV Biz", "rv_admin")
    enable_connector("rv_admin")
    access, _, _ = connect(client, hdr)
    assert client.post("/oauth/revoke", data={"token": access}).status_code == 200
    assert appmod.McpGrant.query.one().revoked_at is not None
    assert client.post("/oauth/revoke", data={"token": "garbage"}).status_code == 200


def test_unsupported_grant_type(client):
    r = client.post("/oauth/token", data={"grant_type": "password"})
    assert r.status_code == 400 and r.get_json()["error"] == "unsupported_grant_type"
