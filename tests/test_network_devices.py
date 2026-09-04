"""Endpoint tests for NetworkDevice -- tenant-scoped, on-demand RouterOS
health checks. See
docs/superpowers/specs/2026-09-01-network-device-health-monitoring-design.md.
"""
import app as appmod
from tests.conftest import make_tenant


def _create_device(client, hdr, name="Core CCR", host="10.0.0.1"):
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": name, "host": host, "username": "admin", "password": "pw",
        "device_type": "mikrotik_ccr",
    })
    return r.get_json()["device"]["id"]


def test_create_and_list_network_device(client):
    hdr = make_tenant(client, "Biz A", "a_admin")
    device_id = _create_device(client, hdr)

    r = client.get("/api/network-devices", headers=hdr)
    assert r.status_code == 200
    devices = r.get_json()
    assert len(devices) == 1
    assert devices[0]["id"] == device_id
    assert devices[0]["name"] == "Core CCR"
    assert "password" not in devices[0]


def test_create_network_device_requires_password(client):
    hdr = make_tenant(client, "Biz B", "b_admin")
    r = client.post("/api/network-devices", headers=hdr, json={
        "name": "Core CCR", "host": "10.0.0.1", "username": "admin",
    })
    assert r.status_code == 400


def test_update_network_device(client):
    hdr = make_tenant(client, "Biz C", "c_admin")
    device_id = _create_device(client, hdr)

    r = client.put(f"/api/network-devices/{device_id}", headers=hdr, json={"name": "Renamed CCR"})
    assert r.status_code == 200
    assert r.get_json()["device"]["name"] == "Renamed CCR"


def test_delete_network_device(client):
    hdr = make_tenant(client, "Biz D", "d_admin")
    device_id = _create_device(client, hdr)

    r = client.delete(f"/api/network-devices/{device_id}", headers=hdr)
    assert r.status_code == 200

    r = client.get("/api/network-devices", headers=hdr)
    assert r.get_json() == []


def test_network_devices_are_tenant_isolated(client):
    hdr_a = make_tenant(client, "Biz E", "e_admin")
    hdr_b = make_tenant(client, "Biz F", "f_admin")
    _create_device(client, hdr_a)

    r = client.get("/api/network-devices", headers=hdr_b)
    assert r.get_json() == []


def test_check_now_success(client, monkeypatch):
    hdr = make_tenant(client, "Biz G", "g_admin")
    device_id = _create_device(client, hdr)

    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda server: (True, {
        "identity": "ccr-router", "uptime": "1w2d",
        "interfaces": [{"name": "ether1", "running": True, "disabled": False}],
    }))

    r = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True

    polled = client.get(f"/api/network-jobs/{body['job_id']}", headers=hdr).get_json()
    assert polled["status"] == "done"
    assert polled["result"]["identity"] == "ccr-router"
    assert polled["result"]["interfaces"][0]["name"] == "ether1"


def test_check_now_failure_surfaces_message(client, monkeypatch):
    hdr = make_tenant(client, "Biz H", "h_admin")
    device_id = _create_device(client, hdr)

    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda server: (False, "Connection refused"))

    r = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True  # the job was created; the connector failure is inside it

    polled = client.get(f"/api/network-jobs/{body['job_id']}", headers=hdr).get_json()
    assert polled["status"] == "done"
    assert polled["error"] == "Connection refused"
    assert polled["result"] is None


def test_set_interface_label_persists_on_the_device(client, monkeypatch):
    """The PATCH endpoint persists the label on the device, and the label is
    merged back onto the interface at read time when the job is polled --
    device.interface_labels is cloud-side data, joined in by get_network_job,
    since the connector (possibly running on an on-prem agent) has no access
    to it."""
    hdr = make_tenant(client, "Biz I", "i_admin")
    device_id = _create_device(client, hdr)

    r = client.patch(f"/api/network-devices/{device_id}/interface-labels", headers=hdr,
                     json={"interface_name": "ether1", "label": "thglobal"})
    assert r.status_code == 200
    assert r.get_json()["device"]["interface_labels"] == {"ether1": "thglobal"}

    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda server: (True, {
        "identity": "ccr-router", "uptime": "1w2d",
        "interfaces": [{"name": "ether1", "running": True, "disabled": False}],
    }))
    r = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)
    polled = client.get(f"/api/network-jobs/{r.get_json()['job_id']}", headers=hdr).get_json()
    assert polled["result"]["interfaces"][0]["name"] == "ether1"
    assert polled["result"]["interfaces"][0]["label"] == "thglobal"


