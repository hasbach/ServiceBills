"""A MAC may be typed with colons, hyphens, dots or nothing at all.

Windows displays MACs with hyphens and the OLT's own web UI uses them in
places, so a colon-only validator turned an ordinary paste into a 400 that
read as a broken feature. Exactly one form -- colon-lowercase -- is ever
stored, so comparison stays a plain string match.
"""
import app as appmod
from tests.conftest import make_tenant


def _plan(client, hdr):
    return client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 10,
                             "billing_cycle": "monthly"}).get_json()["plan"]["id"]


def _customer(client, hdr, plan_id, mac):
    return client.post("/api/customers", headers=hdr,
                       json={"name": "C", "phone": "1", "address": "a",
                             "subscription_plan_id": plan_id,
                             "subscription_start_date": "2026-01-01",
                             "onu_mac_address": mac})


def test_canonical_mac_accepts_every_common_separator():
    for raw in ("dc:8e:8d:61:b0:61", "DC-8E-8D-61-B0-61", "dc.8e.8d.61.b0.61",
                "dc8e8d61b061", "DC8E8D61B061", "  dc:8E:8d:61:B0:61  "):
        assert appmod._canonical_mac(raw) == "dc:8e:8d:61:b0:61", raw


def test_canonical_mac_rejects_anything_that_is_not_twelve_hex_digits():
    for raw in ("", "dc:8e:8d:61:b0", "dc:8e:8d:61:b0:61:99",
                "zz:8e:8d:61:b0:61", "not a mac", None, 12345, ["dc"]):
        assert appmod._canonical_mac(raw) == "", repr(raw)


def test_customer_accepts_a_hyphenated_mac_and_stores_the_colon_form(app, client):
    hdr = make_tenant(client, "Mac A", "mac_a_admin")
    plan_id = _plan(client, hdr)
    resp = _customer(client, hdr, plan_id, "DC-8E-8D-61-B0-61")
    assert resp.status_code == 201, resp.get_json()
    with app.app_context():
        customer = appmod.Customer.query.filter_by(name="C").first()
        assert customer.onu_mac_address == "dc:8e:8d:61:b0:61"


def test_customer_still_rejects_a_malformed_mac(app, client):
    hdr = make_tenant(client, "Mac B", "mac_b_admin")
    plan_id = _plan(client, hdr)
    resp = _customer(client, hdr, plan_id, "dc-8e-8d-61-b0")
    assert resp.status_code == 400
    assert "not a valid MAC address" in resp.get_json()["error"]


def test_normalize_mac_matches_across_separator_styles():
    assert appmod._normalize_mac("DC-8E-8D-61-B0-61") == appmod._normalize_mac("dc:8e:8d:61:b0:61")
    # A value that is not a MAC at all keeps its old behaviour: stripped and
    # lowercased, never collapsed to '' where it could collide with another
    # malformed value.
    assert appmod._normalize_mac("  Garbage ") == "garbage"
    assert appmod._normalize_mac(12345) == ""
