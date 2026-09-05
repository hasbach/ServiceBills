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
    customer = polled["result"][0]["customers"][0]
    assert customer["name"] == "Upper Mac"
    # The customer carries the MAC it is linked BY, in the spelling stored on
    # the customer row -- not the normalized lookup key and not the OLT's own
    # lowercase spelling (the ONU here reports 'b4:64:15:3f:c1:94'). That is
    # what makes a link made against an oddly formatted MAC visible on the page.
    assert customer["onu_mac_address"] == "B4:64:15:3F:C1:94"
    assert polled["result"][0]["mac_address"] == "b4:64:15:3f:c1:94"


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


def test_onu_entry_missing_mac_address_still_polls_and_shows_the_onu(app, client, monkeypatch):
    """Mirrors what agent_post_result persists verbatim from an on-prem agent:
    the stored result can be whatever JSON the agent posted, with no shape
    validation (see tests/test_network_devices.py's interface-label
    equivalents for the device_health path this mirrors). An ONU entry that
    is a dict but has no 'mac_address' key must not raise KeyError -- it
    should still come back in the tree, with an empty 'customers' list
    (it can't be matched to anyone without a MAC), so a bad entry doesn't
    turn every subsequent poll of that job into a 500."""
    hdr = make_tenant(client, "Tree N", "tree_n_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))

    started = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr).get_json()
    job_id = started["job_id"]

    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.result = [{"pon_port": "PON1", "description": "NoMac"}]  # no mac_address
        appmod.db.session.commit()

    resp = client.get(f"/api/network-jobs/{job_id}", headers=hdr)
    assert resp.status_code == 200
    polled = resp.get_json()
    assert polled["result"][0]["customers"] == []
    assert polled["result"][0]["description"] == "NoMac"


def test_onu_entry_that_is_not_a_dict_survives_untouched(app, client, monkeypatch):
    """Same agent-supplied-JSON scenario, but the entry isn't a dict at all.
    It must be passed through untouched rather than crashing or being
    dropped -- the goal is that a poll always returns, never that the data
    is silently reshaped."""
    hdr = make_tenant(client, "Tree O", "tree_o_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))

    started = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr).get_json()
    job_id = started["job_id"]

    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.result = ["onu1"]  # not a dict at all
        appmod.db.session.commit()

    resp = client.get(f"/api/network-jobs/{job_id}", headers=hdr)
    assert resp.status_code == 200
    polled = resp.get_json()
    assert polled["result"][0] == "onu1"


# --- Fix round 2: agent_post_result now validates a result's shape before
# storing it (see _validate_agent_result), but pre-existing rows stored
# before that validation existed can still carry the two newly-found
# malformed shapes -- job.result itself not being a list, and a
# mac_address that isn't a string. The read-path guards in
# _resolve_onu_customers must degrade on both rather than raising. These
# write the malformed value directly to job.result, bypassing validation,
# exactly as a pre-existing row would look. ---

def test_non_list_job_result_still_polls_cleanly(app, client, monkeypatch):
    """job.result itself (not one entry) can be a non-list -- e.g. a stray
    int -- on a row stored before validation existed. `for onu in
    job.result` would raise TypeError; _resolve_onu_customers must treat
    this as nothing to enrich and hand it back unchanged instead."""
    hdr = make_tenant(client, "Tree P", "tree_p_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))

    started = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr).get_json()
    job_id = started["job_id"]

    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.result = 1  # not a list at all
        appmod.db.session.commit()

    resp = client.get(f"/api/network-jobs/{job_id}", headers=hdr)
    assert resp.status_code == 200
    assert resp.get_json()["result"] == 1


def test_onu_entry_with_a_non_string_mac_address_still_polls_and_shows_the_onu(
        app, client, monkeypatch):
    """A plausible agent bug: a colon-less MAC parsed as a number. normalize_
    mac's old (mac or '').strip() raised AttributeError on an int; it must
    now treat a non-string mac_address the same as a missing one (empty
    'customers', entry otherwise shown unchanged)."""
    hdr = make_tenant(client, "Tree Q", "tree_q_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))

    started = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr).get_json()
    job_id = started["job_id"]

    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.result = [{"pon_port": "PON1", "description": "IntMac",
                       "mac_address": 123456789012}]
        appmod.db.session.commit()

    resp = client.get(f"/api/network-jobs/{job_id}", headers=hdr)
    assert resp.status_code == 200
    polled = resp.get_json()
    assert polled["result"][0]["customers"] == []
    assert polled["result"][0]["description"] == "IntMac"


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


# --- Tree v2: the page renders from the last known result, so the endpoint
# carries it. Jobs already store results for 7 days; this is a read, not new
# storage. See docs/superpowers/specs/2026-09-05-network-tree-v2-design.md.

def _tree_by_id(client, hdr):
    def walk(nodes, out):
        for n in nodes:
            out[n["id"]] = n
            walk(n.get("children") or [], out)
        return out
    return walk(client.get("/api/network-tree", headers=hdr).get_json()["tree"], {})


