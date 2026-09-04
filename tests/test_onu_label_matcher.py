"""Tests for the one-off ONU-label -> customer matcher that backfills
Customer.onu_mac_address. 71 of the real OLT's 87 ONUs already carry a staff
label that is usually the customer's name, so this saves hand-typing MACs --
but nothing is written until the user approves.
See docs/superpowers/specs/2026-09-01-network-topology-tree-design.md."""
import app as appmod
from tests.conftest import make_tenant


def onu(mac, description, pon="PON1", onu_id="EPON0/1:1", status="online"):
    return {"pon_port": pon, "onu_id": onu_id, "status": status,
            "mac_address": mac, "description": description,
            "model": "V2801D", "distance_m": 100}


def setup_devices(client, hdr):
    ccr = client.post("/api/network-devices", headers=hdr, json={
        "name": "Core CCR", "host": "10.0.0.1", "username": "admin",
        "password": "secret", "device_type": "mikrotik_ccr",
    }).get_json()["device"]
    olt = client.post("/api/network-devices", headers=hdr, json={
        "name": "EPON OLT", "host": "192.168.8.100", "password": "public",
        "device_type": "vsol_olt", "parent_device_id": ccr["id"],
    }).get_json()["device"]
    return olt


def get_label_matches(client, hdr, olt_id):
    """The label-matches endpoint is two-phase: a bare GET starts an
    olt_status walk and hands back its job_id; the proposals themselves only
    come back once that job_id is passed back in. In 'direct' mode (the
    default for these tests -- no BusinessSettings row is created) the walk
    runs inline, so the job is already done by the time the first call
    returns and this helper's second call gets the real proposals straight
    away."""
    started = client.get(f"/api/network-tree/olt/{olt_id}/label-matches",
                         headers=hdr).get_json()
    assert started["job_id"], started
    return client.get(
        f"/api/network-tree/olt/{olt_id}/label-matches?job_id={started['job_id']}",
        headers=hdr).get_json()


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


def test_squashed_label_matches_spaced_customer_name(app, client, monkeypatch):
    """The real OLT stores 'MoussaGhadir' for a customer named 'Moussa Ghadir'."""
    hdr = make_tenant(client, "Match A", "match_a_admin")
    olt = setup_devices(client, hdr)
    cid = add_customer(app, "Match A", "Moussa Ghadir")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("b4:64:15:3f:c1:94", "MoussaGhadir")]))
    body = get_label_matches(client, hdr, olt['id'])
    assert body["ok"] is True
    assert len(body["proposals"]) == 1
    proposal = body["proposals"][0]
    assert proposal["customer"]["id"] == cid
    assert proposal["onu"]["mac_address"] == "b4:64:15:3f:c1:94"
    assert proposal["confidence"] == 1.0


def test_case_and_punctuation_are_ignored(app, client, monkeypatch):
    """'aLIhACHEM' is a real label; the customer is 'Ali Hachem'."""
    hdr = make_tenant(client, "Match B", "match_b_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match B", "Ali Hachem")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("f4:c4:d6:4d:88:81", "aLIhACHEM")]))
    body = get_label_matches(client, hdr, olt['id'])
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["confidence"] == 1.0


def test_unrelated_label_produces_no_proposal(app, client, monkeypatch):
    hdr = make_tenant(client, "Match C", "match_c_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match C", "Completely Different Person")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("aa:bb:cc:dd:ee:ff", "zein_khodor")]))
    body = get_label_matches(client, hdr, olt['id'])
    assert body["proposals"] == []
    assert len(body["unmatched_onus"]) == 1
    assert len(body["unmatched_customers"]) == 1


def test_unlabelled_onus_are_never_proposed(app, client, monkeypatch):
    hdr = make_tenant(client, "Match D", "match_d_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match D", "Somebody")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("aa:bb:cc:dd:ee:ff", None)]))
    body = get_label_matches(client, hdr, olt['id'])
    assert body["proposals"] == []
    assert body["unmatched_onus"][0]["mac_address"] == "aa:bb:cc:dd:ee:ff"


