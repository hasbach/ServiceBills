from tests.conftest import make_tenant


def _seed_customer(client, hdr):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "Basic", "price": 10, "billing_cycle": "monthly"})
    plan_id = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr,
                json={"name": "Cust", "phone": "1", "address": "a",
                      "subscription_plan_id": plan_id,
                      "subscription_start_date": "2026-01-01"})


def test_reports_do_not_leak_across_tenants(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    # Only tenant A has any data.
    _seed_customer(client, a)

    # Dashboard totals are tenant-scoped.
    dash_a = client.get("/api/dashboard", headers=a).get_json()
    dash_b = client.get("/api/dashboard", headers=b).get_json()
    assert dash_a["totalCustomers"] >= 1
    assert dash_b["totalCustomers"] == 0
    assert dash_b["totalRevenue"] == 0

    # customer-numbers aggregate excludes the other tenant.
    cn_b = client.get("/api/reports/customer-numbers", headers=b).get_json()
    assert sum(item["value"] for item in cn_b) == 0
    cn_a = client.get("/api/reports/customer-numbers", headers=a).get_json()
    assert sum(item["value"] for item in cn_a) >= 1

    # unpaid-payments aggregate: B has none.
    up_b = client.get("/api/reports/unpaid-payments", headers=b).get_json()
    assert sum(item["value"] for item in up_b) == 0
