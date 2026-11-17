"""Roadmap 1B, step 15: reseller collections and upstream top-ups can be
corrected (edited or deleted) by an admin, with the same rules as supplier
payments: the balance moves by the difference, the day lock holds, and every
change is in the audit log."""
from datetime import datetime

from tests.conftest import make_tenant, auth_headers
from tests.test_profit_includes_resellers_and_upstream import _reseller, _provider
from tests.test_receive_payment import _customer
from app import app as flask_app, db, Reseller, UpstreamProvider, ResellerPayment


def _res_balance(rid):
    with flask_app.app_context():
        return round(float(db.session.get(Reseller, rid).balance), 2)


def _prov_balance(pid):
    with flask_app.app_context():
        return round(float(db.session.get(UpstreamProvider, pid).balance), 2)


def _entries(client, hdr, url):
    return client.get(url, headers=hdr).get_json()


def _reseller_with_entries(client, a):
    rid = _reseller(client, a)
    assert client.post(f"/api/resellers/{rid}/add_credit", headers=a, json={"amount": 500}).status_code == 200
    assert client.post(f"/api/resellers/{rid}/collect_payment", headers=a, json={"amount": 200}).status_code == 200
    hist = _entries(client, a, f"/api/resellers/{rid}/history")
    by_type = {h["type"]: h["id"] for h in hist}
    return rid, by_type


def test_editing_a_reseller_collection_moves_the_balance_by_the_difference(app, client):
    a = make_tenant(client, "B", "ce1")
    rid, ids = _reseller_with_entries(client, a)
    assert _res_balance(rid) == 300

    r = client.put(f"/api/resellers/{rid}/entries/{ids['payment_received']}", headers=a,
                   json={"amount": 150, "description": "Cash, corrected"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _res_balance(rid) == 350                      # collected 150, not 200
    assert r.get_json()["entry"]["amount"] == 150 and r.get_json()["entry"]["description"] == "Cash, corrected"

    r = client.put(f"/api/resellers/{rid}/entries/{ids['credit_added']}", headers=a, json={"amount": 520})
    assert r.status_code == 200
    assert _res_balance(rid) == 370                      # charged 520, not 500


def test_deleting_reseller_entries_puts_the_balance_back(app, client):
    a = make_tenant(client, "B", "ce2")
    rid, ids = _reseller_with_entries(client, a)
    r = client.delete(f"/api/resellers/{rid}/entries/{ids['payment_received']}", headers=a,
                      json={"reason": "recorded on the wrong reseller"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _res_balance(rid) == 500
    assert client.delete(f"/api/resellers/{rid}/entries/{ids['credit_added']}", headers=a).status_code == 200
    assert _res_balance(rid) == 0
    assert _entries(client, a, f"/api/resellers/{rid}/history") == []

    log = client.get("/api/audit-log", headers=a, query_string={"entity": "reseller_payment", "action": "delete"}).get_json()
    assert log["total"] == 2
    assert "recorded on the wrong reseller" in [i["note"] for i in log["items"]]
    reasons = [l["reason"] for l in client.get(f"/api/resellers/{rid}/balance-log", headers=a).get_json()]
    assert any(r.startswith("Payment received deleted") for r in reasons)


def test_reseller_entry_rules(app, client):
    a = make_tenant(client, "B", "ce3")
    rid, ids = _reseller_with_entries(client, a)
    pid = ids["payment_received"]
    # bad amounts
    for bad in (0, -5, "abc"):
        assert client.put(f"/api/resellers/{rid}/entries/{pid}", headers=a, json={"amount": bad}).status_code == 400
    assert _res_balance(rid) == 300
    # finance cannot correct
    fin = auth_headers(client, username="ce3fin", role="finance")
    assert client.put(f"/api/resellers/{rid}/entries/{pid}", headers=fin, json={"amount": 1}).status_code == 403
    assert client.delete(f"/api/resellers/{rid}/entries/{pid}", headers=fin).status_code == 403
    # an entry created by a customer's billing is not edited here
    cid = _customer(client, a)
    with flask_app.app_context():
        r = db.session.get(Reseller, rid)
        e = ResellerPayment(tenant_id=r.tenant_id, reseller_id=rid, customer_id=cid, amount=25,
                            type="credit_added", description="cycle")
        db.session.add(e)
        db.session.commit()
        billing_id = e.id
    assert client.put(f"/api/resellers/{rid}/entries/{billing_id}", headers=a, json={"amount": 1}).status_code == 400
    assert client.delete(f"/api/resellers/{rid}/entries/{billing_id}", headers=a).status_code == 400
    # another tenant cannot reach it
    b = make_tenant(client, "Other", "ce3b")
    assert client.delete(f"/api/resellers/{rid}/entries/{pid}", headers=b).status_code == 404
    assert _res_balance(rid) == 300


def test_upstream_topup_can_be_edited_and_deleted(app, client):
    a = make_tenant(client, "B", "ce4")
    pid = _provider(client, a)
    assert client.post(f"/api/upstream-providers/{pid}/topup", headers=a, json={"amount": 80}).status_code in (200, 201)
    eid = _entries(client, a, f"/api/upstream-providers/{pid}/history")[0]["id"]
    assert _prov_balance(pid) == 80

    r = client.put(f"/api/upstream-providers/{pid}/entries/{eid}", headers=a, json={"amount": 100, "paid_via": "whish"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _prov_balance(pid) == 100 and r.get_json()["entry"]["paid_via"] == "whish"

    fin = auth_headers(client, username="ce4fin", role="finance")
    assert client.delete(f"/api/upstream-providers/{pid}/entries/{eid}", headers=fin).status_code == 403

    assert client.delete(f"/api/upstream-providers/{pid}/entries/{eid}", headers=a, json={"reason": "duplicate"}).status_code == 200
    assert _prov_balance(pid) == 0
    log = client.get("/api/audit-log", headers=a, query_string={"entity": "upstream_payment"}).get_json()["items"]
    assert [i["action"] for i in log] == ["delete", "update", "create"]
    assert log[0]["note"] == "duplicate" and log[1]["changes"]["amount"] == [80, 100]


def test_a_closed_day_blocks_corrections_of_money_entries(app, client):
    a = make_tenant(client, "B", "ce5")
    rid, ids = _reseller_with_entries(client, a)
    pid = _provider(client, a)
    client.post(f"/api/upstream-providers/{pid}/topup", headers=a, json={"amount": 80})
    top = _entries(client, a, f"/api/upstream-providers/{pid}/history")[0]["id"]
    today = datetime.utcnow().strftime("%Y-%m-%d")
    assert client.put("/api/reports/cash-opening", headers=a,
                      json={"date": today, "amount": 0, "whish_amount": 0}).status_code == 200
    r = client.post("/api/day-closes", headers=a, json={"day": today, "counted_cash": 120, "counted_whish": 0})
    assert r.status_code == 201, r.get_data(as_text=True)

    assert client.put(f"/api/resellers/{rid}/entries/{ids['payment_received']}", headers=a, json={"amount": 1}).status_code == 409
    assert client.delete(f"/api/upstream-providers/{pid}/entries/{top}", headers=a).status_code == 409
    # a charge moved no money, so the lock does not apply to it
    assert client.put(f"/api/resellers/{rid}/entries/{ids['credit_added']}", headers=a, json={"amount": 510}).status_code == 200
    assert _res_balance(rid) == 310 and _prov_balance(pid) == 80
