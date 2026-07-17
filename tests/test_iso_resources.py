from tests.conftest import make_tenant


def _names(resp):
    data = resp.get_json()
    items = data if isinstance(data, list) else data.get("items", [])
    return {i.get("name") for i in items}


def test_reseller_and_supplier_isolation(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    assert client.post("/api/resellers", headers=a,
                       json={"name": "ResellerA", "phone": "1", "type": "type1"}).status_code == 201
    assert client.post("/api/suppliers", headers=a, json={"name": "SupplierA"}).status_code == 201

    # Tenant B sees none of tenant A's resellers/suppliers.
    assert _names(client.get("/api/resellers", headers=b)) == set()
    assert _names(client.get("/api/suppliers", headers=b)) == set()
    # Tenant A sees its own.
    assert "ResellerA" in _names(client.get("/api/resellers", headers=a))
    assert "SupplierA" in _names(client.get("/api/suppliers", headers=a))


def test_sector_and_category_isolation_and_name_reuse(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    # Both tenants can use the SAME name now that uniqueness is per-tenant.
    for hdr in (a, b):
        assert client.post("/api/sectors", headers=hdr, json={"name": "North"}).status_code == 201
        assert client.post("/api/expense_categories", headers=hdr, json={"name": "Rent"}).status_code == 201

    # Each tenant sees only its own single row.
    assert _names(client.get("/api/sectors", headers=a)) == {"North"}
    assert _names(client.get("/api/sectors", headers=b)) == {"North"}
    assert _names(client.get("/api/expense_categories", headers=a)) == {"Rent"}
    assert _names(client.get("/api/expense_categories", headers=b)) == {"Rent"}


def test_payments_isolation(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    # Creating a customer in A generates back-dated payments for A.
    r = client.post("/api/subscription_plans", headers=a,
                    json={"name": "P", "price": 10, "billing_cycle": "monthly"})
    pid = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=a,
                json={"name": "C", "phone": "1", "address": "a",
                      "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})

    assert len(client.get("/api/payments", headers=a).get_json()["payments"]) >= 1
    assert client.get("/api/payments", headers=b).get_json()["payments"] == []
