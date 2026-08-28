import hashlib
import hmac
import json

import app as appmod
from tests.conftest import make_tenant


def _signed_post(client, payload, app_secret):
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/whatsapp/webhook",
        data=body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": f"sha256={sig}"},
    )


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

    app_secret = "shh-tenant-a-secret"
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        b_tid = appmod.Tenant.query.filter_by(slug="biz-b").first().id
        # Tenant A owns business phone number "PNID_A" (no creds -> no outbound network calls).
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=a_tid, phone_number_id="PNID_A", enabled=True, mode="api",
            app_secret=app_secret))
        appmod.db.session.commit()

    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID_A"},
        "contacts": [{"profile": {"name": "Cust A"}}],
        "messages": [{"from": "96170123456", "type": "text", "text": {"body": "hello"}}],
    }}]}]}

    # Public endpoint (no auth). Meta delivers a message for tenant A's number,
    # correctly signed with tenant A's app_secret.
    r = _signed_post(client, payload, app_secret)
    assert r.status_code == 200

    with app.app_context():
        # The incoming reply became a support ticket for tenant A only.
        assert appmod.SupportTicket.query.filter_by(tenant_id=a_tid).count() == 1
        assert appmod.SupportTicket.query.filter_by(tenant_id=b_tid).count() == 0


def test_webhook_rejects_missing_signature(app, client):
    a = make_tenant(client, "Biz C", "c_admin")
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-c").first().id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=a_tid, phone_number_id="PNID_C", enabled=True, mode="api",
            app_secret="shh-tenant-c-secret"))
        appmod.db.session.commit()

    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID_C"},
        "contacts": [{"profile": {"name": "Cust C"}}],
        "messages": [{"from": "96170000000", "type": "text", "text": {"body": "hello"}}],
    }}]}]}

    # No X-Hub-Signature-256 header at all.
    r = client.post("/api/whatsapp/webhook", json=payload)
    assert r.status_code == 401

    with app.app_context():
        assert appmod.SupportTicket.query.filter_by(tenant_id=a_tid).count() == 0


def test_webhook_rejects_invalid_signature(app, client):
    a = make_tenant(client, "Biz D", "d_admin")
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-d").first().id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=a_tid, phone_number_id="PNID_D", enabled=True, mode="api",
            app_secret="shh-tenant-d-secret"))
        appmod.db.session.commit()

    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID_D"},
        "contacts": [{"profile": {"name": "Cust D"}}],
        "messages": [{"from": "96170000001", "type": "text", "text": {"body": "hello"}}],
    }}]}]}

    # Signed with the WRONG secret.
    r = _signed_post(client, payload, "not-the-real-secret")
    assert r.status_code == 401

    with app.app_context():
        assert appmod.SupportTicket.query.filter_by(tenant_id=a_tid).count() == 0


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


def test_template_status_update_updates_local_row(app, client):
    a = make_tenant(client, "Biz TE", "te_admin")
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-te").first().id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=a_tid, business_account_id="WABA_TE", enabled=True, mode="api",
            app_secret="shh-te-secret"))
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=a_tid, name="greeting", language="en", category="UTILITY",
            status="PENDING", components=[{"type": "BODY", "text": "Hi"}]))
        appmod.db.session.commit()

    payload = {"entry": [{"id": "WABA_TE", "changes": [{
        "field": "message_template_status_update",
        "value": {"message_template_name": "greeting", "message_template_language": "en",
                  "event": "APPROVED"},
    }]}]}
    r = _signed_post(client, payload, "shh-te-secret")
    assert r.status_code == 200

    with app.app_context():
        row = appmod.WhatsAppTemplate.query.filter_by(tenant_id=a_tid, name="greeting").first()
        assert row.status == "APPROVED"


def test_template_status_update_rejects_bad_signature(app, client):
    a = make_tenant(client, "Biz TF", "tf_admin")
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-tf").first().id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=a_tid, business_account_id="WABA_TF", enabled=True, mode="api",
            app_secret="shh-tf-secret"))
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=a_tid, name="greeting", language="en", category="UTILITY",
            status="PENDING", components=[{"type": "BODY", "text": "Hi"}]))
        appmod.db.session.commit()

    payload = {"entry": [{"id": "WABA_TF", "changes": [{
        "field": "message_template_status_update",
        "value": {"message_template_name": "greeting", "message_template_language": "en",
                  "event": "APPROVED"},
    }]}]}
    r = _signed_post(client, payload, "wrong-secret")
    assert r.status_code == 401
    with app.app_context():
        row = appmod.WhatsAppTemplate.query.filter_by(tenant_id=a_tid, name="greeting").first()
        assert row.status == "PENDING"  # untouched


def test_template_status_update_unmatched_waba_is_a_noop(app, client):
    payload = {"entry": [{"id": "NO_SUCH_WABA", "changes": [{
        "field": "message_template_status_update",
        "value": {"message_template_name": "greeting", "message_template_language": "en",
                  "event": "APPROVED"},
    }]}]}
    # No signature needed to reach the "no tenant found" branch -- it's checked
    # before signature verification, mirroring the existing phone_number_id path.
    r = client.post("/api/whatsapp/webhook", json=payload)
    assert r.status_code == 200
