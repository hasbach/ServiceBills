"""The CPE MAC is the customer's own router, as the OLT learns it -- unique
per customer, unlike the shared ONU MAC. See
docs/superpowers/specs/2026-09-06-cpe-mac-linking-design.md."""
import app as appmod
from tests.conftest import make_tenant


def _plan(client, hdr):
    return client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 10,
                             "billing_cycle": "monthly"}).get_json()["plan"]["id"]


def _customer(client, hdr, plan_id, name, **extra):
    body = {"name": name, "phone": "1", "address": "a",
            "subscription_plan_id": plan_id,
            "subscription_start_date": "2026-01-01"}
    body.update(extra)
    return client.post("/api/customers", headers=hdr, json=body)


def test_customer_model_has_the_cpe_columns(app, client):
    hdr = make_tenant(client, "Cpe A", "cpe_a_admin")
    plan_id = _plan(client, hdr)
    _customer(client, hdr, plan_id, "C")
    with app.app_context():
        customer = appmod.Customer.query.filter_by(name="C").first()
        assert customer.cpe_mac_address is None
        assert customer.onu_last_seen_at is None


def test_cpe_mac_is_stored_canonicalised_and_returned(app, client):
    hdr = make_tenant(client, "Cpe B", "cpe_b_admin")
    plan_id = _plan(client, hdr)
    resp = _customer(client, hdr, plan_id, "C", cpe_mac_address="DC-8E-8D-61-B0-61")
    assert resp.status_code == 201, resp.get_json()
    with app.app_context():
        customer = appmod.Customer.query.filter_by(name="C").first()
        assert customer.cpe_mac_address == "dc:8e:8d:61:b0:61"
    listed = client.get("/api/customers", headers=hdr).get_json()
    rows = listed["customers"] if isinstance(listed, dict) else listed
    assert any(c.get("cpe_mac_address") == "dc:8e:8d:61:b0:61" for c in rows)


def test_a_second_customer_cannot_claim_the_same_cpe(app, client):
    hdr = make_tenant(client, "Cpe C", "cpe_c_admin")
    plan_id = _plan(client, hdr)
    _customer(client, hdr, plan_id, "First", cpe_mac_address="dc:8e:8d:61:b0:61")
    resp = _customer(client, hdr, plan_id, "Second", cpe_mac_address="DC:8E:8D:61:B0:61")
    assert resp.status_code == 400
    # The message must name the holder -- "duplicate" alone leaves the
    # operator hunting through 300 customers for the clash.
    assert "First" in resp.get_json()["error"]


def test_the_same_cpe_may_be_used_by_another_tenant(app, client):
    hdr_one = make_tenant(client, "Cpe D1", "cpe_d1_admin")
    _customer(client, hdr_one, _plan(client, hdr_one), "Theirs",
              cpe_mac_address="dc:8e:8d:61:b0:61")
    hdr_two = make_tenant(client, "Cpe D2", "cpe_d2_admin")
    resp = _customer(client, hdr_two, _plan(client, hdr_two), "Ours",
                     cpe_mac_address="dc:8e:8d:61:b0:61")
    assert resp.status_code == 201, resp.get_json()


def test_updating_a_customer_to_its_own_cpe_is_not_a_duplicate(app, client):
    hdr = make_tenant(client, "Cpe E", "cpe_e_admin")
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "C",
                    cpe_mac_address="dc:8e:8d:61:b0:61").get_json()["customer_id"]
    resp = client.put(f"/api/customers/{cid}", headers=hdr,
                      json={"cpe_mac_address": "dc:8e:8d:61:b0:61"})
    assert resp.status_code == 200, resp.get_json()


def test_clearing_the_cpe_is_allowed(app, client):
    hdr = make_tenant(client, "Cpe F", "cpe_f_admin")
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "C",
                    cpe_mac_address="dc:8e:8d:61:b0:61").get_json()["customer_id"]
    assert client.put(f"/api/customers/{cid}", headers=hdr,
                      json={"cpe_mac_address": ""}).status_code == 200
    with app.app_context():
        assert appmod.Customer.query.get(cid).cpe_mac_address is None


def test_a_malformed_cpe_is_rejected(app, client):
    hdr = make_tenant(client, "Cpe G", "cpe_g_admin")
    resp = _customer(client, hdr, _plan(client, hdr), "C", cpe_mac_address="nope")
    assert resp.status_code == 400
    assert "not a valid MAC address" in resp.get_json()["error"]