def test_interface_without_a_configured_label_comes_back_as_none(client, monkeypatch):
    """An interface nobody has labeled yet must come back with label: None --
    not missing, not a KeyError -- so the frontend's label-editing UI can
    always read iface.label."""
    hdr = make_tenant(client, "Biz J", "j_admin")
    device_id = _create_device(client, hdr)

    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda server: (True, {
        "identity": "ccr-router", "uptime": "1w2d",
        "interfaces": [{"name": "ether2", "running": True, "disabled": False}],
    }))
    r = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)
    polled = client.get(f"/api/network-jobs/{r.get_json()['job_id']}", headers=hdr).get_json()
    assert polled["result"]["interfaces"][0]["label"] is None


def test_get_network_job_device_health_missing_interfaces_key_does_not_raise(client, monkeypatch):
    """A device_health result with no 'interfaces' key at all (malformed or
    from a connector variant that omits it) must be returned as-is, not 500."""
    hdr = make_tenant(client, "Biz K", "k_admin")
    device_id = _create_device(client, hdr)

    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda server: (True, {
        "identity": "ccr-router", "uptime": "1w2d",
    }))
    r = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)
    resp = client.get(f"/api/network-jobs/{r.get_json()['job_id']}", headers=hdr)
    assert resp.status_code == 200
    polled = resp.get_json()
    assert polled["result"]["identity"] == "ccr-router"
    assert "interfaces" not in polled["result"]


def test_interface_entry_missing_name_still_polls_and_gets_label_none(client, app, monkeypatch):
    """Mirrors what agent_post_result persists verbatim from an on-prem agent:
    the stored result can be whatever JSON the agent posted, with no shape
    validation. An interface entry that is a dict but has no 'name' key must
    not raise KeyError -- it should still come back enriched, with label:
    None, so a bad entry doesn't turn every subsequent poll into a 500."""
    hdr = make_tenant(client, "Biz L", "l_admin")
    device_id = _create_device(client, hdr)

    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda server: (True, {
        "identity": "ccr-router", "uptime": "1w2d",
        "interfaces": [{"name": "ether1", "running": True, "disabled": False}],
    }))
    r = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)
    job_id = r.get_json()["job_id"]

    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.result = {
            "identity": "ccr-router", "uptime": "1w2d",
            "interfaces": [{"running": True, "disabled": False}],  # no 'name'
        }
        appmod.db.session.commit()

    resp = client.get(f"/api/network-jobs/{job_id}", headers=hdr)
    assert resp.status_code == 200
    polled = resp.get_json()
    assert polled["result"]["interfaces"][0]["label"] is None


def test_interface_entry_that_is_not_a_dict_survives_untouched(client, app, monkeypatch):
    """Same agent-supplied-JSON scenario, but the entry isn't a dict at all.
    It must be passed through untouched rather than crashing or being
    dropped -- the goal is that a poll always returns, never that the data is
    silently reshaped."""
    hdr = make_tenant(client, "Biz M", "m_admin")
    device_id = _create_device(client, hdr)

    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda server: (True, {
        "identity": "ccr-router", "uptime": "1w2d",
        "interfaces": [{"name": "ether1", "running": True, "disabled": False}],
    }))
    r = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)
    job_id = r.get_json()["job_id"]

    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.result = {
            "identity": "ccr-router", "uptime": "1w2d",
            "interfaces": ["ether1"],  # not a dict at all
        }
        appmod.db.session.commit()

    resp = client.get(f"/api/network-jobs/{job_id}", headers=hdr)
    assert resp.status_code == 200
    polled = resp.get_json()
    assert polled["result"]["interfaces"][0] == "ether1"


def test_interface_entry_with_a_list_name_still_polls_and_gets_label_none(client, app, monkeypatch):
    """Fix round 3: a pre-existing row (stored before this round's validation
    existed) can carry a 'name' that isn't hashable -- e.g. a list, from a
    malformed agent payload. _with_interface_labels used to do
    labels.get(iface.get('name')) unconditionally, which raises TypeError:
    unhashable type on a list name, wedging GET /api/network-jobs/<id> for
    that job forever. It must treat any non-string name as unlabelled
    instead of raising."""
    hdr = make_tenant(client, "Biz N", "n_admin")
    device_id = _create_device(client, hdr)

    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda server: (True, {
        "identity": "ccr-router", "uptime": "1w2d",
        "interfaces": [{"name": "ether1", "running": True, "disabled": False}],
    }))
    r = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)
    job_id = r.get_json()["job_id"]

    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.result = {
            "identity": "ccr-router", "uptime": "1w2d",
            "interfaces": [{"name": ["eth1"], "running": True}],  # unhashable name
        }
        appmod.db.session.commit()

    resp = client.get(f"/api/network-jobs/{job_id}", headers=hdr)
    assert resp.status_code == 200
    polled = resp.get_json()
    assert polled["result"]["interfaces"][0]["label"] is None
    assert polled["result"]["interfaces"][0]["name"] == ["eth1"]
