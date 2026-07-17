import app as appmod
from tests.conftest import make_tenant


def _seed_customer(client, hdr):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "P", "price": 10, "billing_cycle": "monthly"})
    pid = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr,
                json={"name": "C", "phone": "70123456", "address": "a",
                      "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})


def test_whatsapp_settings_resolved_per_customer_tenant(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    # Enable WhatsApp (manual/deep-link mode) for tenant A only; B stays default (disabled).
    client.post("/api/whatsapp-settings", headers=a, json={"enabled": True, "mode": "deeplink"})

    _seed_customer(client, a)
    _seed_customer(client, b)

    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        b_tid = appmod.Tenant.query.filter_by(slug="biz-b").first().id
        ca = appmod.Customer.query.filter_by(tenant_id=a_tid).first()
        cb = appmod.Customer.query.filter_by(tenant_id=b_tid).first()

        # Settings are resolved by each customer's own tenant.
        res_a = appmod.send_whatsapp_message(ca, "subscription_created", {})
        res_b = appmod.send_whatsapp_message(cb, "subscription_created", {})

    assert res_a["success"] is True          # A enabled (deep-link) -> simulated/manual
    assert res_b["success"] is False         # B disabled -> skipped
    assert "disabled" in res_b["error"].lower()
