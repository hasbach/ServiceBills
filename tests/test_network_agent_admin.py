"""Agent management: creation, one-time token display, and rotation."""
import app as appmod
from tests.conftest import make_tenant


def test_create_returns_the_token_exactly_once(app, client):
    hdr = make_tenant(client, "Adm A", "adm_a_admin")
    created = client.post("/api/network-agents", headers=hdr,
                          json={"name": "DeltaNet Box"}).get_json()
    assert created["agent"]["name"] == "DeltaNet Box"
    assert created["agent"]["is_online"] is False
    token = created["token"]
    assert "." in token

    listed = client.get("/api/network-agents", headers=hdr).get_json()
    assert len(listed) == 1
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]


def test_created_token_authenticates_the_agent(app, client):
    hdr = make_tenant(client, "Adm B", "adm_b_admin")
    token = client.post("/api/network-agents", headers=hdr,
                        json={"name": "Box"}).get_json()["token"]
    r = client.get("/api/agent/jobs",
                   headers={"Authorization": "Bearer " + token})
    assert r.status_code == 204  # authenticated, simply no work


def test_regenerate_invalidates_the_previous_token(app, client):
    hdr = make_tenant(client, "Adm C", "adm_c_admin")
    created = client.post("/api/network-agents", headers=hdr,
                          json={"name": "Box"}).get_json()
    old, agent_id = created["token"], created["agent"]["id"]
    new = client.post(f"/api/network-agents/{agent_id}/regenerate-token",
                      headers=hdr).get_json()["token"]
    assert new != old
    assert client.get("/api/agent/jobs",
                      headers={"Authorization": "Bearer " + old}).status_code == 401
    assert client.get("/api/agent/jobs",
                      headers={"Authorization": "Bearer " + new}).status_code == 204


def test_agents_are_tenant_scoped(app, client):
    hdr_one = make_tenant(client, "Adm D1", "adm_d1_admin")
    agent_id = client.post("/api/network-agents", headers=hdr_one,
                           json={"name": "Box"}).get_json()["agent"]["id"]
    hdr_two = make_tenant(client, "Adm D2", "adm_d2_admin")
    assert client.get("/api/network-agents", headers=hdr_two).get_json() == []
    assert client.post(f"/api/network-agents/{agent_id}/regenerate-token",
                       headers=hdr_two).status_code == 404


def test_only_one_agent_per_tenant(app, client):
    hdr = make_tenant(client, "Adm E", "adm_e_admin")
    client.post("/api/network-agents", headers=hdr, json={"name": "One"})
    r = client.post("/api/network-agents", headers=hdr, json={"name": "Two"})
    assert r.status_code == 400


def test_agent_mode_allows_a_device_with_no_password(app, client):
    hdr = make_tenant(client, "Adm F", "adm_f_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Adm F").first()
        # address/mobile are NOT NULL with no Python-side default -- see
        # tests/test_network_agent_jobs.py's set_mode() for the same pattern.
        settings = appmod.BusinessSettings(
            tenant_id=tenant.id, business_name="Adm F", address="a", mobile="1")
        settings.network_access_mode = "agent"
        appmod.db.session.add(settings)
        appmod.db.session.commit()
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": "EPON OLT", "host": "192.168.8.100", "device_type": "vsol_olt"})
    assert r.status_code == 201
    assert r.get_json()["device"]["api_port"] == 161


def test_direct_mode_still_requires_a_device_password(app, client):
    hdr = make_tenant(client, "Adm G", "adm_g_admin")
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": "EPON OLT", "host": "192.168.8.100", "device_type": "vsol_olt"})
    assert r.status_code == 400
    assert "password" in r.get_json()["error"]
