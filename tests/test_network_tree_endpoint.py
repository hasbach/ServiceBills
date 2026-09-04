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


def refresh_and_poll(client, hdr, olt_id):
    """The refresh endpoint is job-based (Task 4): POST starts an olt_status
    walk -- or, in 'direct' mode (the default here; these tests never create
    a BusinessSettings row), runs it inline -- and returns a job_id. The
    enriched ONU list, with customers attached, comes back from polling that
    job (see get_network_job's _resolve_onu_customers call), not from the
    POST response itself."""
    started = client.post(f"/api/network-tree/olt/{olt_id}/refresh", headers=hdr).get_json()
    assert started["ok"] is True and started["job_id"], started
    return client.get(f"/api/network-jobs/{started['job_id']}", headers=hdr).get_json()


def test_refresh_resolves_many_customers_behind_one_onu(app, client, monkeypatch):
    hdr = make_tenant(client, "Tree D", "tree_d_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    add_customer(app, "Tree D", "Moussa Ghadir", "b4:64:15:3f:c1:94")
    add_customer(app, "Tree D", "Second Behind Same ONU", "b4:64:15:3f:c1:94")
    add_customer(app, "Tree D", "Unlinked Person", None)
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))

    polled = refresh_and_poll(client, hdr, olt["id"])
    assert polled["status"] == "done"
    first, second = polled["result"]
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
    polled = refresh_and_poll(client, hdr, olt["id"])
    assert [c["name"] for c in polled["result"][0]["customers"]] == ["Upper Mac"]


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
    # In direct mode the job is created successfully (ok:true) regardless of
    # whether the connector itself succeeds -- the connector failure surfaces
    # on the job once polled, as an error with no result, not as ok:false on
    # the POST. (The ok:false path belongs to _create_device_job's own
    # refusal -- e.g. agent mode with no online agent -- covered in
    # tests/test_network_agent_jobs.py.)
    polled = refresh_and_poll(client, hdr, olt["id"])
    assert polled["status"] == "done"
    assert polled["result"] is None
    assert "timeout" in polled["error"]


def _walk_ids(nodes):
    """Flatten a tree's node ids (depth-first), duplicates and all -- used to
    assert both presence and uniqueness across the whole returned structure."""
    ids = []
    for node in nodes:
        ids.append(node["id"])
        ids.extend(_walk_ids(node["children"]))
    return ids


# --- Cyclic parent_device_id data. These rows are written directly through
# the model (appmod.db.session), not through the API: Task 3's cycle guard
# rejects cycles at the API layer, so the only way to prove the tree builder
# itself survives corrupted same-tenant data (pre-existing rows, or rows
# written directly, per the documented migration/schema drift on this
# project) is to bypass that guard and write the bad rows ourselves. ---

def test_self_parented_device_still_appears_in_the_tree(app, client):
    hdr = make_tenant(client, "Tree H", "tree_h_admin")
    ccr = make_ccr(client, hdr)
    with app.app_context():
        device = appmod.NetworkDevice.query.filter_by(id=ccr["id"]).first()
        device.parent_device_id = device.id
        appmod.db.session.commit()

    ids = _walk_ids(client.get("/api/network-tree", headers=hdr).get_json()["tree"])
    assert ids == [ccr["id"]]


def test_two_device_parent_cycle_both_appear_and_endpoint_returns(app, client):
    hdr = make_tenant(client, "Tree I", "tree_i_admin")
    device_a = make_ccr(client, hdr)
    device_b = make_ccr(client, hdr)
    with app.app_context():
        a = appmod.NetworkDevice.query.filter_by(id=device_a["id"]).first()
        b = appmod.NetworkDevice.query.filter_by(id=device_b["id"]).first()
        a.parent_device_id = b.id
        b.parent_device_id = a.id
        appmod.db.session.commit()

    # The point of this assertion is as much "it returns at all" (rather than
    # hanging in infinite recursion) as it is the content of the response.
    resp = client.get("/api/network-tree", headers=hdr)
    assert resp.status_code == 200
    ids = _walk_ids(resp.get_json()["tree"])
    assert sorted(ids) == sorted([device_a["id"], device_b["id"]])


def test_healthy_device_parented_by_a_cycle_member_still_appears(app, client):
    hdr = make_tenant(client, "Tree J", "tree_j_admin")
    device_a = make_ccr(client, hdr)
    device_b = make_ccr(client, hdr)
    healthy = make_olt(client, hdr, device_a["id"])
    with app.app_context():
        a = appmod.NetworkDevice.query.filter_by(id=device_a["id"]).first()
        b = appmod.NetworkDevice.query.filter_by(id=device_b["id"]).first()
        a.parent_device_id = b.id
        b.parent_device_id = a.id
        appmod.db.session.commit()

    ids = _walk_ids(client.get("/api/network-tree", headers=hdr).get_json()["tree"])
    assert healthy["id"] in ids