def test_raced_cpe_mac_collision_degrades_the_same_way_on_create_and_update(app, client, monkeypatch):
    """_check_cpe_mac_available is a read followed by a write with no lock:
    two concurrent requests can both pass it, and the loser then hits
    uq_customer_tenant_cpe_mac at commit. Simulate that race by bypassing the
    pre-check entirely (so a genuine constraint violation reaches the
    IntegrityError handler) -- both endpoints must answer with the same
    friendly conflict, not a 500 or raw driver text."""
    hdr = make_tenant(client, "Cpe H", "cpe_h_admin")
    plan_id = _plan(client, hdr)
    _customer(client, hdr, plan_id, "Holder", cpe_mac_address="dc:8e:8d:61:b0:61")
    other_id = _customer(client, hdr, plan_id, "Other").get_json()["customer_id"]

    # Pretend the pre-check found nothing available -- the only thing left to
    # catch the collision is the DB constraint itself.
    monkeypatch.setattr(appmod, "_check_cpe_mac_available", lambda *a, **k: None)

    create_resp = _customer(client, hdr, plan_id, "Racer",
                            cpe_mac_address="dc:8e:8d:61:b0:61")
    update_resp = client.put(f"/api/customers/{other_id}", headers=hdr,
                             json={"cpe_mac_address": "dc:8e:8d:61:b0:61"})

    assert create_resp.status_code == update_resp.status_code == 409
    for resp in (create_resp, update_resp):
        error = resp.get_json()["error"]
        assert "already recorded" in error
        assert "one customer" in error
        assert "UNIQUE constraint" not in error
        assert "IntegrityError" not in error


import types

ONUS = [
    {"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
     "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
     "model": "V2801D", "distance_m": 531},
]
LOCATIONS = {"aa:bb:cc:00:00:01": {"pon_port": "PON1", "onu_id": "EPON0/1:2",
                                   "onu_mac": "b4:64:15:3f:c1:94"}}


def _olt(client, hdr):
    ccr = client.post("/api/network-devices", headers=hdr, json={
        "name": "Core CCR", "host": "10.0.0.1", "username": "admin",
        "password": "secret", "device_type": "mikrotik_ccr"}).get_json()["device"]
    return client.post("/api/network-devices", headers=hdr, json={
        "name": "EPON OLT", "host": "192.168.8.100", "password": "public",
        "device_type": "vsol_olt", "parent_device_id": ccr["id"],
    }).get_json()["device"]


def _locate_and_apply(client, hdr, olt_id):
    started = client.post(f"/api/network-tree/olt/{olt_id}/locate-customers",
                          headers=hdr).get_json()
    assert started["ok"] is True, started
    return client.post(f"/api/network-tree/olt/{olt_id}/locate-customers/apply",
                       headers=hdr, json={"job_id": started["job_id"]})


def test_locate_places_a_customer_behind_the_onu_holding_their_cpe(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc A", "loc_a_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "Moussa",
                    cpe_mac_address="aa:bb:cc:00:00:01").get_json()["customer_id"]
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))

    body = _locate_and_apply(client, hdr, olt["id"]).get_json()
    assert body["located"] == 1
    with app.app_context():
        customer = appmod.Customer.query.get(cid)
        assert customer.onu_mac_address == "b4:64:15:3f:c1:94"
        assert customer.onu_last_seen_at is not None


def test_a_cpe_matching_no_customer_is_ignored(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc B", "loc_b_admin")
    olt = _olt(client, hdr)
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))
    body = _locate_and_apply(client, hdr, olt["id"]).get_json()
    assert body["located"] == 0
    assert body["unmatched"] == 1


def test_a_customer_whose_cpe_was_not_seen_is_left_completely_alone(app, client, monkeypatch):
    """This is the memory. Their previous placement AND its timestamp stand."""
    hdr = make_tenant(client, "Loc C", "loc_c_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "Absent",
                    cpe_mac_address="aa:bb:cc:99:99:99",
                    onu_mac_address="f4:c4:d6:4d:80:e1").get_json()["customer_id"]
    with app.app_context():
        appmod.Customer.query.get(cid).onu_last_seen_at = appmod.datetime(2026, 1, 1)
        appmod.db.session.commit()
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))

    _locate_and_apply(client, hdr, olt["id"])
    with app.app_context():
        customer = appmod.Customer.query.get(cid)
        assert customer.onu_mac_address == "f4:c4:d6:4d:80:e1"
        assert customer.onu_last_seen_at == appmod.datetime(2026, 1, 1)


def test_a_moved_cpe_updates_the_placement(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc D", "loc_d_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "Mover",
                    cpe_mac_address="aa:bb:cc:00:00:01",
                    onu_mac_address="f4:c4:d6:4d:80:e1").get_json()["customer_id"]
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))

    body = _locate_and_apply(client, hdr, olt["id"]).get_json()
    assert body["moved"] == 1
    with app.app_context():
        assert appmod.Customer.query.get(cid).onu_mac_address == "b4:64:15:3f:c1:94"


