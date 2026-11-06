from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import enable_connector


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
