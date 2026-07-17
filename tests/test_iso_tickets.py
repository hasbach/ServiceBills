from tests.conftest import make_tenant


def _make_customer(client, hdr, name="C"):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "P", "price": 10, "billing_cycle": "monthly"})
    pid = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr,
                json={"name": name, "phone": "1", "address": "a",
                      "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})
    return client.get("/api/customers", headers=hdr).get_json()["customers"][0]["id"]


def test_support_ticket_customer_must_be_in_tenant(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")
    a_customer = _make_customer(client, a)

    # Tenant B cannot open a ticket against tenant A's customer.
    r = client.post("/api/support-tickets", headers=b,
                    json={"customer_id": a_customer, "title": "x",
                          "description": "y", "priority": "medium"})
    assert r.status_code == 404

    # Tenant B can open one against its own customer.
    b_customer = _make_customer(client, b)
    r = client.post("/api/support-tickets", headers=b,
                    json={"customer_id": b_customer, "title": "x",
                          "description": "y", "priority": "medium"})
    assert r.status_code in (200, 201)
