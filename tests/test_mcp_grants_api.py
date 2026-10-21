from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import connect, enable_connector, mcp


def test_admin_sees_all_staff_sees_own(client):
    admin = make_tenant(client, "GR Biz", "gr_admin")
    enable_connector("gr_admin")
    cashier = auth_headers(client, username="gr_cashier", role="cashier")
    connect(client, admin)
    connect(client, cashier)
    j = client.get("/api/mcp/grants", headers=admin).get_json()
    assert j["mcp_url"] == "http://localhost/mcp" and j["enabled"] is True and j["is_admin"] is True
    assert sorted(g["username"] for g in j["grants"]) == ["gr_admin", "gr_cashier"]
    j = client.get("/api/mcp/grants", headers=cashier).get_json()
    assert [g["username"] for g in j["grants"]] == ["gr_cashier"]


def test_revoke_own_and_admin_revokes_any(client):
    admin = make_tenant(client, "RV2 Biz", "rv2_admin")
    enable_connector("rv2_admin")
    cashier = auth_headers(client, username="rv2_cashier", role="cashier")
    admin_tok, _, _ = connect(client, admin)
    cashier_tok, _, _ = connect(client, cashier)
    grants = client.get("/api/mcp/grants", headers=admin).get_json()["grants"]
    admin_gid = next(g["id"] for g in grants if g["username"] == "rv2_admin")
    cashier_gid = next(g["id"] for g in grants if g["username"] == "rv2_cashier")
    assert client.delete(f"/api/mcp/grants/{admin_gid}", headers=cashier).status_code == 404
    assert client.delete(f"/api/mcp/grants/{cashier_gid}", headers=admin).status_code == 200
    assert mcp(client, cashier_tok, "ping").status_code == 401
    assert mcp(client, admin_tok, "ping").status_code == 200


def test_other_tenant_grant_is_404(client):
    a = make_tenant(client, "GA", "ga_admin")
    enable_connector("ga_admin")
    connect(client, a)
    gid = client.get("/api/mcp/grants", headers=a).get_json()["grants"][0]["id"]
    b = make_tenant(client, "GB", "gb_admin")
    assert client.delete(f"/api/mcp/grants/{gid}", headers=b).status_code == 404
    assert client.get("/api/mcp/grants", headers=b).get_json()["grants"] == []
