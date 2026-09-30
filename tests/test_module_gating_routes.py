import app as appmod
from tests.conftest import make_tenant
from tests.test_module_gating_api import _set_overrides


def _off(app, client, name, slug, module):
    hdr = make_tenant(client, name, slug.replace("-", "_") + "_admin")
    _set_overrides(app, slug, {module: False})
    return hdr


def _assert_blocked(r, module):
    assert r.status_code == 403, r.get_data(as_text=True)
    assert r.get_json()["module"] == module


def test_network_routes_blocked(app, client):
    hdr = _off(app, client, "Net Off", "net-off", "network")
    for path in ("/api/network-devices", "/api/network-tree", "/api/network-map", "/api/network-agents"):
        _assert_blocked(client.get(path, headers=hdr), "network")


def test_network_routes_open_when_enabled(app, client):
    hdr = make_tenant(client, "Net On", "neton_admin")
    assert client.get("/api/network-devices", headers=hdr).status_code == 200


def test_upstream_routes_blocked(app, client):
    hdr = _off(app, client, "Up Off", "up-off", "upstream_sync")
    _assert_blocked(client.get("/api/upstream-providers", headers=hdr), "upstream_sync")


def test_upstream_routes_open_when_enabled(app, client):
    hdr = make_tenant(client, "Up On", "upon_admin")
    assert client.get("/api/upstream-providers", headers=hdr).status_code == 200


def test_agent_poll_blocked_when_network_off(app, client):
    hdr = make_tenant(client, "Agent Off", "agentoff_admin")
    r = client.post("/api/network-agents", headers=hdr, json={"name": "a1"})
    token = r.get_json().get("token")
    assert token, r.get_json()
    _set_overrides(app, "agent-off", {"network": False})
    r = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"})
    _assert_blocked(r, "network")