def test_already_linked_customers_are_not_proposed_again(app, client, monkeypatch):
    hdr = make_tenant(client, "Match E", "match_e_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match E", "Moussa Ghadir", mac="b4:64:15:3f:c1:94")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("b4:64:15:3f:c1:94", "MoussaGhadir")]))
    body = get_label_matches(client, hdr, olt['id'])
    assert body["proposals"] == []
    assert body["unmatched_customers"] == []


def test_one_customer_is_proposed_at_most_once(app, client, monkeypatch):
    """Two ONUs whose labels both resemble one customer must not both claim it."""
    hdr = make_tenant(client, "Match F", "match_f_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match F", "Taleb Caffe")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, [
        onu("aa:00:00:00:00:01", "TalebCaffe", onu_id="EPON0/1:1"),
        onu("aa:00:00:00:00:02", "TalebCaffee", onu_id="EPON0/1:2"),
    ]))
    body = get_label_matches(client, hdr, olt['id'])
    assert len(body["proposals"]) == 1
    # The stronger (exact) match wins the customer; the other is left unmatched.
    assert body["proposals"][0]["onu"]["mac_address"] == "aa:00:00:00:00:01"
    assert len(body["unmatched_onus"]) == 1


def test_one_onu_label_colliding_with_two_customers_proposes_only_the_stronger_match(
        app, client, monkeypatch):
    """Mirror of test_one_customer_is_proposed_at_most_once: one ONU label
    that resembles two different customer names must not match both. The
    design spec calls this shape out explicitly (see Testing section of
    docs/superpowers/specs/2026-09-01-network-topology-tree-design.md). The
    greedy pairing in _propose_label_matches processes candidates by
    descending confidence, so the exact match wins the ONU and the weaker
    (fuzzy) match is left unmatched rather than also being proposed."""
    hdr = make_tenant(client, "Match F2", "match_f2_admin")
    olt = setup_devices(client, hdr)
    winner = add_customer(app, "Match F2", "Taleb Caffe")
    loser = add_customer(app, "Match F2", "Taleb Caffee")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("aa:00:00:00:00:03", "TalebCaffe")]))
    body = get_label_matches(client, hdr, olt['id'])
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["customer"]["id"] == winner
    assert body["proposals"][0]["confidence"] == 1.0
    assert len(body["unmatched_customers"]) == 1
    assert body["unmatched_customers"][0]["id"] == loser


def test_get_writes_nothing(app, client, monkeypatch):
    hdr = make_tenant(client, "Match G", "match_g_admin")
    olt = setup_devices(client, hdr)
    cid = add_customer(app, "Match G", "Moussa Ghadir")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("b4:64:15:3f:c1:94", "MoussaGhadir")]))
    get_label_matches(client, hdr, olt['id'])
    with app.app_context():
        assert appmod.Customer.query.filter_by(id=cid).first().onu_mac_address is None


def test_apply_writes_only_the_accepted_links(app, client, monkeypatch):
    hdr = make_tenant(client, "Match H", "match_h_admin")
    olt = setup_devices(client, hdr)
    accepted = add_customer(app, "Match H", "Moussa Ghadir")
    rejected = add_customer(app, "Match H", "Villa Eid")
    r = client.post(f"/api/network-tree/olt/{olt['id']}/label-matches/apply",
                    headers=hdr,
                    json={"links": [{"customer_id": accepted,
                                     "mac_address": "B4:64:15:3F:C1:94"}]})
    assert r.status_code == 200
    assert r.get_json()["applied"] == 1
    with app.app_context():
        # Stored normalized to lowercase so tree matching is exact.
        assert appmod.Customer.query.filter_by(id=accepted).first().onu_mac_address == \
            "b4:64:15:3f:c1:94"
        assert appmod.Customer.query.filter_by(id=rejected).first().onu_mac_address is None


def test_apply_refuses_another_tenants_customer(app, client):
    hdr_one = make_tenant(client, "Match I1", "match_i1_admin")
    victim = add_customer(app, "Match I1", "Someone Else")
    hdr_two = make_tenant(client, "Match I2", "match_i2_admin")
    olt = setup_devices(client, hdr_two)
    r = client.post(f"/api/network-tree/olt/{olt['id']}/label-matches/apply",
                    headers=hdr_two,
                    json={"links": [{"customer_id": victim,
                                     "mac_address": "aa:bb:cc:dd:ee:ff"}]})
    assert r.status_code == 400
    with app.app_context():
        assert appmod.Customer.query.filter_by(id=victim).first().onu_mac_address is None


