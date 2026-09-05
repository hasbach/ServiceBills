"""The Network Tree page is readable by 'employee' and 'collector' as well as
admin/finance -- they need to see which ONU a customer sits behind and whether
it is down -- but the widening is read-only. The label matcher, which rewrites
Customer.onu_mac_address, and every device create/edit stay on
admin_or_finance_required(). See network_view_required() in app.py.
"""
import app as appmod
from tests.conftest import make_tenant


ONUS = [
    {"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
     "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
     "model": "V2801D", "distance_m": 531},
]


def _login(client, username, password="pw"):
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _create_user(client, admin_hdr, username, role):
    client.post("/api/users", headers=admin_hdr,
                json={"username": username, "password": "pw", "role": role})
    return _login(client, username)


def _make_olt(client, hdr):
    ccr = client.post("/api/network-devices", headers=hdr, json={
        "name": "Core CCR", "host": "10.0.0.1", "username": "admin",
        "password": "secret", "device_type": "mikrotik_ccr"}).get_json()["device"]
    return client.post("/api/network-devices", headers=hdr, json={
        "name": "EPON OLT", "host": "192.168.8.100", "password": "public",
        "device_type": "vsol_olt", "parent_device_id": ccr["id"],
    }).get_json()["device"]


def test_employee_and_collector_can_read_the_tree_and_run_a_check(app, client, monkeypatch):
    admin_hdr = make_tenant(client, "Roles A", "roles_a_admin")
    olt = _make_olt(client, admin_hdr)
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))

    for username, role in (("roles_a_emp", "employee"), ("roles_a_col", "collector")):
        hdr = _create_user(client, admin_hdr, username, role)

        tree = client.get("/api/network-tree", headers=hdr)
        assert tree.status_code == 200, role
        assert len(tree.get_json()["tree"]) == 1

        started = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr)
        assert started.status_code == 200, role
        job_id = started.get_json()["job_id"]

        polled = client.get(f"/api/network-jobs/{job_id}", headers=hdr)
        assert polled.status_code == 200, role
        assert polled.get_json()["result"][0]["mac_address"] == "b4:64:15:3f:c1:94"

        # The agent chip on the page reads this; without it the page would
        # render "agent offline" for these roles even when it is up.
        assert client.get("/api/network-agents", headers=hdr).status_code == 200, role


def test_employee_and_collector_cannot_rewrite_customer_onu_links(app, client):
    """The read widening must not widen who can rewrite the customer<->ONU
    mapping: the matcher's endpoints stay admin/finance."""
    admin_hdr = make_tenant(client, "Roles B", "roles_b_admin")
    olt = _make_olt(client, admin_hdr)

    for username, role in (("roles_b_emp", "employee"), ("roles_b_col", "collector")):
        hdr = _create_user(client, admin_hdr, username, role)
        assert client.get(f"/api/network-tree/olt/{olt['id']}/label-matches",
                          headers=hdr).status_code == 403, role
        assert client.post(f"/api/network-tree/olt/{olt['id']}/label-matches/apply",
                           headers=hdr, json={"links": []}).status_code == 403, role


def test_employee_and_collector_cannot_change_devices_or_the_agent(app, client):
    admin_hdr = make_tenant(client, "Roles C", "roles_c_admin")
    olt = _make_olt(client, admin_hdr)

    for username, role in (("roles_c_emp", "employee"), ("roles_c_col", "collector")):
        hdr = _create_user(client, admin_hdr, username, role)
        assert client.post("/api/network-devices", headers=hdr, json={
            "name": "Rogue", "host": "10.0.0.9", "username": "admin",
            "password": "x", "device_type": "mikrotik_ccr"}).status_code == 403, role
        assert client.put(f"/api/network-devices/{olt['id']}", headers=hdr,
                          json={"name": "Renamed"}).status_code == 403, role
        assert client.delete(f"/api/network-devices/{olt['id']}",
                             headers=hdr).status_code == 403, role
        assert client.post("/api/network-agents", headers=hdr,
                           json={"name": "Rogue agent"}).status_code == 403, role


def test_a_role_outside_the_allowlist_is_still_refused(app, client):
    """network_view_required() is an allowlist, not 'anyone logged in'."""
    admin_hdr = make_tenant(client, "Roles D", "roles_d_admin")
    hdr = _create_user(client, admin_hdr, "roles_d_tech", "technician")
    assert client.get("/api/network-tree", headers=hdr).status_code == 403
    assert client.get("/api/network-agents", headers=hdr).status_code == 403


def test_the_tree_stays_tenant_scoped_for_the_widened_roles(app, client):
    """Widening by role must not widen across tenants: another business's
    employee sees nothing, not a 403-free view of someone else's network."""
    hdr_one = make_tenant(client, "Roles E1", "roles_e1_admin")
    _make_olt(client, hdr_one)

    hdr_two = make_tenant(client, "Roles E2", "roles_e2_admin")
    emp_two = _create_user(client, hdr_two, "roles_e2_emp", "employee")
    assert client.get("/api/network-tree", headers=emp_two).get_json()["tree"] == []
