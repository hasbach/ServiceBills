"""Supplier payments can be corrected (edited/deleted) only by an admin, and
the supplier's balance owed stays consistent with the corrected history."""
from tests.conftest import make_tenant


def _login(client, username, password="pw"):
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _setup(client, name):
    admin = make_tenant(client, f"Biz {name}", f"{name}_admin")
    sid = client.post("/api/suppliers", headers=admin, json={"name": "Sup"}).get_json()["id"]
    client.put(f"/api/suppliers/{sid}/fix-balance", headers=admin, json={"balance": 100})
    r = client.post(f"/api/suppliers/{sid}/payments", headers=admin, json={"amount": 30})
    return admin, sid, r.get_json()["payment"]["id"]


def _balance(client, hdr, sid):
    return next(s["balance"] for s in client.get("/api/suppliers", headers=hdr).get_json() if s["id"] == sid)


def test_admin_edit_moves_balance_by_the_difference(client):
    admin, sid, pid = _setup(client, "a")
    assert _balance(client, admin, sid) == 70
    r = client.put(f"/api/suppliers/{sid}/payments/{pid}", headers=admin,
                   json={"amount": 50, "payment_date": "2026-09-01", "payment_method": "cash", "reference_note": "fixed"})
    assert r.status_code == 200, r.get_json()
    assert _balance(client, admin, sid) == 50
    p = client.get(f"/api/suppliers/{sid}/payments", headers=admin).get_json()[0]
    assert (p["amount"], p["payment_method"], p["reference_note"]) == (50, "cash", "fixed")
    assert p["payment_date"].startswith("2026-09-01")


def test_admin_delete_puts_amount_back_on_balance(client):
    admin, sid, pid = _setup(client, "b")
    assert client.delete(f"/api/suppliers/{sid}/payments/{pid}", headers=admin).status_code == 200
    assert _balance(client, admin, sid) == 100
    assert client.get(f"/api/suppliers/{sid}/payments", headers=admin).get_json() == []


def test_finance_cannot_edit_or_delete_supplier_payment(client):
    admin, sid, pid = _setup(client, "c")
    client.post("/api/users", headers=admin, json={"username": "c_fin", "password": "pw", "role": "finance"})
    fin = _login(client, "c_fin")
    assert client.put(f"/api/suppliers/{sid}/payments/{pid}", headers=fin, json={"amount": 1}).status_code == 403
    assert client.delete(f"/api/suppliers/{sid}/payments/{pid}", headers=fin).status_code == 403
    assert _balance(client, admin, sid) == 70


def test_combined_admin_role_counts_as_admin(client):
    admin, sid, pid = _setup(client, "d")
    client.post("/api/users", headers=admin, json={"username": "d_both", "password": "pw", "role": "admin,finance"})
    both = _login(client, "d_both")
    assert client.put(f"/api/suppliers/{sid}/payments/{pid}", headers=both, json={"amount": 40}).status_code == 200


def test_edit_rejects_bad_amount_and_other_tenants_payment(client):
    admin, sid, pid = _setup(client, "e")
    assert client.put(f"/api/suppliers/{sid}/payments/{pid}", headers=admin, json={"amount": -5}).status_code == 400
    other, other_sid, other_pid = _setup(client, "f")
    assert client.put(f"/api/suppliers/{other_sid}/payments/{other_pid}", headers=admin, json={"amount": 1}).status_code == 404
    assert client.delete(f"/api/suppliers/{other_sid}/payments/{other_pid}", headers=admin).status_code == 404
    assert _balance(client, other, other_sid) == 70


def test_history_exposes_payment_id_for_editing(client):
    admin, sid, pid = _setup(client, "g")
    h = client.get(f"/api/suppliers/{sid}/history", headers=admin).get_json()["history"]
    assert [x["payment_id"] for x in h if x["type"] == "payment"] == [pid]
