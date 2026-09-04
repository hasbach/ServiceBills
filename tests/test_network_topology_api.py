"""API tests for the topology fields on NetworkDevice and the type-dispatched
check-now. See docs/superpowers/specs/2026-09-01-network-topology-tree-design.md."""
import app as appmod
from tests.conftest import make_tenant

OLT_ONUS = [
    {"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
     "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
     "model": "V2801D", "distance_m": 531},
    {"pon_port": "PON1", "onu_id": "EPON0/1:5", "status": "offline",
     "mac_address": "f4:c4:d6:4d:80:e1", "description": "OstaMarket",
     "model": "unknow", "distance_m": 0},
]


def make_ccr(client, hdr, name="Core CCR"):
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": name, "host": "10.0.0.1", "username": "admin",
        "password": "secret", "device_type": "mikrotik_ccr",
    })
    assert r.status_code == 201, r.get_json()
    return r.get_json()["device"]


def make_olt(client, hdr, parent_id, name="EPON OLT"):
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": name, "host": "192.168.8.100", "password": "public",
        "device_type": "vsol_olt", "parent_device_id": parent_id,
    })
    assert r.status_code == 201, r.get_json()
    return r.get_json()["device"]


def test_create_requires_an_explicit_device_type(app, client):
    hdr = make_tenant(client, "Api A", "api_a_admin")
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": "Nameless", "host": "10.0.0.1", "username": "admin", "password": "s",
    })
    assert r.status_code == 400
    assert "device_type" in r.get_json()["error"]


def test_create_rejects_an_unknown_device_type(app, client):
    hdr = make_tenant(client, "Api B", "api_b_admin")
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": "Weird", "host": "10.0.0.1", "username": "a", "password": "s",
        "device_type": "cisco_something",
    })
    assert r.status_code == 400


