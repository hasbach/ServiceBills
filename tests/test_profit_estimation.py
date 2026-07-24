import app as appmod
from tests.conftest import make_tenant


def test_customer_lifecycle_triggers_recompute(app, client):
    a = make_tenant(client, "Biz A", "a_admin")

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id

    r = client.post("/api/subscription_plans", headers=a,
                     json={"name": "Fiber 50", "price": 25, "billing_cycle": "monthly", "cost": 15})
    plan_id = r.get_json()["plan"]["id"]

    r2 = client.post("/api/customers", headers=a,
                      json={"name": "Cust", "phone": "1", "address": "a",
                            "subscription_plan_id": plan_id, "subscription_start_date": "2026-01-01"})
    assert r2.status_code == 201
    customer_id = r2.get_json()["customer_id"]

    def _current_estimate():
        with app.app_context():
            month = appmod.datetime.utcnow().strftime('%Y-%m')
            return appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first()

    # Adding an active customer triggers a recompute.
    est = _current_estimate()
    assert est is not None
    assert est.estimated_income == 25.0
    assert est.estimated_cost == 15.0

    # Setting a per-customer cost override triggers a recompute.
    r3 = client.put(f"/api/customers/{customer_id}", headers=a, json={"cost_override": 18})
    assert r3.status_code == 200
    assert r3.get_json()["customer"]["cost_override"] == 18.0
    assert _current_estimate().estimated_cost == 18.0

    # Clearing the override falls back to the plan's cost.
    r4 = client.put(f"/api/customers/{customer_id}", headers=a, json={"cost_override": ""})
    assert r4.status_code == 200
    assert r4.get_json()["customer"]["cost_override"] is None
    assert _current_estimate().estimated_cost == 15.0

    # cost_override also appears in the customer list.
    listed = client.get("/api/customers", headers=a).get_json()
    assert listed["customers"][0]["cost_override"] is None

    # Canceling the subscription removes the customer's contribution.
    r5 = client.put(f"/api/customers/{customer_id}/cancel_subscription", headers=a)
    assert r5.status_code == 200
    assert _current_estimate().estimated_income == 0.0

    # Reactivating restores it.
    r6 = client.put(f"/api/customers/{customer_id}/activate_subscription", headers=a)
    assert r6.status_code == 200
    assert _current_estimate().estimated_income == 25.0

    # Deleting the customer also removes their contribution.
    r7 = client.delete(f"/api/customers/{customer_id}", headers=a)
    assert r7.status_code == 200
    assert _current_estimate().estimated_income == 0.0
