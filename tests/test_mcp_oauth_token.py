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
    r = exchange(client, code, cid, verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


def test_wrong_client_or_redirect_rejected(client):
    _, cid, verifier, code = _code(client)
    r = exchange(client, code, "other-client", verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    r = exchange(client, code, cid, verifier, redirect="https://claude.ai/other")
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


def test_code_replay_by_other_client_does_not_revoke(client):
    import app as appmod
    _, cid, verifier, code = _code(client)
    assert exchange(client, code, cid, verifier).status_code == 200
    r = exchange(client, code, "someone-else", verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    assert appmod.McpGrant.query.one().revoked_at is None   # only the owner's replay revokes


def test_refresh_rotates_and_reuse_revokes(client):
    import app as appmod
    hdr = make_tenant(client, "RF Biz", "rf_admin")
    enable_connector("rf_admin")
    access, refresh, cid = connect(client, hdr)
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
    assert r.status_code == 200
    new = r.get_json()
    assert new["refresh_token"] != refresh and new["access_token"] != access
    _age_rotations(timedelta(minutes=2))   # past the reuse grace window
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


def _refresh_post(client, refresh, cid):
    return client.post("/oauth/token", data={"grant_type": "refresh_token",
                                             "refresh_token": refresh, "client_id": cid})


def _age_rotations(delta):
    """Backdate every rotated-refresh row (moves it out of the grace window)."""
    import app as appmod
    for row in appmod.McpRotatedRefresh.query.all():
        row.created_at = row.created_at - delta
    appmod.db.session.commit()


def test_refresh_reuse_within_grace_window_is_refused_without_revoking(client):
    import app as appmod
    hdr = make_tenant(client, "GR Biz", "gr_admin")
    enable_connector("gr_admin")
    _, r1, cid = connect(client, hdr)
    new = _refresh_post(client, r1, cid).get_json()
    # A retry of the same refresh within 60 s (lost response, double-click)
    # is refused but must not kill the connection or the newer token.
    r = _refresh_post(client, r1, cid)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    assert appmod.McpGrant.query.one().revoked_at is None
    assert _refresh_post(client, new["refresh_token"], cid).status_code == 200


def test_refresh_reuse_after_grace_window_revokes(client):
    import app as appmod
    hdr = make_tenant(client, "GA Biz", "ga_admin")
    enable_connector("ga_admin")
    _, r1, cid = connect(client, hdr)
    new = _refresh_post(client, r1, cid).get_json()
    _age_rotations(timedelta(minutes=2))
    assert _refresh_post(client, r1, cid).status_code == 400
    assert appmod.McpGrant.query.one().revoked_at is not None
    assert _refresh_post(client, new["refresh_token"], cid).status_code == 400


def test_refresh_reuse_two_rotations_back_revokes(client):
    import app as appmod
    hdr = make_tenant(client, "R2 Biz", "r2_admin")
    enable_connector("r2_admin")
    _, r1, cid = connect(client, hdr)
    r2 = _refresh_post(client, r1, cid).get_json()["refresh_token"]
    r3 = _refresh_post(client, r2, cid).get_json()["refresh_token"]
    assert appmod.McpRotatedRefresh.query.count() == 2
    _age_rotations(timedelta(minutes=2))
    # the real client comes back with R1, two rotations behind the thief's R3
    r = _refresh_post(client, r1, cid)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    assert appmod.McpGrant.query.one().revoked_at is not None
    assert _refresh_post(client, r3, cid).status_code == 400


def test_refresh_refused_when_connector_module_off_but_not_revoked(client):
    import app as appmod
    hdr = make_tenant(client, "MO Biz", "mo_admin")
    tenant = enable_connector("mo_admin")
    _, refresh, cid = connect(client, hdr)
    tenant.module_overrides = {**tenant.module_overrides, "ai_connector": False}
    appmod.db.session.commit()
    r = _refresh_post(client, refresh, cid)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    g = appmod.McpGrant.query.one()
    assert g.revoked_at is None and g.refresh_token_hash is not None   # may come back
    tenant.module_overrides = {**tenant.module_overrides, "ai_connector": True}
    appmod.db.session.commit()
    assert _refresh_post(client, refresh, cid).status_code == 200


def test_refresh_refused_when_tenant_suspended_but_not_revoked(client):
    import app as appmod
    hdr = make_tenant(client, "SU Biz", "su_admin")
    tenant = enable_connector("su_admin")
    _, refresh, cid = connect(client, hdr)
    tenant.status = "suspended"
    appmod.db.session.commit()
    r = _refresh_post(client, refresh, cid)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    assert appmod.McpGrant.query.one().revoked_at is None


def _simulate_concurrent_winner(monkeypatch, winner):
    """Run `winner()` (a competing request's committed write) exactly once, after
    the loser has loaded and checked the grant but before it claims it. A real
    thread race can't be reproduced on in-memory SQLite, so the competing commit
    is injected at the point where the new token values are computed."""
    import mcp_oauth
    real = mcp_oauth.new_token
    fired = []

    def hooked():
        if not fired:
            fired.append(True)
            winner()
        return real()
    monkeypatch.setattr(mcp_oauth, "new_token", hooked)
    return fired


def test_code_redemption_is_atomic(client, monkeypatch):
    import app as appmod
    _, cid, verifier, code = _code(client, "at_admin")

    def winner():
        appmod.db.session.execute(appmod.McpGrant.__table__.update().values(code_used_at=datetime.utcnow()))
        appmod.db.session.commit()
    fired = _simulate_concurrent_winner(monkeypatch, winner)
    r = exchange(client, code, cid, verifier)
    assert fired
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    assert "access_token" not in r.get_json()
    g = appmod.McpGrant.query.one()
    assert g.access_token_hash is None and g.refresh_token_hash is None   # loser issued nothing
    assert g.revoked_at is None                                           # race path doesn't revoke


def test_refresh_rotation_is_atomic(client, monkeypatch):
    import app as appmod
    hdr = make_tenant(client, "AR Biz", "ar_admin")
    enable_connector("ar_admin")
    _, refresh, cid = connect(client, hdr)
    old_hash = appmod.McpGrant.query.one().refresh_token_hash
    winner_hash = "w" * 64

    def winner():
        # a competing refresh already rotated this token away
        g = appmod.McpGrant.query.one()
        appmod.db.session.add(appmod.McpRotatedRefresh(
            tenant_id=g.tenant_id, grant_id=g.id, token_hash=old_hash))
        appmod.db.session.execute(appmod.McpGrant.__table__.update().values(refresh_token_hash=winner_hash))
        appmod.db.session.commit()
    fired = _simulate_concurrent_winner(monkeypatch, winner)
    r = _refresh_post(client, refresh, cid)
    assert fired
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    assert "access_token" not in r.get_json()
    g = appmod.McpGrant.query.one()
    assert g.refresh_token_hash == winner_hash        # the loser did not overwrite the winner's rotation
    assert g.revoked_at is None
    assert appmod.McpRotatedRefresh.query.count() == 1  # no duplicate row from the loser