def test_tree_carries_the_newest_completed_olt_result(app, client, monkeypatch):
    hdr = make_tenant(client, "Tree P", "tree_p_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    add_customer(app, "Tree P", "Moussa Ghadir", "b4:64:15:3f:c1:94")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))
    refresh_and_poll(client, hdr, olt["id"])

    node = _tree_by_id(client, hdr)[olt["id"]]
    assert node["last_result_operation"] == "olt_status"
    assert node["last_result_at"]
    macs = [o["mac_address"] for o in node["last_result"]]
    assert "b4:64:15:3f:c1:94" in macs
    # Enriched at read time, exactly as the job-poll endpoint does it.
    first = node["last_result"][0]
    assert [c["name"] for c in first["customers"]] == ["Moussa Ghadir"]


def test_tree_carries_the_ccr_result_with_interface_labels_applied(app, client, monkeypatch):
    hdr = make_tenant(client, "Tree Q", "tree_q_admin")
    ccr = make_ccr(client, hdr)
    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda d: (
        True, {"identity": "CCR", "uptime": "1d",
               "interfaces": [{"name": "ether1", "running": True, "disabled": False}]}))
    client.patch(f"/api/network-devices/{ccr['id']}/interface-labels", headers=hdr,
                 json={"interface_name": "ether1", "label": "MYISP"})
    started = client.post(f"/api/network-devices/{ccr['id']}/check-now", headers=hdr).get_json()
    assert started["ok"] is True

    node = _tree_by_id(client, hdr)[ccr["id"]]
    assert node["last_result_operation"] == "device_health"
    assert node["last_result"]["interfaces"][0]["label"] == "MYISP"


def test_tree_reports_no_result_for_a_device_that_has_never_been_checked(app, client):
    hdr = make_tenant(client, "Tree R", "tree_r_admin")
    ccr = make_ccr(client, hdr)
    node = _tree_by_id(client, hdr)[ccr["id"]]
    assert node["last_result"] is None
    assert node["last_result_at"] is None
    assert node["last_result_operation"] is None


def test_tree_ignores_jobs_that_are_not_done(app, client, monkeypatch):
    """A pending or failed job must not be mistaken for a result."""
    hdr = make_tenant(client, "Tree S", "tree_s_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))
    started = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr).get_json()
    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, started["job_id"])
        job.status = "failed"
        appmod.db.session.commit()

    assert _tree_by_id(client, hdr)[olt["id"]]["last_result"] is None


def test_tree_carries_the_newer_of_two_completed_jobs_for_the_same_device(app, client, monkeypatch):
    """Pins 'newest wins' for _latest_results_by_device: ORDER BY created_at
    DESC with an id DESC tiebreak. Nothing else in the suite creates two
    completed jobs for one device, so a regression that flipped .desc() to
    .asc() (on either column), or broke the one-per-device reduction, would
    otherwise pass the whole suite. The two jobs' created_at is forced equal
    after the fact -- jobs for one device really can share a created_at at
    SQLite's resolution -- so the assertion actually exercises the id DESC
    tiebreak, not just created_at ordering."""
    hdr = make_tenant(client, "Tree U", "tree_u_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])

    older_onu = [{"pon_port": "PON1", "onu_id": "EPON0/1:9", "status": "online",
                  "mac_address": "aa:aa:aa:aa:aa:01", "description": "OlderResult",
                  "model": "V2801D", "distance_m": 100}]
    newer_onu = [{"pon_port": "PON1", "onu_id": "EPON0/1:9", "status": "online",
                  "mac_address": "bb:bb:bb:bb:bb:02", "description": "NewerResult",
                  "model": "V2801D", "distance_m": 200}]

    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, older_onu))
    older = refresh_and_poll(client, hdr, olt["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, newer_onu))
    newer = refresh_and_poll(client, hdr, olt["id"])
    assert newer["id"] > older["id"]

    with app.app_context():
        older_job = appmod.db.session.get(appmod.NetworkAgentJob, older["id"])
        newer_job = appmod.db.session.get(appmod.NetworkAgentJob, newer["id"])
        newer_job.created_at = older_job.created_at
        appmod.db.session.commit()

    node = _tree_by_id(client, hdr)[olt["id"]]
    assert node["last_result"][0]["description"] == "NewerResult"


def test_tree_results_stay_tenant_scoped(app, client, monkeypatch):
    hdr_one = make_tenant(client, "Tree T1", "tree_t1_admin")
    ccr_one = make_ccr(client, hdr_one)
    olt_one = make_olt(client, hdr_one, ccr_one["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))
    refresh_and_poll(client, hdr_one, olt_one["id"])

    hdr_two = make_tenant(client, "Tree T2", "tree_t2_admin")
    ccr_two = make_ccr(client, hdr_two)
    olt_two = make_olt(client, hdr_two, ccr_two["id"])
    assert _tree_by_id(client, hdr_two)[olt_two["id"]]["last_result"] is None