def test_a_cpe_recorded_with_hyphens_still_matches(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc E", "loc_e_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "Hyphen",
                    cpe_mac_address="AA-BB-CC-00-00-01").get_json()["customer_id"]
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))
    _locate_and_apply(client, hdr, olt["id"])
    with app.app_context():
        assert appmod.Customer.query.get(cid).onu_mac_address == "b4:64:15:3f:c1:94"


def test_applying_the_same_result_twice_is_harmless(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc F", "loc_f_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    _customer(client, hdr, plan_id, "Twice", cpe_mac_address="aa:bb:cc:00:00:01")
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))
    _locate_and_apply(client, hdr, olt["id"])
    body = _locate_and_apply(client, hdr, olt["id"]).get_json()
    assert body["located"] == 1
    assert body["moved"] == 0


def test_a_malformed_entry_is_skipped_not_raised(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc G", "loc_g_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    _customer(client, hdr, plan_id, "Ok", cpe_mac_address="aa:bb:cc:00:00:01")
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, dict(LOCATIONS)))
    started = client.post(f"/api/network-tree/olt/{olt['id']}/locate-customers",
                          headers=hdr).get_json()
    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, started["job_id"])
        job.result = {"aa:bb:cc:00:00:01": {"onu_mac": "b4:64:15:3f:c1:94"},
                      "bad": "not-an-object", "worse": {"onu_mac": None}}
        appmod.db.session.commit()
    resp = client.post(f"/api/network-tree/olt/{olt['id']}/locate-customers/apply",
                       headers=hdr, json={"job_id": started["job_id"]})
    assert resp.status_code == 200
    assert resp.get_json()["located"] == 1


def test_locate_on_a_non_olt_is_rejected(app, client):
    hdr = make_tenant(client, "Loc H", "loc_h_admin")
    ccr = client.post("/api/network-devices", headers=hdr, json={
        "name": "Core CCR", "host": "10.0.0.1", "username": "admin",
        "password": "secret", "device_type": "mikrotik_ccr"}).get_json()["device"]
    assert client.post(f"/api/network-tree/olt/{ccr['id']}/locate-customers",
                       headers=hdr).status_code == 400


def test_employee_and_collector_cannot_locate_or_apply(app, client):
    """Reading the tree is theirs; rewriting who lives where is not."""
    admin_hdr = make_tenant(client, "Loc I", "loc_i_admin")
    olt = _olt(client, admin_hdr)
    for username, role in (("loc_i_emp", "employee"), ("loc_i_col", "collector")):
        client.post("/api/users", headers=admin_hdr,
                    json={"username": username, "password": "pw", "role": role})
        token = client.post("/api/login", json={"username": username,
                                                "password": "pw"}).get_json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}
        assert client.post(f"/api/network-tree/olt/{olt['id']}/locate-customers",
                           headers=hdr).status_code == 403, role
        assert client.post(f"/api/network-tree/olt/{olt['id']}/locate-customers/apply",
                           headers=hdr, json={"job_id": 1}).status_code == 403, role


def test_apply_rejects_a_non_numeric_job_id(app, client):
    """On SQLite, id='not-a-number' would just match nothing and 404 -- which
    is exactly why a plain 404 assertion here wouldn't prove anything. On
    Postgres (production), comparing the Integer id column to a non-numeric
    string raises DataError before this endpoint's un-caught path, which
    would hand Sentry a frame containing the decrypted device credential.
    job_id must be parsed and rejected with a clean 400 before it ever
    reaches the query."""
    hdr = make_tenant(client, "Loc K", "loc_k_admin")
    olt = _olt(client, hdr)
    resp = client.post(f"/api/network-tree/olt/{olt['id']}/locate-customers/apply",
                       headers=hdr, json={"job_id": "not-a-number"})
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "Invalid job_id"


def test_apply_is_tenant_scoped(app, client, monkeypatch):
    hdr_one = make_tenant(client, "Loc J1", "loc_j1_admin")
    olt_one = _olt(client, hdr_one)
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))
    started = client.post(f"/api/network-tree/olt/{olt_one['id']}/locate-customers",
                          headers=hdr_one).get_json()

    hdr_two = make_tenant(client, "Loc J2", "loc_j2_admin")
    olt_two = _olt(client, hdr_two)
    resp = client.post(f"/api/network-tree/olt/{olt_two['id']}/locate-customers/apply",
                       headers=hdr_two, json={"job_id": started["job_id"]})
    assert resp.status_code == 404
