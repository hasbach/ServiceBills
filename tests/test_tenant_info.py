from tests.conftest import make_tenant


def test_tenant_me_and_plans(client):
    hdr = make_tenant(client, "Biz A", "a_admin")

    me = client.get("/api/tenant/me", headers=hdr)
    assert me.status_code == 200
    body = me.get_json()
    assert body["slug"] == "biz-a"
    assert body["plan"] == "free"
    assert body["status"] == "active"

    plans = client.get("/api/plans", headers=hdr).get_json()
    assert "free" in plans and "pro" in plans
    # Stripe price / secrets must NOT be exposed to the client.
    assert "stripe_price" not in plans["pro"]
    assert "max_customers" in plans["free"]
