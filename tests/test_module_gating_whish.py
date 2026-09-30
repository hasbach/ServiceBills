import app as appmod
from tests.conftest import make_tenant
from tests.test_module_gating_api import _set_overrides


def test_whish_settings_blocked_on_free(app, client):
    hdr = make_tenant(client, "Wh Free", "whfree_admin")
    r = client.get("/api/tenant-whish-settings", headers=hdr)
    assert r.status_code == 403 and r.get_json()["module"] == "whish_payments"


def test_whish_settings_open_with_override(app, client):
    hdr = make_tenant(client, "Wh On", "whon_admin")
    _set_overrides(app, "wh-on", {"whish_payments": True})
    assert client.get("/api/tenant-whish-settings", headers=hdr).status_code == 200


def test_public_pay_page_404_when_module_off(app, client):
    make_tenant(client, "Wh Pub", "whpub_admin")
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug="wh-pub").first()
        t.public_pay_slug = "pubslug1"
        t.module_overrides = {"whish_payments": True}
        appmod.db.session.commit()
    assert client.get("/api/pay/t/pubslug1").status_code == 200
    _set_overrides(app, "wh-pub", {"whish_payments": False})
    assert client.get("/api/pay/t/pubslug1").status_code == 404


def test_plans_no_longer_carry_whish_flag():
    import plans
    assert all("whish_customer_payments" not in p for p in plans.PLANS.values())


def test_no_payment_link_created_or_sent_when_module_off(app, client, monkeypatch):
    from datetime import timedelta
    hdr = make_tenant(client, "Wh NoLink", "whnolink_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(slug="wh-nolink").first()
        tenant.module_overrides = {"whish_payments": False}
        appmod.db.session.add(appmod.TenantWhishSettings(
            tenant_id=tenant.id, enabled=True, whish_channel="c", whish_secret="s"))
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="P", price=30.0,
                                       billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        cust = appmod.Customer(tenant_id=tenant.id, name="Nadia", phone="+96170000099",
                               subscription_plan_id=plan.id, address="Beirut",
                               subscription_expiry_date=appmod.datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(cust)
        appmod.db.session.commit()
        cid = cust.id
    sent = []
    monkeypatch.setattr(appmod, "send_whatsapp_message", lambda *a, **k: sent.append(a) or {})
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": cid, "amount": 30.0, "reason": "Monthly",
        "date": appmod.datetime.utcnow().strftime('%Y-%m-%d'), "pre_payment": False})
    assert r.status_code == 201
    with app.app_context():
        assert appmod.CustomerPaymentLink.query.count() == 0
    assert not [a for a in sent if len(a) > 1 and a[1] == 'payment_link']


def test_report_open_on_free_shows_only_manual_transfers(app, client):
    from datetime import datetime, timedelta
    hdr = make_tenant(client, "Wh Rep", "whrep_admin")
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug="wh-rep").first()
        t.module_overrides = {"whish_payments": False}
        plan = appmod.SubscriptionPlan(tenant_id=t.id, name="P", price=10.0, billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        cust = appmod.Customer(tenant_id=t.id, name="C", phone="70111222", address="a", subscription_plan_id=plan.id,
                               subscription_expiry_date=datetime.utcnow() + timedelta(days=30))
        appmod.db.session.add(cust)
        appmod.db.session.commit()
        pay = appmod.Payment(tenant_id=t.id, customer_id=cust.id, amount=40, paid=True,
                             paid_at=datetime.utcnow(), collected_via="whish_transfer")
        appmod.db.session.add(pay)
        appmod.db.session.commit()
        appmod.db.session.add(appmod.CustomerPaymentLink(
            tenant_id=t.id, customer_id=cust.id, payment_id=pay.id, amount=40, currency="USD",
            view_token="v1", callback_token="c1", status="pending",
            expires_at=datetime.utcnow() + timedelta(days=1)))
        appmod.db.session.commit()
    r = client.get("/api/reports/customer-whish-payments", headers=hdr)
    assert r.status_code == 200
    sources = [x["source"] for x in r.get_json()["links"]]
    assert sources == ["manual_transfer"]
    _set_overrides(app, "wh-rep", {"whish_payments": True})
    sources = [x["source"] for x in client.get("/api/reports/customer-whish-payments", headers=hdr).get_json()["links"]]
    assert sorted(sources) == ["manual_transfer", "payment_link"]
