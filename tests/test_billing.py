import app as appmod
import billing
from tests.conftest import make_tenant


def _tid(slug):
    return appmod.Tenant.query.filter_by(slug=slug).first().id


def test_checkout_completed_sets_customer_and_active(app, client):
    make_tenant(client, "Biz A", "a_admin")
    with app.app_context():
        tid = _tid("biz-a")
        billing.handle_event({
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": str(tid),
                                 "customer": "cus_1", "subscription": "sub_1"}},
        })
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.stripe_customer_id == "cus_1"
        assert t.stripe_subscription_id == "sub_1"
        assert t.status == "active"


def test_subscription_lifecycle_maps_plan_and_status(app, client, monkeypatch):
    monkeypatch.setitem(billing.plans.PLANS["pro"], "stripe_price", "price_pro")
    make_tenant(client, "Biz B", "b_admin")
    with app.app_context():
        tid = _tid("biz-b")
        appmod.db.session.get(appmod.Tenant, tid).stripe_customer_id = "cus_2"
        appmod.db.session.commit()

        def sub_event(status):
            return {"type": "customer.subscription.updated", "data": {"object": {
                "customer": "cus_2", "id": "sub_2", "status": status,
                "items": {"data": [{"price": {"id": "price_pro"}}]}}}}

        billing.handle_event(sub_event("active"))
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "pro" and t.status == "active"

        billing.handle_event(sub_event("past_due"))
        assert appmod.db.session.get(appmod.Tenant, tid).status == "suspended"

        billing.handle_event({"type": "customer.subscription.deleted",
                              "data": {"object": {"customer": "cus_2"}}})
        t = appmod.db.session.get(appmod.Tenant, tid)
        assert t.plan == "free" and t.status == "active"


def test_webhook_route_verifies_and_syncs(app, client, monkeypatch):
    import stripe
    make_tenant(client, "Biz C", "c_admin")
    with app.app_context():
        tid = _tid("biz-c")
    event = {"type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": str(tid), "customer": "cus_3", "subscription": "sub_3"}}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, sig, secret: event)
    r = client.post("/api/stripe/webhook", data=b"{}", headers={"Stripe-Signature": "x"})
    assert r.status_code == 200
    with app.app_context():
        assert appmod.db.session.get(appmod.Tenant, tid).stripe_customer_id == "cus_3"


def test_checkout_route(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz D", "d_admin")
    # Free plan is not purchasable.
    assert client.post("/api/billing/checkout", headers=hdr, json={"plan": "free"}).status_code == 400
    # Pro plan with a configured price + mocked Stripe -> a checkout URL.
    monkeypatch.setitem(billing.plans.PLANS["pro"], "stripe_price", "price_pro")
    monkeypatch.setattr(billing, "create_checkout_session", lambda tenant, price: "https://stripe.test/checkout")
    r = client.post("/api/billing/checkout", headers=hdr, json={"plan": "pro"})
    assert r.status_code == 200 and r.get_json()["url"].startswith("https://")
