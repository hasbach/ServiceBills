import pytest

from tests.conftest import make_tenant, auth_headers

DAY = "2026-10-05"


def _range(day_str):
    from datetime import datetime, timedelta
    end = (datetime.strptime(day_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"{day_str}T00:00:00.000Z", f"{end}T00:00:00.000Z"


def _flow(client, hdr, day=DAY):
    start, end = _range(day)
    r = client.get("/api/reports/daily-cash", headers=hdr, query_string={"start_date": start, "end_date": end})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["cash_flow"]


def _totals(section):
    return {i["category"]: i["total"] for i in section["items"]}


def _entry(client, hdr, **kw):
    body = {"account": "cash", "amount": 50, "reason": "Owner top-up", "date": DAY}
    body.update(kw)
    return client.post("/api/cash-entries", headers=hdr, json=body)


def _transfer(client, hdr, **kw):
    body = {"from_account": "cash", "to_account": "whish", "amount": 40, "note": "deposit", "date": DAY}
    body.update(kw)
    return client.post("/api/account-transfers", headers=hdr, json=body)


def test_cash_entry_crud(app, client):
    a = make_tenant(client, "Biz A", "ce1")
    r = _entry(client, a)
    assert r.status_code == 201
    body = r.get_json()
    assert body["account"] == "cash" and body["amount"] == 50 and body["reason"] == "Owner top-up"
    assert body["date"] == DAY and body["created_by"] == "ce1" and body["created_at"]
    listed = client.get("/api/cash-entries", headers=a).get_json()
    assert [e["id"] for e in listed] == [body["id"]]
    assert client.get("/api/cash-entries", headers=a,
                      query_string={"start_date": "2026-10-06"}).get_json() == []
    assert client.get("/api/cash-entries", headers=a,
                      query_string={"start_date": DAY, "end_date": DAY}).get_json()[0]["id"] == body["id"]
    assert client.delete(f"/api/cash-entries/{body['id']}", headers=a).status_code == 200
    assert client.get("/api/cash-entries", headers=a).get_json() == []


def test_transfer_crud(app, client):
    a = make_tenant(client, "Biz A", "tr1")
    r = _transfer(client, a)
    assert r.status_code == 201
    body = r.get_json()
    assert body["from_account"] == "cash" and body["to_account"] == "whish" and body["amount"] == 40
    assert body["note"] == "deposit" and body["date"] == DAY
    assert [t["id"] for t in client.get("/api/account-transfers", headers=a).get_json()] == [body["id"]]
    assert client.delete(f"/api/account-transfers/{body['id']}", headers=a).status_code == 200
    assert client.get("/api/account-transfers", headers=a).get_json() == []


def test_finance_can_create_but_not_delete(app, client):
    a = make_tenant(client, "Biz A", "fin_adm")
    fin = auth_headers(client, "fin_user", role="finance")
    eid = _entry(client, fin).get_json()["id"]
    tid = _transfer(client, fin).get_json()["id"]
    assert client.get("/api/cash-entries", headers=fin).status_code == 200
    assert client.delete(f"/api/cash-entries/{eid}", headers=fin).status_code == 403
    assert client.delete(f"/api/account-transfers/{tid}", headers=fin).status_code == 403
    assert client.delete(f"/api/cash-entries/{eid}", headers=a).status_code == 200
    assert client.delete(f"/api/account-transfers/{tid}", headers=a).status_code == 200


def test_employee_cannot_create(app, client):
    make_tenant(client, "Biz A", "emp_adm")
    emp = auth_headers(client, "emp_user", role="employee")
    assert _entry(client, emp).status_code == 403
    assert _transfer(client, emp).status_code == 403


def test_cross_tenant_delete_404(app, client):
    a = make_tenant(client, "Biz A", "xt_a")
    b = make_tenant(client, "Biz B", "xt_b")
    eid = _entry(client, a).get_json()["id"]
    tid = _transfer(client, a).get_json()["id"]
    assert client.delete(f"/api/cash-entries/{eid}", headers=b).status_code == 404
    assert client.delete(f"/api/account-transfers/{tid}", headers=b).status_code == 404
    assert client.get("/api/cash-entries", headers=b).get_json() == []
    assert len(client.get("/api/cash-entries", headers=a).get_json()) == 1


@pytest.mark.parametrize("kw", [
    {"account": "bank"}, {"account": None}, {"amount": 0}, {"amount": -5}, {"amount": "abc"}, {"amount": None},
    {"reason": ""}, {"reason": "   "}, {"reason": None}, {"date": "05/10/2026"},
])
def test_cash_entry_validation(app, client, kw):
    a = make_tenant(client, "Biz A", "ev1")
    assert _entry(client, a, **kw).status_code == 400
    assert client.get("/api/cash-entries", headers=a).get_json() == []


@pytest.mark.parametrize("kw", [
    {"from_account": "bank"}, {"to_account": "bank"}, {"to_account": "cash"},  # from == to
    {"amount": 0}, {"amount": -1}, {"amount": "x"}, {"date": "nope"},
])
def test_transfer_validation(app, client, kw):
    a = make_tenant(client, "Biz A", "tv1")
    assert _transfer(client, a, **kw).status_code == 400
    assert client.get("/api/account-transfers", headers=a).get_json() == []


def test_transfer_note_optional(app, client):
    a = make_tenant(client, "Biz A", "tn1")
    r = _transfer(client, a, note=None)
    assert r.status_code == 201 and r.get_json()["note"] is None


def test_manual_cash_in_on_correct_channel(app, client):
    a = make_tenant(client, "Biz A", "cf1")
    _entry(client, a, account="cash", amount=50, reason="Float")
    _entry(client, a, account="whish", amount=20, reason="Gift")
    _entry(client, a, account="cash", amount=7, reason="Other day", date="2026-10-06")
    flow = _flow(client, a)
    assert _totals(flow["cash_in"]) == {"Manual cash-in": 50}
    assert _totals(flow["whish_in"]) == {"Manual cash-in": 20}
    assert flow["cash_in"]["items"][0]["entries"][0]["description"] == "Float"
    assert flow["total_in"] == 70 and flow["total_out"] == 0 and flow["total_net"] == 70


def test_transfer_legs_and_combined_totals_exclude_it(app, client):
    a = make_tenant(client, "Biz A", "cf2")
    _entry(client, a, account="cash", amount=100, reason="Float")
    _transfer(client, a, from_account="cash", to_account="whish", amount=40)
    flow = _flow(client, a)
    assert _totals(flow["cash_out"]) == {"Transfer to Whish": 40}
    assert _totals(flow["whish_in"]) == {"Transfer from Cash": 40}
    assert flow["cash_in"]["total"] == 100 and flow["net"] == 60
    assert flow["whish_net"] == 40
    # Combined view ignores the transfer entirely.
    assert flow["total_in"] == 100 and flow["total_out"] == 0 and flow["total_net"] == 100

    _transfer(client, a, from_account="whish", to_account="cash", amount=10)
    flow = _flow(client, a)
    assert _totals(flow["whish_out"]) == {"Transfer to Cash": 10}
    assert _totals(flow["cash_in"]) == {"Manual cash-in": 100, "Transfer from Whish": 10}
    assert flow["total_in"] == 100 and flow["total_out"] == 0 and flow["total_net"] == 100


def test_next_day_balances_reflect_transfer(app, client):
    a = make_tenant(client, "Biz A", "cf3")
    client.put("/api/reports/cash-opening", headers=a,
               json={"date": "2026-10-04", "amount": 100, "whish_amount": 10})
    _transfer(client, a, from_account="cash", to_account="whish", amount=40)
    _entry(client, a, account="whish", amount=5, reason="Gift")

    today = _flow(client, a)
    assert today["cash_start"] == 100 and today["cash_end"] == 60
    assert today["whish_start"] == 10 and today["whish_end"] == 55
    assert today["total_start"] == 110 and today["total_end"] == 115

    tomorrow = _flow(client, a, "2026-10-06")
    assert tomorrow["cash_start"] == 60 and tomorrow["whish_start"] == 55
    assert tomorrow["total_start"] == 115
