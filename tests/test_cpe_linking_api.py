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
