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
    body = client.get(f"/api/network-tree/olt/{olt['id']}/label-matches",
                      headers=hdr).get_json()
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
    body = client.get(f"/api/network-tree/olt/{olt['id']}/label-matches",
                      headers=hdr).get_json()
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["confidence"] == 1.0


def test_unrelated_label_produces_no_proposal(app, client, monkeypatch):
    hdr = make_tenant(client, "Match C", "match_c_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match C", "Completely Different Person")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("aa:bb:cc:dd:ee:ff", "zein_khodor")]))
    body = client.get(f"/api/network-tree/olt/{olt['id']}/label-matches",
                      headers=hdr).get_json()
    assert body["proposals"] == []
    assert len(body["unmatched_onus"]) == 1
    assert len(body["unmatched_customers"]) == 1


def test_unlabelled_onus_are_never_proposed(app, client, monkeypatch):
    hdr = make_tenant(client, "Match D", "match_d_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match D", "Somebody")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("aa:bb:cc:dd:ee:ff", None)]))
    body = client.get(f"/api/network-tree/olt/{olt['id']}/label-matches",
                      headers=hdr).get_json()
    assert body["proposals"] == []
    assert body["unmatched_onus"][0]["mac_address"] == "aa:bb:cc:dd:ee:ff"


def test_already_linked_customers_are_not_proposed_again(app, client, monkeypatch):
    hdr = make_tenant(client, "Match E", "match_e_admin")
    olt = setup_devices(client, hdr)
    add_customer(app, "Match E", "Moussa Ghadir", mac="b4:64:15:3f:c1:94")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("b4:64:15:3f:c1:94", "MoussaGhadir")]))
    body = client.get(f"/api/network-tree/olt/{olt['id']}/label-matches",
                      headers=hdr).get_json()
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
    body = client.get(f"/api/network-tree/olt/{olt['id']}/label-matches",
                      headers=hdr).get_json()
    assert len(body["proposals"]) == 1
    # The stronger (exact) match wins the customer; the other is left unmatched.
    assert body["proposals"][0]["onu"]["mac_address"] == "aa:00:00:00:00:01"
    assert len(body["unmatched_onus"]) == 1


def test_get_writes_nothing(app, client, monkeypatch):
    hdr = make_tenant(client, "Match G", "match_g_admin")
    olt = setup_devices(client, hdr)
    cid = add_customer(app, "Match G", "Moussa Ghadir")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [onu("b4:64:15:3f:c1:94", "MoussaGhadir")]))
    client.get(f"/api/network-tree/olt/{olt['id']}/label-matches", headers=hdr)
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
