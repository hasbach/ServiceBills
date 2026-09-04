"""Tests for the Network Tree endpoints: tree assembly from parent_device_id,
and ONU -> customer resolution by MAC (including the many-customers-behind-
one-ONU case). See docs/superpowers/specs/2026-09-01-network-topology-tree-design.md."""
import app as appmod
from tests.conftest import make_tenant

ONUS = [
    {"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
     "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
     "model": "V2801D", "distance_m": 531},
    {"pon_port": "PON1", "onu_id": "EPON0/1:5", "status": "offline",
     "mac_address": "f4:c4:d6:4d:80:e1", "description": "OstaMarket",
     "model": "unknow", "distance_m": 0},
]


def make_ccr(client, hdr):
    return client.post("/api/network-devices", headers=hdr, json={
        "name": "Core CCR", "host": "10.0.0.1", "username": "admin",
        "password": "secret", "device_type": "mikrotik_ccr",
    }).get_json()["device"]


def make_olt(client, hdr, parent_id):
    return client.post("/api/network-devices", headers=hdr, json={
        "name": "EPON OLT", "host": "192.168.8.100", "password": "public",
        "device_type": "vsol_olt", "parent_device_id": parent_id,
    }).get_json()["device"]


def add_customer(app, tenant_name, name, mac=None):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        plan = appmod.SubscriptionPlan.query.filter_by(tenant_id=tenant.id).first()
        if plan is None:
            plan = appmod.SubscriptionPlan(
                tenant_id=tenant.id, name="Basic", price=10, cost=5,
                billing_cycle="monthly", currency="USD")
            appmod.db.session.add(plan)
            appmod.db.session.commit()
        customer = appmod.Customer(
            tenant_id=tenant.id, name=name, phone="1", address="a",
            subscription_plan_id=plan.id,
            subscription_expiry_date=appmod.datetime.utcnow(),
            onu_mac_address=mac)
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        return customer.id


def test_tree_nests_the_olt_under_the_ccr(app, client):
    hdr = make_tenant(client, "Tree A", "tree_a_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    body = client.get("/api/network-tree", headers=hdr).get_json()
    assert len(body["tree"]) == 1
    root = body["tree"][0]
    assert root["id"] == ccr["id"]
    assert root["device_type"] == "mikrotik_ccr"
    assert len(root["children"]) == 1
    assert root["children"][0]["id"] == olt["id"]
    assert root["children"][0]["children"] == []


def test_tree_is_tenant_scoped(app, client):
    hdr_one = make_tenant(client, "Tree B1", "tree_b1_admin")
    make_ccr(client, hdr_one)
    hdr_two = make_tenant(client, "Tree B2", "tree_b2_admin")
    assert client.get("/api/network-tree", headers=hdr_two).get_json()["tree"] == []


def test_device_with_an_unreachable_parent_still_appears_as_a_root(app, client):
    """A device whose parent isn't visible to this tenant must not vanish from
    the tree. Uses another tenant's device as the parent so the row is a real,
    FK-valid target -- the point is that _build_device_tree only ever sees this
    tenant's devices, so that parent is absent from its lookup map."""
    hdr_other = make_tenant(client, "Tree C-other", "tree_c_other_admin")
    foreign = make_ccr(client, hdr_other)

    hdr = make_tenant(client, "Tree C", "tree_c_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    with app.app_context():
        stranded = appmod.NetworkDevice.query.filter_by(id=olt["id"]).first()
        stranded.parent_device_id = foreign["id"]
        appmod.db.session.commit()

    ids = [n["id"] for n in client.get("/api/network-tree", headers=hdr).get_json()["tree"]]
    assert sorted(ids) == sorted([ccr["id"], olt["id"]])


def test_refresh_resolves_many_customers_behind_one_onu(app, client, monkeypatch):
    hdr = make_tenant(client, "Tree D", "tree_d_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    add_customer(app, "Tree D", "Moussa Ghadir", "b4:64:15:3f:c1:94")
    add_customer(app, "Tree D", "Second Behind Same ONU", "b4:64:15:3f:c1:94")
    add_customer(app, "Tree D", "Unlinked Person", None)
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))

    body = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr).get_json()
    assert body["ok"] is True
    first, second = body["onus"]
    assert sorted(c["name"] for c in first["customers"]) == [
        "Moussa Ghadir", "Second Behind Same ONU"]
    # An ONU nobody is linked to still renders, with its OLT label intact.
    assert second["customers"] == []
    assert second["description"] == "OstaMarket"


def test_refresh_matches_mac_case_insensitively(app, client, monkeypatch):
    hdr = make_tenant(client, "Tree E", "tree_e_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    add_customer(app, "Tree E", "Upper Mac", "B4:64:15:3F:C1:94")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))
    body = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr).get_json()
    assert [c["name"] for c in body["onus"][0]["customers"]] == ["Upper Mac"]


def test_refresh_on_a_non_olt_is_rejected(app, client):
    hdr = make_tenant(client, "Tree F", "tree_f_admin")
    ccr = make_ccr(client, hdr)
    r = client.post(f"/api/network-tree/olt/{ccr['id']}/refresh", headers=hdr)
    assert r.status_code == 400


def test_refresh_surfaces_connector_failure(app, client, monkeypatch):
    hdr = make_tenant(client, "Tree G", "tree_g_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (False, "No SNMP response received before timeout"))
    body = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr).get_json()
    assert body["ok"] is False
    assert body["onus"] is None
    assert "timeout" in body["message"]
