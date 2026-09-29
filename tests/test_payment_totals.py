"""GET /api/payments returns totals (collected / uncollected / unpaid / paid)
over everything matching the filters, not just the returned page."""
from datetime import datetime

import app as appmod
from tests.conftest import make_tenant


def _login(client, username, password="pw"):
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _setup(client, name):
    hdr = make_tenant(client, f"Biz {name}", f"{name}_admin")
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    cid = client.post("/api/customers", headers=hdr, json={
        "name": "Cust", "phone": "1", "address": "a", "subscription_plan_id": plan,
        "subscription_start_date": "2026-01-01"}).get_json()["customer_id"]
    tid = appmod.Customer.query.get(cid).tenant_id
    # Replace the auto-generated bills with a known set.
    appmod.Payment.query.filter_by(customer_id=cid).delete()
    appmod.db.session.commit()
    rows = [
        # amount, paid, collected, collected_amount, gratis
        (10, False, False, None, False),   # uncollected
        (20, False, False, None, False),   # uncollected
        (30, False, True, 25, False),      # collected (partial 25), awaiting confirmation
        (40, True, True, None, False),     # collected, then confirmed
        (50, True, False, None, False),    # paid directly (office)
        (60, True, False, None, True),     # gratis -- no money
    ]
    for amount, paid, collected, collected_amount, gratis in rows:
        appmod.db.session.add(appmod.Payment(
            tenant_id=tid, customer_id=cid, amount=amount, paid=paid, collected=collected,
            collected_amount=collected_amount, is_gratis=gratis, date=datetime(2026, 9, 1),
            collected_at=datetime.utcnow() if collected else None,
            paid_at=datetime.utcnow() if paid else None))
    appmod.db.session.commit()
    return hdr


def test_totals_by_bucket(client):
    hdr = _setup(client, "t1")
    t = client.get("/api/payments", headers=hdr).get_json()["totals"]
    assert t["collected"] == {"count": 2, "amount": 65, "awaiting_count": 1, "awaiting_amount": 25}
    assert t["uncollected"] == {"count": 2, "amount": 30}
    assert t["unpaid"] == {"count": 3, "amount": 60}
    assert t["paid"] == {"count": 2, "amount": 90}
    assert t["currency"] == "USD"


def test_totals_follow_filters_not_the_page(client):
    hdr = _setup(client, "t2")
    unpaid = client.get("/api/payments", headers=hdr, query_string={"status": "unpaid"}).get_json()["totals"]
    assert unpaid["paid"]["count"] == 0 and unpaid["unpaid"]["amount"] == 60

    today = datetime.utcnow().strftime("%Y-%m-%d")
    collected_today = client.get("/api/payments", headers=hdr, query_string={"collected_date": today}).get_json()
    assert collected_today["totals"]["collected"]["amount"] == 65

    page1 = client.get("/api/payments", headers=hdr, query_string={"per_page": 2, "page": 1}).get_json()
    assert len(page1["payments"]) == 2
    assert page1["totals"]["unpaid"]["count"] == 3  # whole filtered set, not the 2 on this page


def test_totals_visible_to_collector_and_cashier(client):
    hdr = _setup(client, "t3")
    for role in ("collector", "cashier"):
        client.post("/api/users", headers=hdr, json={"username": f"t3_{role}", "password": "pw", "role": role})
        body = client.get("/api/payments", headers=_login(client, f"t3_{role}")).get_json()
        assert body["totals"]["uncollected"]["amount"] == 30, role