# --- Short-name false positives (difflib's ratio is not length-normalised) ---

def test_short_names_below_length_gate_do_not_propose(app, client, monkeypatch):
    """'ali' vs 'alia' scores 0.857 on the raw difflib ratio -- comfortably
    above _LABEL_MATCH_THRESHOLD -- but these are two different people and
    both normalised strings are under _LABEL_FUZZY_MIN_LENGTH, so no
    proposal should be made."""
    hdr = make_tenant(client, "Match K", "match_k_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match K", "Ali")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("aa:bb:cc:dd:ee:01", "alia")]))
    body = get_label_matches(client, hdr, olt['id'])
    assert body["proposals"] == []


def test_short_names_below_length_gate_do_not_propose_2(app, client, monkeypatch):
    """Same false-positive shape as 'ali'/'alia': 'sam' vs 'sami' also scores
    0.857 on the raw ratio."""
    hdr = make_tenant(client, "Match L", "match_l_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match L", "Sam")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("aa:bb:cc:dd:ee:02", "sami")]))
    body = get_label_matches(client, hdr, olt['id'])
    assert body["proposals"] == []


def test_exact_short_match_still_proposes_despite_length_gate(app, client, monkeypatch):
    """The length gate must only apply to *non-exact* matches -- an exact
    match at any length (even 3 characters) still proposes at confidence
    1.0."""
    hdr = make_tenant(client, "Match M", "match_m_admin")
    olt = setup_devices(client, hdr)
    cid = add_customer(app, "Match M", "Ali")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("aa:bb:cc:dd:ee:03", "aLI")]))
    body = get_label_matches(client, hdr, olt['id'])
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["customer"]["id"] == cid
    assert body["proposals"][0]["confidence"] == 1.0


def test_long_near_miss_still_proposes(app, client, monkeypatch):
    """Genuine long near-misses (a doubled letter) must survive the length
    gate: both normalised strings are 10+ characters, well past
    _LABEL_FUZZY_MIN_LENGTH, and the ratio still clears the threshold."""
    hdr = make_tenant(client, "Match N", "match_n_admin")
    olt = setup_devices(client, hdr)
    cid = add_customer(app, "Match N", "Taleb Caffe")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("aa:bb:cc:dd:ee:04", "TalebCaffee")]))
    body = get_label_matches(client, hdr, olt['id'])
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["customer"]["id"] == cid
    assert 0.82 <= body["proposals"][0]["confidence"] < 1.0


# --- /apply must be all-or-nothing ---

def test_apply_does_not_partially_persist_on_a_later_bad_link(app, client, monkeypatch):
    """A links list whose first entry is valid and whose second entry names
    a customer from another tenant must reject the whole batch, and the
    first (valid) customer's onu_mac_address must remain untouched -- not
    just uncommitted this request, but genuinely never written."""
    hdr_outsider = make_tenant(client, "Match O1", "match_o1_admin")
    outsider = add_customer(app, "Match O1", "Outsider")
    hdr = make_tenant(client, "Match O2", "match_o2_admin")
    olt = setup_devices(client, hdr)
    valid = add_customer(app, "Match O2", "Valid Customer")
    r = client.post(f"/api/network-tree/olt/{olt['id']}/label-matches/apply",
                    headers=hdr,
                    json={"links": [
                        {"customer_id": valid, "mac_address": "aa:bb:cc:dd:ee:10"},
                        {"customer_id": outsider, "mac_address": "aa:bb:cc:dd:ee:11"},
                    ]})
    assert r.status_code == 400
    with app.app_context():
        assert appmod.Customer.query.filter_by(id=valid).first().onu_mac_address is None
        assert appmod.Customer.query.filter_by(id=outsider).first().onu_mac_address is None


# --- Minor: /apply must gate on device_type like the GET route does ---

def test_apply_refuses_non_olt_device(app, client):
    hdr = make_tenant(client, "Match P", "match_p_admin")
    ccr = client.post("/api/network-devices", headers=hdr, json={
        "name": "Core CCR", "host": "10.0.0.2", "username": "admin",
        "password": "secret", "device_type": "mikrotik_ccr",
    }).get_json()["device"]
    r = client.post(f"/api/network-tree/olt/{ccr['id']}/label-matches/apply",
                    headers=hdr, json={"links": []})
    assert r.status_code == 400


