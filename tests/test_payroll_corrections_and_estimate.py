"""Payroll follow-ups: accrued salary labelled with the month worked, combined
admin roles, admin-only edit/delete of charges, advances flagged, and payroll
counted in the estimated profit."""
from datetime import datetime

from dateutil.relativedelta import relativedelta

import app as appmod
from tests.conftest import make_tenant


def _login(client, username, password="pw"):
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _employee(client, hdr, salary=200, hire="2026-01-01", name="Emp"):
    return client.post("/api/employees", headers=hdr,
                       json={"name": name, "monthly_salary": salary, "hire_date": hire}).get_json()["id"]


def _balance(client, hdr, eid):
    return next(e["balance"] for e in client.get("/api/employees", headers=hdr).get_json() if e["id"] == eid)


def test_accrued_salary_is_labelled_with_the_month_worked(app, client):
    hdr = make_tenant(client, "Biz P1", "p1_admin")
    eid = _employee(client, hdr, hire="2026-01-01")
    with app.app_context():
        tid = appmod.Employee.query.get(eid).tenant_id
        appmod.generate_missing_salary_charges(tid)
        charges = appmod.SalaryCharge.query.filter_by(employee_id=eid, type="salary") \
            .order_by(appmod.SalaryCharge.date).all()
        first = charges[0]
        assert first.date.strftime("%Y-%m-%d") == "2026-02-01"   # charged when January ends
        assert first.period == "2026-01"                          # ...for January's work
        assert all(c.period == (c.date - relativedelta(months=1)).strftime("%Y-%m") for c in charges)


def test_combined_admin_role_can_use_admin_only_pages(client):
    admin = make_tenant(client, "Biz P2", "p2_admin")
    client.post("/api/users", headers=admin, json={"username": "p2_both", "password": "pw", "role": "admin,finance"})
    both = _login(client, "p2_both")
    assert client.get("/api/employees", headers=both).status_code == 200
    client.post("/api/users", headers=admin, json={"username": "p2_fin", "password": "pw", "role": "finance"})
    assert client.get("/api/employees", headers=_login(client, "p2_fin")).status_code == 403


def test_admin_edits_and_deletes_charges_with_balance_kept_consistent(client):
    hdr = make_tenant(client, "Biz P3", "p3_admin")
    eid = _employee(client, hdr, salary=0)
    bonus = client.post(f"/api/employees/{eid}/charges", headers=hdr,
                        json={"type": "bonus", "amount": 50, "reason": "eid"}).get_json()["charge"]["id"]
    assert _balance(client, hdr, eid) == 50

    r = client.put(f"/api/employees/{eid}/charges/{bonus}", headers=hdr, json={"amount": 80, "reason": "eid bonus"})
    assert r.status_code == 200, r.get_json()
    assert _balance(client, hdr, eid) == 80

    # Turning it into a deduction flips its effect: -80 instead of +80.
    assert client.put(f"/api/employees/{eid}/charges/{bonus}", headers=hdr, json={"type": "deduction"}).status_code == 200
    assert _balance(client, hdr, eid) == -80

    assert client.delete(f"/api/employees/{eid}/charges/{bonus}", headers=hdr).status_code == 200
    assert _balance(client, hdr, eid) == 0
    assert client.put(f"/api/employees/{eid}/charges/{bonus}", headers=hdr, json={"amount": 1}).status_code == 404


def test_latest_salary_charge_cannot_be_deleted_but_can_be_zeroed(app, client):
    hdr = make_tenant(client, "Biz P4", "p4_admin")
    eid = _employee(client, hdr, salary=100, hire="2026-06-01")
    with app.app_context():
        appmod.generate_missing_salary_charges(appmod.Employee.query.get(eid).tenant_id)
        salary = appmod.SalaryCharge.query.filter_by(employee_id=eid, type="salary") \
            .order_by(appmod.SalaryCharge.date.desc()).all()
        latest_id, older_id = salary[0].id, salary[-1].id
    before = _balance(client, hdr, eid)

    assert client.delete(f"/api/employees/{eid}/charges/{latest_id}", headers=hdr).status_code == 400
    assert client.put(f"/api/employees/{eid}/charges/{latest_id}", headers=hdr, json={"amount": 0}).status_code == 200
    assert _balance(client, hdr, eid) == before - 100
    assert client.delete(f"/api/employees/{eid}/charges/{older_id}", headers=hdr).status_code == 200
    assert _balance(client, hdr, eid) == before - 200


def test_only_admin_can_correct_charges(client):
    admin = make_tenant(client, "Biz P5", "p5_admin")
    eid = _employee(client, admin, salary=0)
    cid = client.post(f"/api/employees/{eid}/charges", headers=admin,
                      json={"type": "bonus", "amount": 10}).get_json()["charge"]["id"]
    client.post("/api/users", headers=admin, json={"username": "p5_fin", "password": "pw", "role": "finance"})
    fin = _login(client, "p5_fin")
    assert client.put(f"/api/employees/{eid}/charges/{cid}", headers=fin, json={"amount": 99}).status_code == 403
    assert client.delete(f"/api/employees/{eid}/charges/{cid}", headers=fin).status_code == 403


def test_advance_is_stored_and_shown_as_advance(client):
    hdr = make_tenant(client, "Biz P6", "p6_admin")
    eid = _employee(client, hdr, salary=0)
    r = client.post(f"/api/employees/{eid}/payments", headers=hdr, json={"amount": 40, "is_advance": True})
    assert r.get_json()["payment"]["is_advance"] is True
    client.post(f"/api/employees/{eid}/payments", headers=hdr, json={"amount": 10})
    hist = client.get(f"/api/employees/{eid}/history", headers=hdr).get_json()["history"]
    assert sorted((h["type"], -h["amount"]) for h in hist) == [("advance", 40), ("payment", 10)]


def test_estimated_profit_subtracts_this_months_payroll(app, client):
    hdr = make_tenant(client, "Biz P7", "p7_admin")
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 100, "cost": 30, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr, json={"name": "C", "phone": "1", "address": "a",
                                                     "subscription_plan_id": plan, "subscription_start_date": "2026-01-01"})
    _employee(client, hdr, salary=25, name="Active")
    inactive = _employee(client, hdr, salary=1000, name="Gone")
    client.put(f"/api/employees/{inactive}", headers=hdr, json={"active": False})
    next_month = (datetime.utcnow().replace(day=1) + relativedelta(months=1)).strftime("%Y-%m-%d")
    _employee(client, hdr, salary=500, hire=next_month, name="Starts next month")

    with app.app_context():
        tid = appmod.Employee.query.filter_by(name="Active").first().tenant_id
        est = appmod.MonthlyProfitEstimate.query.filter_by(
            tenant_id=tid, month=datetime.utcnow().strftime("%Y-%m")).first()
        assert est.estimated_payroll == 25
        assert est.estimated_profit == est.estimated_income - est.estimated_cost - 25
