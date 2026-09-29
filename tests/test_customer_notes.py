"""Staff notes on a customer's subscription: saved on create/edit, returned in
the customer list (shown on the Subscriptions card), editable by the cashier."""
from tests.conftest import make_tenant


def _login(client, username, password="pw"):
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _plan(client, hdr):
    return client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]


def _notes(client, hdr, cid):
    return next(c["notes"] for c in client.get("/api/customers", headers=hdr).get_json()["customers"] if c["id"] == cid)


def test_notes_saved_on_create_and_edit_and_listed(client):
    hdr = make_tenant(client, "Biz N1", "n1_admin")
    cid = client.post("/api/customers", headers=hdr, json={
        "name": "C", "phone": "1", "address": "a", "subscription_plan_id": _plan(client, hdr),
        "subscription_start_date": "2026-01-01", "notes": "  Router on 2nd floor\nCall before visiting  "}).get_json()["customer_id"]
    assert _notes(client, hdr, cid) == "Router on 2nd floor\nCall before visiting"

    r = client.put(f"/api/customers/{cid}", headers=hdr, json={"notes": "Paid cash in office"})
    assert r.status_code == 200 and r.get_json()["customer"]["notes"] == "Paid cash in office"
    assert _notes(client, hdr, cid) == "Paid cash in office"

    client.put(f"/api/customers/{cid}", headers=hdr, json={"notes": "   "})
    assert _notes(client, hdr, cid) is None

    # An edit that doesn't send `notes` leaves them alone.
    client.put(f"/api/customers/{cid}", headers=hdr, json={"notes": "keep me"})
    client.put(f"/api/customers/{cid}", headers=hdr, json={"name": "C2"})
    assert _notes(client, hdr, cid) == "keep me"


def test_notes_are_capped(client):
    hdr = make_tenant(client, "Biz N2", "n2_admin")
    cid = client.post("/api/customers", headers=hdr, json={
        "name": "C", "phone": "1", "address": "a", "subscription_plan_id": _plan(client, hdr),
        "subscription_start_date": "2026-01-01"}).get_json()["customer_id"]
    client.put(f"/api/customers/{cid}", headers=hdr, json={"notes": "x" * 5000})
    assert len(_notes(client, hdr, cid)) == 2000


def test_cashier_can_edit_notes(client):
    admin = make_tenant(client, "Biz N3", "n3_admin")
    cid = client.post("/api/customers", headers=admin, json={
        "name": "C", "phone": "1", "address": "a", "subscription_plan_id": _plan(client, admin),
        "subscription_start_date": "2026-01-01"}).get_json()["customer_id"]
    client.post("/api/users", headers=admin, json={"username": "n3_cash", "password": "pw", "role": "cashier"})
    r = client.put(f"/api/customers/{cid}", headers=_login(client, "n3_cash"), json={"notes": "Wants to switch plan"})
    assert r.status_code == 200, r.get_json()
    assert _notes(client, admin, cid) == "Wants to switch plan"