# --- /apply must 400 cleanly on a malformed link entry, not crash ---
# (fix round 1 moved validation ahead of the mutation pass but outside the
# try/except that used to catch exactly these shapes)

def test_apply_rejects_non_dict_link_entry(app, client):
    """A links list whose first entry is valid and whose second entry isn't
    even a dict (e.g. a stray string) must 400 cleanly -- not raise an
    unhandled AttributeError from calling .get() on a str -- and the valid
    entry must not be applied."""
    hdr = make_tenant(client, "Match Q", "match_q_admin")
    olt = setup_devices(client, hdr)
    valid = add_customer(app, "Match Q", "Valid Customer")
    r = client.post(f"/api/network-tree/olt/{olt['id']}/label-matches/apply",
                    headers=hdr,
                    json={"links": [
                        {"customer_id": valid, "mac_address": "aa:bb:cc:dd:ee:20"},
                        "not-a-dict",
                    ]})
    assert r.status_code == 400
    with app.app_context():
        assert appmod.Customer.query.filter_by(id=valid).first().onu_mac_address is None


def test_apply_rejects_null_link_entry(app, client):
    """A links list containing a bare null must 400 cleanly -- not raise an
    unhandled AttributeError from calling .get() on None."""
    hdr = make_tenant(client, "Match R", "match_r_admin")
    olt = setup_devices(client, hdr)
    r = client.post(f"/api/network-tree/olt/{olt['id']}/label-matches/apply",
                    headers=hdr, json={"links": [None]})
    assert r.status_code == 400


def test_apply_rejects_non_string_mac_address(app, client):
    """A mac_address that isn't a string (e.g. a bare int) must 400 cleanly
    -- not raise an unhandled AttributeError from calling .strip() on an
    int -- and the targeted customer must not be linked."""
    hdr = make_tenant(client, "Match S", "match_s_admin")
    olt = setup_devices(client, hdr)
    customer = add_customer(app, "Match S", "Someone")
    r = client.post(f"/api/network-tree/olt/{olt['id']}/label-matches/apply",
                    headers=hdr,
                    json={"links": [{"customer_id": customer, "mac_address": 123}]})
    assert r.status_code == 400
    with app.app_context():
        assert appmod.Customer.query.filter_by(id=customer).first().onu_mac_address is None


def test_apply_rejects_malformed_mac_address(app, client):
    """A mac_address that is a non-empty string but not shaped like a MAC
    (e.g. missing colons, wrong segment count, or just plain garbage) must
    400 naming the offending value -- not be persisted as-is. Before this
    fix, apply_onu_label_matches only rejected an empty string; anything
    else up to 20 chars was written straight to the database, and anything
    longer raised a raw, unvalidated database error back to the client."""
    hdr = make_tenant(client, "Match T", "match_t_admin")
    olt = setup_devices(client, hdr)
    customer = add_customer(app, "Match T", "Someone Else")
    r = client.post(f"/api/network-tree/olt/{olt['id']}/label-matches/apply",
                    headers=hdr,
                    json={"links": [{"customer_id": customer,
                                     "mac_address": "not-a-real-mac-address"}]})
    assert r.status_code == 400
    assert "not-a-real-mac-address" in r.get_json()["error"]
    with app.app_context():
        assert appmod.Customer.query.filter_by(id=customer).first().onu_mac_address is None


# --- Critical 1 fix: onu_mac_address must stay editable outside of /apply ---
# (an applied link could previously never be corrected or removed -- the
# field was written in exactly one place and excluded from every customer
# read/write endpoint). Important 4 fix: the same MAC-shape validation
# /apply enforces must also cover these endpoints, via the shared
# _validate_mac_address helper. See
# docs/superpowers/specs/2026-09-01-network-topology-tree-design.md
# ("The field stays manually editable afterwards...").

def _create_plan(client, hdr):
    return client.post("/api/subscription_plans", headers=hdr, json={
        "name": "Basic", "price": 10, "billing_cycle": "monthly",
    }).get_json()["plan"]["id"]


