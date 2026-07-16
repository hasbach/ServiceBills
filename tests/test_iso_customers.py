from tests.conftest import make_tenant


def _make_plan(client, hdr, name="Basic", price=10):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": name, "price": price, "billing_cycle": "monthly"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["plan"]["id"]


def _make_customer(client, hdr, plan_id, name="Cust"):
    r = client.post("/api/customers", headers=hdr,
                    json={"name": name, "phone": "111", "address": "addr",
                          "subscription_plan_id": plan_id,
                          "subscription_start_date": "2026-01-01"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r


def test_customer_isolation(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    plan_a = _make_plan(client, a)
    _make_customer(client, a, plan_a, name="AliceCo")

    # Tenant B's customer list must not contain tenant A's customer.
    listed_b = client.get("/api/customers", headers=b).get_json()
    names_b = [c["name"] for c in listed_b["customers"]]
    assert "AliceCo" not in names_b
    assert listed_b["total"] == 0

    # Tenant A sees its own customer.
    listed_a = client.get("/api/customers", headers=a).get_json()
    assert "AliceCo" in [c["name"] for c in listed_a["customers"]]
    cid = listed_a["customers"][0]["id"]

    # Tenant B cannot update or delete tenant A's customer -> 404.
    assert client.put(f"/api/customers/{cid}", headers=b, json={"name": "Hacked"}).status_code == 404
    assert client.delete(f"/api/customers/{cid}", headers=b).status_code == 404

    # And tenant A's customer is untouched.
    listed_a = client.get("/api/customers", headers=a).get_json()
    assert "Hacked" not in [c["name"] for c in listed_a["customers"]]