def test_olt_defaults_to_snmp_port_161(app, client):
    hdr = make_tenant(client, "Api C", "api_c_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    assert olt["api_port"] == 161
    assert olt["device_type"] == "vsol_olt"
    assert olt["parent_device_id"] == ccr["id"]
    assert ccr["api_port"] == 8728


def test_parent_must_belong_to_the_same_tenant(app, client):
    hdr_one = make_tenant(client, "Api D1", "api_d1_admin")
    ccr = make_ccr(client, hdr_one)
    hdr_two = make_tenant(client, "Api D2", "api_d2_admin")
    r = client.post("/api/network-devices", headers=hdr_two, json={
        "name": "Sneaky OLT", "host": "192.168.8.100", "password": "public",
        "device_type": "vsol_olt", "parent_device_id": ccr["id"],
    })
    assert r.status_code == 400
    assert "parent" in r.get_json()["error"].lower()


def test_device_cannot_be_its_own_parent(app, client):
    hdr = make_tenant(client, "Api E", "api_e_admin")
    ccr = make_ccr(client, hdr)
    r = client.put(f"/api/network-devices/{ccr['id']}", headers=hdr,
                   json={"parent_device_id": ccr["id"]})
    assert r.status_code == 400


def test_parent_cycle_is_rejected(app, client):
    hdr = make_tenant(client, "Api F", "api_f_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    # Making the CCR a child of its own child would create a cycle.
    r = client.put(f"/api/network-devices/{ccr['id']}", headers=hdr,
                   json={"parent_device_id": olt["id"]})
    assert r.status_code == 400
    assert "cycle" in r.get_json()["error"].lower()


def test_deleting_a_parent_reparents_its_children(app, client):
    hdr = make_tenant(client, "Api G", "api_g_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    r = client.delete(f"/api/network-devices/{ccr['id']}", headers=hdr)
    assert r.status_code == 200
    remaining = client.get("/api/network-devices", headers=hdr).get_json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == olt["id"]
    assert remaining[0]["parent_device_id"] is None


def test_check_now_on_an_olt_returns_onus_not_health(app, client, monkeypatch):
    """check-now now only returns a job_id; the OLT's onu list (not a health
    payload) shows up as the job's result once polled."""
    hdr = make_tenant(client, "Api H", "api_h_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])

    def fake_get_olt_status(device):
        device.last_status = "online"
        return True, OLT_ONUS
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", fake_get_olt_status)

    r = client.post(f"/api/network-devices/{olt['id']}/check-now", headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["device"]["last_status"] == "online"

    polled = client.get(f"/api/network-jobs/{body['job_id']}", headers=hdr).get_json()
    assert polled["status"] == "done"
    assert len(polled["result"]) == 2
    assert polled["result"][0]["description"] == "MoussaGhadir"


def test_check_now_on_an_olt_surfaces_failure_without_raising(app, client, monkeypatch):
    """The connector failure is direct mode's job running inline -- check-now
    itself still succeeds (a job was created); the failure lands in the job."""
    hdr = make_tenant(client, "Api I", "api_i_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda device: (False, "No SNMP response received before timeout"))
    r = client.post(f"/api/network-devices/{olt['id']}/check-now", headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True

    polled = client.get(f"/api/network-jobs/{body['job_id']}", headers=hdr).get_json()
    assert polled["status"] == "done"
    assert "timeout" in polled["error"]


def test_check_now_on_a_ccr_still_returns_health(app, client, monkeypatch):
    hdr = make_tenant(client, "Api J", "api_j_admin")
    ccr = make_ccr(client, hdr)
    monkeypatch.setattr(appmod.mikrotik, "get_device_health",
                        lambda device: (True, {"identity": "CCR", "uptime": "1d",
                                               "interfaces": [{"name": "ether1",
                                                               "running": True,
                                                               "disabled": False}]}))
    r = client.post(f"/api/network-devices/{ccr['id']}/check-now", headers=hdr)
    body = r.get_json()
    assert body["ok"] is True

    polled = client.get(f"/api/network-jobs/{body['job_id']}", headers=hdr).get_json()
    assert polled["status"] == "done"
    assert polled["result"]["identity"] == "CCR"


def test_olt_semaphore_returns_clear_error_when_exhausted(app, client, monkeypatch):
    hdr = make_tenant(client, "Api K", "api_k_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod, "_OLT_CHECK_ACQUIRE_TIMEOUT_SECONDS", 0.01)
    appmod._olt_check_semaphore.acquire()
    try:
        r = client.post(f"/api/network-devices/{olt['id']}/check-now", headers=hdr)
        body = r.get_json()
        assert body["ok"] is True  # job created; the semaphore failure is inside it

        polled = client.get(f"/api/network-jobs/{body['job_id']}", headers=hdr).get_json()
        assert polled["status"] == "done"
        assert "Too many OLT checks" in polled["error"]
    finally:
        appmod._olt_check_semaphore.release()


def test_olt_semaphore_is_independent_of_the_upstream_one(app, client, monkeypatch):
    """Exhausting the upstream-sync semaphore must not block an OLT check."""
    hdr = make_tenant(client, "Api L", "api_l_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda device: (True, OLT_ONUS))
    appmod._upstream_sync_semaphore.acquire()
    try:
        r = client.post(f"/api/network-devices/{olt['id']}/check-now", headers=hdr)
        assert r.get_json()["ok"] is True
    finally:
        appmod._upstream_sync_semaphore.release()


def test_update_retypes_ccr_to_olt_resets_port_to_snmp_default(app, client):
    """Retyping without an explicit api_port must not leave the old type's
    port stale -- a CCR's 8728 has no meaning once the device is an SNMP OLT."""
    hdr = make_tenant(client, "Api M", "api_m_admin")
    ccr = make_ccr(client, hdr)
    assert ccr["api_port"] == 8728
    r = client.put(f"/api/network-devices/{ccr['id']}", headers=hdr,
                   json={"device_type": "vsol_olt"})
    assert r.status_code == 200
    assert r.get_json()["device"]["api_port"] == 161


def test_update_retypes_olt_to_ccr_resets_port_to_routeros_default(app, client):
    hdr = make_tenant(client, "Api N", "api_n_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    assert olt["api_port"] == 161
    r = client.put(f"/api/network-devices/{olt['id']}", headers=hdr,
                   json={"device_type": "mikrotik_ccr"})
    assert r.status_code == 200
    assert r.get_json()["device"]["api_port"] == 8728


def test_update_retype_with_explicit_api_port_keeps_the_explicit_value(app, client):
    """A caller who states a port means it, even on the same request that
    changes device_type."""
    hdr = make_tenant(client, "Api O", "api_o_admin")
    ccr = make_ccr(client, hdr)
    r = client.put(f"/api/network-devices/{ccr['id']}", headers=hdr,
                   json={"device_type": "vsol_olt", "api_port": 5000})
    assert r.status_code == 200
    assert r.get_json()["device"]["api_port"] == 5000


def test_update_without_changing_device_type_leaves_api_port_untouched(app, client):
    """An unrelated edit must not clobber a deliberately customised port."""
    hdr = make_tenant(client, "Api P", "api_p_admin")
    ccr = make_ccr(client, hdr)
    custom = client.put(f"/api/network-devices/{ccr['id']}", headers=hdr,
                         json={"api_port": 9999})
    assert custom.get_json()["device"]["api_port"] == 9999
    r = client.put(f"/api/network-devices/{ccr['id']}", headers=hdr,
                   json={"name": "Renamed CCR"})
    assert r.status_code == 200
    assert r.get_json()["device"]["api_port"] == 9999


def test_update_retype_with_use_tls_false_still_uses_snmp_default(app, client):
    """The OLT default ignores use_tls entirely -- 161 either way."""
    hdr = make_tenant(client, "Api Q", "api_q_admin")
    ccr = make_ccr(client, hdr)
    r = client.put(f"/api/network-devices/{ccr['id']}", headers=hdr,
                   json={"device_type": "vsol_olt", "use_tls": False})
    assert r.status_code == 200
    assert r.get_json()["device"]["api_port"] == 161


def test_update_rejects_non_integer_parent_device_id(app, client):
    hdr = make_tenant(client, "Api R", "api_r_admin")
    ccr = make_ccr(client, hdr)
    r = client.put(f"/api/network-devices/{ccr['id']}", headers=hdr,
                   json={"parent_device_id": "not-a-number"})
    assert r.status_code == 400
    assert "must be an integer" in r.get_json()["error"]


def test_update_rejects_parent_device_id_that_matches_no_tenant(app, client):
    hdr = make_tenant(client, "Api S", "api_s_admin")
    ccr = make_ccr(client, hdr)
    r = client.put(f"/api/network-devices/{ccr['id']}", headers=hdr,
                   json={"parent_device_id": 999999})
    assert r.status_code == 400
    assert "does not match a device in this tenant" in r.get_json()["error"]