def test_customer_post_accepts_and_normalizes_onu_mac_address(client):
    hdr = make_tenant(client, "Cust MAC A", "cust_mac_a_admin")
    plan_id = _create_plan(client, hdr)
    r = client.post("/api/customers", headers=hdr, json={
        "name": "New Cust", "phone": "1", "address": "a",
        "subscription_plan_id": plan_id,
        "onu_mac_address": "B4:64:15:3F:C1:94",
    })
    assert r.status_code == 201
    customer_id = r.get_json()["customer_id"]
    listed = client.get("/api/customers", headers=hdr).get_json()["customers"]
    row = next(c for c in listed if c["id"] == customer_id)
    assert row["onu_mac_address"] == "b4:64:15:3f:c1:94"


def test_customer_post_rejects_malformed_onu_mac_address(client):
    hdr = make_tenant(client, "Cust MAC B", "cust_mac_b_admin")
    plan_id = _create_plan(client, hdr)
    r = client.post("/api/customers", headers=hdr, json={
        "name": "New Cust", "phone": "1", "address": "a",
        "subscription_plan_id": plan_id,
        "onu_mac_address": "garbage",
    })
    assert r.status_code == 400
    assert "garbage" in r.get_json()["error"]


def test_customer_put_can_correct_a_previously_applied_onu_link(app, client, monkeypatch):
    """The exact scenario the finding describes: a customer gets wrongly
    linked via the label-matcher /apply endpoint, then disappears from
    get_onu_label_matches (which excludes already-linked customers) -- but
    must still be reachable and correctable through a plain PUT."""
    hdr = make_tenant(client, "Cust MAC C", "cust_mac_c_admin")
    olt = setup_devices(client, hdr)
    customer_id = add_customer(app, "Cust MAC C", "Some Customer")
    apply_resp = client.post(
        f"/api/network-tree/olt/{olt['id']}/label-matches/apply", headers=hdr,
        json={"links": [{"customer_id": customer_id,
                         "mac_address": "aa:bb:cc:dd:ee:01"}]})
    assert apply_resp.status_code == 200

    # Confirm the finding's premise: the now-linked customer is excluded from
    # the only endpoint that could previously write this field.
    matches = get_label_matches(client, hdr, olt['id'])
    assert all(c["id"] != customer_id for c in matches["unmatched_customers"])

    # The wrong link must still be correctable directly.
    r = client.put(f"/api/customers/{customer_id}", headers=hdr,
                   json={"onu_mac_address": "AA:BB:CC:DD:EE:02"})
    assert r.status_code == 200
    assert r.get_json()["customer"]["onu_mac_address"] == "aa:bb:cc:dd:ee:02"
    with app.app_context():
        assert appmod.Customer.query.filter_by(id=customer_id).first().onu_mac_address == \
            "aa:bb:cc:dd:ee:02"


def test_customer_put_can_clear_onu_mac_address(app, client):
    hdr = make_tenant(client, "Cust MAC D", "cust_mac_d_admin")
    customer_id = add_customer(app, "Cust MAC D", "Linked Customer",
                               mac="aa:bb:cc:dd:ee:03")
    r = client.put(f"/api/customers/{customer_id}", headers=hdr,
                   json={"onu_mac_address": ""})
    assert r.status_code == 200
    assert r.get_json()["customer"]["onu_mac_address"] is None
    with app.app_context():
        assert appmod.Customer.query.filter_by(id=customer_id).first().onu_mac_address is None


def test_customer_put_rejects_malformed_onu_mac_address(app, client):
    hdr = make_tenant(client, "Cust MAC E", "cust_mac_e_admin")
    customer_id = add_customer(app, "Cust MAC E", "Some Customer",
                               mac="aa:bb:cc:dd:ee:04")
    r = client.put(f"/api/customers/{customer_id}", headers=hdr,
                   json={"onu_mac_address": "aa:bb:cc:dd:ee:zz"})
    assert r.status_code == 400
    assert "aa:bb:cc:dd:ee:zz" in r.get_json()["error"]
    with app.app_context():
        # Rejected input must not overwrite the existing valid value.
        assert appmod.Customer.query.filter_by(id=customer_id).first().onu_mac_address == \
            "aa:bb:cc:dd:ee:04"


