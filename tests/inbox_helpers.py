"""Shared helpers for the WhatsApp inbox tests."""
import hashlib
import hmac
import json

import app as appmod
from tests.conftest import make_tenant


def signed_post(client, payload, app_secret):
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return client.post("/api/whatsapp/webhook", data=body, content_type="application/json",
                       headers={"X-Hub-Signature-256": f"sha256={sig}"})


def seed_customer(client, hdr, phone, name="C"):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "P", "price": 10, "billing_cycle": "monthly"})
    pid = r.get_json()["plan"]["id"]
    r = client.post("/api/customers", headers=hdr,
                    json={"name": name, "phone": phone, "address": "a",
                          "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})
    return r


def setup_wa_tenant(app, client, business, username, pnid, secret="s3cret", token="tok"):
    """Tenant with a configured WhatsApp Cloud API number. Returns (headers, tenant_id)."""
    hdr = make_tenant(client, business, username)
    with app.app_context():
        tid = appmod.User.query.filter_by(username=username).first().tenant_id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=tid, phone_number_id=pnid, enabled=True, mode="api",
            app_secret=secret, access_token=token, api_version="v19.0"))
        appmod.db.session.commit()
    return hdr, tid


def wa_payload(pnid, messages=None, statuses=None, name="Cust"):
    value = {"metadata": {"phone_number_id": pnid}, "contacts": [{"profile": {"name": name}}]}
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {"entry": [{"changes": [{"value": value}]}]}


class FakeResponse:
    def __init__(self, ok=True, status_code=200, body=None):
        self.ok = ok
        self.status_code = status_code
        self._body = body if body is not None else {"messages": [{"id": "wamid.OUT1"}]}
        self.text = json.dumps(self._body)
        self.content = self.text.encode()

    def json(self):
        return self._body
