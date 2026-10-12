"""Reseller actions must actually send their reseller_* WhatsApp templates.

Regression: from a93cffb (2026-07-17) send_whatsapp_message read
customer.tenant_id, which the reseller stand-in objects lacked -- every
reseller send raised AttributeError inside the sender's catch-all and nothing
reached Meta, with no visible error."""
from datetime import datetime

import pytest

import app as appmod
from app import db, Customer, Reseller, WhatsAppSettings
from tests.conftest import make_tenant


@pytest.fixture
def meta_calls(monkeypatch):
    calls = []

    class _Resp:
        ok = True

        def json(self):
            return {'messages': [{'id': 'wamid.test'}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        return _Resp()

    monkeypatch.setattr(appmod.requests, 'post', fake_post)
    monkeypatch.setattr(appmod, 'build_meta_template_payload',
                        lambda settings, template_name, default_language, user_body_params, user_header_params:
                        {'name': template_name, 'params': user_body_params})
    return calls


def _setup(app, client, username):
    hdr = make_tenant(client, "Biz R", username)
    r = client.post("/api/resellers", headers=hdr, json={"name": "Res", "phone": "70111222", "type": "type1"})
    assert r.status_code == 201, r.get_data(as_text=True)
    reseller_id = r.get_json()["reseller"]["id"]
    with app.app_context():
        tenant_id = db.session.get(Reseller, reseller_id).tenant_id
        s = WhatsAppSettings.query.filter_by(tenant_id=tenant_id).first()
        if not s:
            s = WhatsAppSettings(tenant_id=tenant_id)
            db.session.add(s)
        s.enabled, s.mode, s.access_token, s.phone_number_id = True, 'api', 'tok', '123'
        db.session.commit()
    return hdr, reseller_id


@pytest.mark.parametrize('action,template', [
    ('add_credit', 'reseller_credit_added'),
    ('apply_discount', 'reseller_discount_applied'),
    ('collect_payment', 'reseller_payment_collected'),
])
def test_reseller_balance_actions_send_template(app, client, meta_calls, action, template):
    hdr, reseller_id = _setup(app, client, f"res_wa_{action}")
    r = client.post(f"/api/resellers/{reseller_id}/{action}", headers=hdr, json={"amount": 25})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert [c['template']['name'] for c in meta_calls] == [template]
    assert meta_calls[0]['to'].endswith('70111222')


def test_renewing_reseller_customer_sends_reseller_template(app, client, meta_calls):
    hdr, reseller_id = _setup(app, client, "res_wa_renew")
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "P", "price": 40, "billing_cycle": "monthly"})
    plan_id = r.get_json()["plan"]["id"]
    with app.app_context():
        reseller = db.session.get(Reseller, reseller_id)
        c = Customer(tenant_id=reseller.tenant_id, name="Cust1", phone="111", address="a",
                     subscription_plan_id=plan_id, subscription_start_date=datetime.utcnow(),
                     subscription_expiry_date=datetime.utcnow(), is_subscription_active=True,
                     balance=0.0, reseller_id=reseller.id)
        db.session.add(c)
        db.session.commit()
        customer_id = c.id

    r = client.post(f"/api/customers/{customer_id}/renew_subscription", headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    sent = [c for c in meta_calls if c['template']['name'] == 'reseller_customer_renewed']
    assert len(sent) == 1
    assert sent[0]['template']['params'][0] == 'Cust1'
