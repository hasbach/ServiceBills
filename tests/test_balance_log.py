from tests.conftest import make_tenant


def _supplier(client, hdr, name="Acme"):
    r = client.post("/api/suppliers", headers=hdr, json={"name": name})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["id"]


def _reseller(client, hdr, balance=0):
    r = client.post("/api/resellers", headers=hdr,
                    json={"name": "R1", "phone": "70000000", "type": "type1", "balance": balance})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["reseller"]["id"]


def _supplier_log(client, hdr, sid):
    r = client.get(f"/api/suppliers/{sid}/balance-log", headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_supplier_payment_logs_before_and_after(app, client):
    a = make_tenant(client, "Biz A", "alice")
    sid = _supplier(client, a)
    client.put(f"/api/suppliers/{sid}/fix-balance", headers=a, json={"balance": -100})
    r = client.post(f"/api/suppliers/{sid}/payments", headers=a,
                    json={"amount": 50, "payment_method": "Cash", "reference_note": "Inv 7"})
    assert r.status_code == 201

    logs = _supplier_log(client, a, sid)
    assert len(logs) == 2
    pay, fix = logs  # newest first
    assert (fix["balance_before"], fix["balance_after"]) == (0, -100)
    assert fix["reason"] == "Fixed credit balance set"
    assert (pay["balance_before"], pay["balance_after"], pay["change"]) == (-100, -150, -50)
    assert pay["reason"] == "Payment recorded (Cash): Inv 7"
    assert pay["changed_by"] == "alice"


def test_supplier_payment_edit_and_delete_logged(app, client):
    a = make_tenant(client, "Biz A", "alice")
    sid = _supplier(client, a)
    client.put(f"/api/suppliers/{sid}/fix-balance", headers=a, json={"balance": 300})
    pid = client.post(f"/api/suppliers/{sid}/payments", headers=a, json={"amount": 100}).get_json()["payment"]["id"]
    client.put(f"/api/suppliers/{sid}/payments/{pid}", headers=a, json={"amount": 80})
    client.delete(f"/api/suppliers/{sid}/payments/{pid}", headers=a)

    delete, edit, pay, _fix = _supplier_log(client, a, sid)
    assert (pay["balance_before"], pay["balance_after"]) == (300, 200)
    assert (edit["balance_before"], edit["balance_after"], edit["reason"]) == (200, 220, "Payment edited: 100.00 -> 80.00")
    assert (delete["balance_before"], delete["balance_after"], delete["reason"]) == (220, 300, "Payment deleted (80.00)")


def test_credit_purchase_logged_on_supplier(app, client):
    a = make_tenant(client, "Biz A", "alice")
    sid = _supplier(client, a)
    client.post("/api/expense_categories", headers=a, json={"name": "Stock"})
    r = client.post("/api/expenses", headers=a, json={
        "category": "Stock", "amount": 40, "description": "Cables", "date": "2026-10-01",
        "is_credit": True, "supplier_id": sid})
    assert r.status_code == 201, r.get_data(as_text=True)
    (log,) = _supplier_log(client, a, sid)
    assert (log["balance_before"], log["balance_after"]) == (0, 40)
    assert log["reason"] == "Credit purchase: Cables"


def test_credit_purchase_edit_is_one_net_row(app, client):
    a = make_tenant(client, "Biz A", "alice")
    sid = _supplier(client, a)
    client.post("/api/expense_categories", headers=a, json={"name": "Stock"})
    eid = client.post("/api/expenses", headers=a, json={
        "category": "Stock", "amount": 40, "description": "Cables", "date": "2026-10-01",
        "is_credit": True, "supplier_id": sid}).get_json()["id"]
    r = client.put(f"/api/expenses/{eid}", headers=a, json={"amount": 60})
    assert r.status_code == 200, r.get_data(as_text=True)
    edit, _create = _supplier_log(client, a, sid)
    assert (edit["balance_before"], edit["balance_after"]) == (40, 60)
    assert edit["reason"] == "Credit purchase edited: Cables"


def test_manual_supplier_edit_logged(app, client):
    a = make_tenant(client, "Biz A", "alice")
    sid = _supplier(client, a)
    client.put(f"/api/suppliers/{sid}", headers=a, json={"balance": 25})
    (log,) = _supplier_log(client, a, sid)
    assert (log["balance_before"], log["balance_after"], log["reason"]) == (0, 25, "Balance edited manually")


def test_reseller_credit_and_payment_logged(app, client):
    a = make_tenant(client, "Biz A", "alice")
    rid = _reseller(client, a, balance=-100)
    client.post(f"/api/resellers/{rid}/add_credit", headers=a, json={"amount": 30, "description": "Top-up"})
    client.post(f"/api/resellers/{rid}/collect_payment", headers=a, json={"amount": 50})
    client.post(f"/api/resellers/{rid}/apply_discount", headers=a, json={"amount": 5})

    r = client.get(f"/api/resellers/{rid}/balance-log", headers=a)
    assert r.status_code == 200
    disc, pay, credit, opening = r.get_json()
    assert (opening["balance_before"], opening["balance_after"], opening["reason"]) == (0, -100, "Opening balance")
    assert (credit["balance_before"], credit["balance_after"], credit["reason"]) == (-100, -70, "Credit added: Top-up")
    assert (pay["balance_before"], pay["balance_after"], pay["reason"]) == (-70, -120, "Payment received")
    assert (disc["balance_before"], disc["balance_after"]) == (-120, -125)


def test_non_balance_edit_writes_no_log(app, client):
    a = make_tenant(client, "Biz A", "alice")
    sid = _supplier(client, a)
    client.put(f"/api/suppliers/{sid}", headers=a, json={"name": "Renamed"})
    assert _supplier_log(client, a, sid) == []


def test_balance_log_is_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "alice")
    b = make_tenant(client, "Biz B", "bob")
    sid = _supplier(client, a)
    client.put(f"/api/suppliers/{sid}/fix-balance", headers=a, json={"balance": 10})
    assert client.get(f"/api/suppliers/{sid}/balance-log", headers=b).status_code == 404
