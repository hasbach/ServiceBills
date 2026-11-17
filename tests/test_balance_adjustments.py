"""Balance adjustments (roadmap 1B, step 14): a balance typed over by hand
needs a reason and is kept as a dated adjustment in the party's balance log
and in Finance > History."""
from tests.conftest import make_tenant
from tests.test_receive_payment import _customer
from app import app as flask_app, db, Customer, Employee, UpstreamProvider


def _adjustments(client, hdr, **params):
    r = client.get("/api/audit-log", headers=hdr, query_string={"entity": "balance_adjustment", **params})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["items"]


def _balance(model, pk):
    with flask_app.app_context():
        return round(float(db.session.get(model, pk).balance or 0), 2)


def test_customer_balance_change_without_a_reason_is_refused(app, client):
    a = make_tenant(client, "B", "ba1")
    cid = _customer(client, a)
    r = client.put(f"/api/customers/{cid}", headers=a, json={"balance": 40})
    assert r.status_code == 400 and "reason" in r.get_json()["error"].lower()
    assert _balance(Customer, cid) == 0
    assert _adjustments(client, a) == []


def test_customer_adjustment_is_logged_in_both_places(app, client):
    a = make_tenant(client, "B", "ba2")
    cid = _customer(client, a, name="Nour")
    r = client.put(f"/api/customers/{cid}", headers=a,
                   json={"balance": 40, "balance_reason": "Old paper ledger carried over"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _balance(Customer, cid) == 40

    adj = _adjustments(client, a)
    assert len(adj) == 1
    row = adj[0]
    assert row["party_type"] == "customer" and row["party_id"] == cid and row["party_name"] == "Nour"
    assert row["amount"] == 40 and row["changes"]["balance"] == [0, 40]
    assert row["note"] == "Old paper ledger carried over" and row["changed_by"] == "ba2"

    log = client.get(f"/api/customers/{cid}/balance-log", headers=a).get_json()[0]
    assert log["reason"] == "Adjustment: Old paper ledger carried over"
    assert (log["balance_before"], log["balance_after"]) == (0, 40)


def test_saving_the_same_balance_needs_no_reason_and_logs_nothing(app, client):
    a = make_tenant(client, "B", "ba3")
    cid = _customer(client, a)
    r = client.put(f"/api/customers/{cid}", headers=a, json={"name": "Renamed", "balance": 0})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _adjustments(client, a) == []


def test_reseller_supplier_employee_and_upstream_adjustments(app, client):
    a = make_tenant(client, "B", "ba4")
    rid = client.post("/api/resellers", headers=a, json={"name": "R", "phone": "1", "type": "type1"}).get_json()["reseller"]["id"]
    sid = client.post("/api/suppliers", headers=a, json={"name": "S"}).get_json()["id"]
    eid = client.post("/api/employees", headers=a, json={"name": "E", "base_salary": 100}).get_json()["id"]
    up = client.post("/api/upstream-providers", headers=a, json={"name": "U", "product": "manual"})
    assert up.status_code in (200, 201), up.get_data(as_text=True)
    with flask_app.app_context():
        uid = UpstreamProvider.query.order_by(UpstreamProvider.id.desc()).first().id

    calls = [
        ("reseller", rid, f"/api/resellers/{rid}", {"balance": 15}),
        ("supplier", sid, f"/api/suppliers/{sid}/fix-balance", {"balance": 60}),
        ("employee", eid, f"/api/employees/{eid}/fix-balance", {"balance": 75}),
        ("upstream", uid, f"/api/upstream-providers/{uid}", {"balance": 90}),
    ]
    for ptype, pid, url, body in calls:
        assert client.put(url, headers=a, json=body).status_code == 400, ptype
        r = client.put(url, headers=a, json={**body, "balance_reason": f"fix {ptype}"})
        assert r.status_code == 200, (ptype, r.get_data(as_text=True))
        row = _adjustments(client, a, party_type=ptype, party_id=pid)[0]
        assert row["note"] == f"fix {ptype}" and row["amount"] == body["balance"]

    assert _balance(Employee, eid) == 75 and _balance(UpstreamProvider, uid) == 90
    assert len(_adjustments(client, a)) == 4


def test_an_adjustment_counts_as_explained_in_the_balance_check(app, client):
    a = make_tenant(client, "B", "ba5")
    cid = _customer(client, a)

    def unexplained():
        rec = client.get("/api/reports/reconciliation", headers=a).get_json()
        return sum(c["unexplained"] for c in rec["customers"] if c["id"] == cid)

    # (the helper's own direct edit shows once the customer is listed, so
    # compare one adjustment against the next)
    client.put(f"/api/customers/{cid}", headers=a, json={"balance": 40, "balance_reason": "carried over"})
    before = unexplained()
    client.put(f"/api/customers/{cid}", headers=a, json={"balance": 55, "balance_reason": "second correction"})
    assert abs(unexplained() - before) < 0.01
