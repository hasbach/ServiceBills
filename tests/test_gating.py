import app as appmod
import plans as plans_mod
from tests.conftest import make_tenant


def _plan(client, hdr):
    return client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]


def _add(client, hdr, pid, name):
    return client.post("/api/customers", headers=hdr,
                       json={"name": name, "phone": "1", "address": "a",
                             "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})


def _set_plan(app, slug, plan):
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug=slug).first()
        t.plan = plan
        appmod.db.session.commit()


def test_free_customer_limit_enforced(app, client, monkeypatch):
    monkeypatch.setitem(plans_mod.PLANS["free"], "max_customers", 1)
    hdr = make_tenant(client, "Biz A", "a_admin")
    pid = _plan(client, hdr)
    assert _add(client, hdr, pid, "C1").status_code in (200, 201)
    assert _add(client, hdr, pid, "C2").status_code == 402   # over the free cap


def test_pro_is_unlimited(app, client, monkeypatch):
    monkeypatch.setitem(plans_mod.PLANS["free"], "max_customers", 1)
    hdr = make_tenant(client, "Biz B", "b_admin")
    _set_plan(app, "biz-b", "pro")
    pid = _plan(client, hdr)
    assert _add(client, hdr, pid, "C1").status_code in (200, 201)
    assert _add(client, hdr, pid, "C2").status_code in (200, 201)  # beyond free cap, allowed on pro


def test_whatsapp_api_mode_gated_by_plan(app, client):
    hdr = make_tenant(client, "Biz C", "c_admin")  # free by default
    assert client.post("/api/whatsapp-settings", headers=hdr,
                       json={"mode": "api", "enabled": True}).status_code == 402
    _set_plan(app, "biz-c", "pro")
    assert client.post("/api/whatsapp-settings", headers=hdr,
                       json={"mode": "api", "enabled": True}).status_code == 200