# --- Fix round 1: GET .../label-matches?job_id=... must tolerate a malformed
# ONU entry in the job's stored result (same bug, and same fix shape, as
# _with_interface_labels on the device_health path), and must 404 a job_id
# that names a job for some other operation rather than trusting the
# convention that only olt_status jobs ever exist for a vsol_olt device. ---

def test_label_matches_skips_a_malformed_onu_entry(app, client, monkeypatch):
    """job.result is whatever JSON an agent posted, with no shape validation
    (see agent_post_result). Matching happens by MAC, so an entry that isn't
    a dict, or a dict with no 'mac_address', can't be matched to a customer
    by definition -- unlike the job-poll/tree path (which still shows a
    malformed ONU so the operator doesn't lose visibility into real
    hardware), here it's simply dropped from consideration. The well-formed
    entry in the same batch must still be matched normally."""
    hdr = make_tenant(client, "Match Z", "match_z_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match Z", "Moussa Ghadir")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("b4:64:15:3f:c1:94", "MoussaGhadir")]))

    started = client.get(f"/api/network-tree/olt/{olt['id']}/label-matches",
                         headers=hdr).get_json()
    job_id = started["job_id"]
    assert job_id

    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.result = [
            {"description": "NoMacHere"},                   # dict, no mac_address
            "onu1",                                          # not a dict at all
            onu("b4:64:15:3f:c1:94", "MoussaGhadir"),         # well-formed
        ]
        appmod.db.session.commit()

    resp = client.get(
        f"/api/network-tree/olt/{olt['id']}/label-matches?job_id={job_id}",
        headers=hdr)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["customer"]["name"] == "Moussa Ghadir"
    assert body["proposals"][0]["onu"]["mac_address"] == "b4:64:15:3f:c1:94"


def test_label_matches_job_id_for_a_non_olt_status_job_is_not_found(app, client):
    """Today only three call sites ever create a NetworkAgentJob, and all
    three derive `operation` from device_type -- so a device_health job can
    never actually exist for a vsol_olt device in practice. But
    get_onu_label_matches's job_id branch only checked job.device_id, not
    job.operation, so correctness rested on that convention holding forever
    rather than being enforced here. Construct the convention-violating job
    directly (bypassing _create_device_job) to prove the guard itself, not
    the convention, is what keeps this endpoint from computing proposals off
    the wrong kind of job."""
    hdr = make_tenant(client, "Match Z2", "match_z2_admin")
    olt = setup_devices(client, hdr)

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Match Z2").first()
        job = appmod.NetworkAgentJob(
            tenant_id=tenant.id, device_id=olt["id"], operation="device_health",
            status="done", result=[])
        appmod.db.session.add(job)
        appmod.db.session.commit()
        job_id = job.id

    resp = client.get(
        f"/api/network-tree/olt/{olt['id']}/label-matches?job_id={job_id}",
        headers=hdr)
    assert resp.status_code == 404


# --- Fix round 2: a pre-existing row (stored before agent_post_result
# validated shapes) can carry a mac_address that isn't a string at all --
# e.g. a colon-less MAC parsed as an int. The old inline `(o.get(
# 'mac_address') or '').strip()` in get_onu_label_matches, and
# _propose_label_matches's own tie-break sort, both assumed a string. ---

def test_label_matches_skips_an_onu_with_a_non_string_mac_address(app, client, monkeypatch):
    hdr = make_tenant(client, "Match Z3", "match_z3_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match Z3", "Moussa Ghadir")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("b4:64:15:3f:c1:94", "MoussaGhadir")]))

    started = client.get(f"/api/network-tree/olt/{olt['id']}/label-matches",
                         headers=hdr).get_json()
    job_id = started["job_id"]
    assert job_id

    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.result = [
            {"description": "IntMac", "mac_address": 123456789012},  # non-string
            onu("b4:64:15:3f:c1:94", "MoussaGhadir"),                  # well-formed
        ]
        appmod.db.session.commit()

    resp = client.get(
        f"/api/network-tree/olt/{olt['id']}/label-matches?job_id={job_id}",
        headers=hdr)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["customer"]["name"] == "Moussa Ghadir"
