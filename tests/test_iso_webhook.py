import app as appmod
from tests.conftest import make_tenant


def _seed_customer(client, hdr, phone):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "P", "price": 10, "billing_cycle": "monthly"})
    pid = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr,
                json={"name": "C", "phone": phone, "address": "a",
                      "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})


def test_webhook_resolves_tenant_by_phone_number_id(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")
    _seed_customer(client, a, "70123456")
    _seed_customer(client, b, "70999999")

    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        b_tid = appmod.Tenant.query.filter_by(slug="biz-b").first().id
        # Tenant A owns business phone number "PNID_A" (no creds -> no outbound network calls).
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=a_tid, phone_number_id="PNID_A", enabled=True, mode="api"))
        appmod.db.session.commit()

    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID_A"},
        "contacts": [{"profile": {"name": "Cust A"}}],
        "messages": [{"from": "96170123456", "type": "text", "text": {"body": "hello"}}],
    }}]}]}

    # Public endpoint (no auth). Meta delivers a message for tenant A's number.
    r = client.post("/api/whatsapp/webhook", json=payload)
    assert r.status_code == 200

    with app.app_context():
        # The incoming reply became a support ticket for tenant A only.
        assert appmod.SupportTicket.query.filter_by(tenant_id=a_tid).count() == 1
        assert appmod.SupportTicket.query.filter_by(tenant_id=b_tid).count() == 0


def test_webhook_verifies_token_against_any_tenant(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=a_tid, phone_number_id="PNID_A", webhook_verify_token="tokA"))
        appmod.db.session.commit()

    ok = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=tokA&hub.challenge=123")
    assert ok.status_code == 200 and ok.get_data(as_text=True) == "123"
    bad = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=123")
    assert bad.status_code == 403