def test_every_device_appears_exactly_once_despite_a_cycle(app, client):
    """The assertion that most directly encodes 'nothing vanishes': walk the
    whole returned structure and confirm every device in the tenant shows up,
    and shows up exactly once -- not zero times (swallowed by the cycle) and
    not twice (double-rendered by the promotion pass)."""
    hdr = make_tenant(client, "Tree K", "tree_k_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    cycle_a = make_ccr(client, hdr)
    cycle_b = make_ccr(client, hdr)
    healthy_leaf = make_olt(client, hdr, cycle_a["id"])
    with app.app_context():
        a = appmod.NetworkDevice.query.filter_by(id=cycle_a["id"]).first()
        b = appmod.NetworkDevice.query.filter_by(id=cycle_b["id"]).first()
        a.parent_device_id = b.id
        b.parent_device_id = a.id
        appmod.db.session.commit()

    ids = _walk_ids(client.get("/api/network-tree", headers=hdr).get_json()["tree"])
    expected = [ccr["id"], olt["id"], cycle_a["id"], cycle_b["id"], healthy_leaf["id"]]
    assert sorted(ids) == sorted(expected)
    assert len(ids) == len(set(ids))


def test_healthy_device_with_lower_id_nests_under_its_cycle_parent(app, client):
    """Fix-round-2 regression: the second pass used to promote the lowest-id
    *unvisited device* as a new root, without checking whether that device
    was itself a cycle member or just a healthy device hanging off one. When
    a healthy device's id is lower than its cycle-member parent's id, it got
    promoted first and detached from its real parent -- reproduced here with
    exactly the reviewer's counter-example: healthy id 1, cycle members with
    higher ids. The healthy device must show up nested under its real parent
    (a cycle member), never as a top-level root."""
    hdr = make_tenant(client, "Tree L", "tree_l_admin")
    healthy = make_ccr(client, hdr)   # lowest id
    cycle_a = make_ccr(client, hdr)
    cycle_b = make_ccr(client, hdr)   # highest id
    with app.app_context():
        h = appmod.NetworkDevice.query.filter_by(id=healthy["id"]).first()
        a = appmod.NetworkDevice.query.filter_by(id=cycle_a["id"]).first()
        b = appmod.NetworkDevice.query.filter_by(id=cycle_b["id"]).first()
        h.parent_device_id = a.id
        a.parent_device_id = b.id
        b.parent_device_id = a.id
        appmod.db.session.commit()

    tree = client.get("/api/network-tree", headers=hdr).get_json()["tree"]
    root_ids = [n["id"] for n in tree]
    assert healthy["id"] not in root_ids, "healthy device must not be a spurious root"
    a_node = next(n for n in tree if n["id"] == cycle_a["id"])
    assert healthy["id"] in [c["id"] for c in a_node["children"]]


def test_healthy_device_with_higher_id_nests_under_its_cycle_parent(app, client):
    """Mirror of the test above with ids reversed -- the healthy device's id
    is now higher than both cycle members'. Pins the nesting behaviour
    independent of id ordering, rather than letting the suite accidentally
    pass only because of which arrangement happened to be tested."""
    hdr = make_tenant(client, "Tree M", "tree_m_admin")
    cycle_a = make_ccr(client, hdr)   # lowest id
    cycle_b = make_ccr(client, hdr)
    healthy = make_ccr(client, hdr)   # highest id
    with app.app_context():
        a = appmod.NetworkDevice.query.filter_by(id=cycle_a["id"]).first()
        b = appmod.NetworkDevice.query.filter_by(id=cycle_b["id"]).first()
        h = appmod.NetworkDevice.query.filter_by(id=healthy["id"]).first()
        a.parent_device_id = b.id
        b.parent_device_id = a.id
        h.parent_device_id = a.id
        appmod.db.session.commit()

    tree = client.get("/api/network-tree", headers=hdr).get_json()["tree"]
    root_ids = [n["id"] for n in tree]
    assert healthy["id"] not in root_ids, "healthy device must not be a spurious root"
    a_node = next(n for n in tree if n["id"] == cycle_a["id"])
    assert healthy["id"] in [c["id"] for c in a_node["children"]]
