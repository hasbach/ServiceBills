"""Finance roadmap 1A, fix 8 (owner rule, 2026-11-16): no back-pay. Adding an
employee with a hire date in the past, or reactivating one, creates no salary
owed for months already gone; a month is never charged twice."""
from datetime import datetime

from dateutil.relativedelta import relativedelta

import app as appmod
from tests.conftest import make_tenant


def _add(client, hdr, hire, salary=100):
    r = client.post("/api/employees", headers=hdr,
                    json={"name": "Emp", "monthly_salary": salary, "hire_date": hire})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["id"]


def _accrue(eid):
    emp = appmod.db.session.get(appmod.Employee, eid)
    appmod.generate_missing_salary_charges(emp.tenant_id)
    appmod.db.session.refresh(emp)
    charges = appmod.SalaryCharge.query.filter_by(employee_id=eid, type="salary").all()
    return emp, charges


def test_adding_an_employee_hired_years_ago_creates_no_back_pay(app, client):
    hdr = make_tenant(client, "Biz", "nb1")
    eid = _add(client, hdr, "2021-03-15")
    with app.app_context():
        emp, charges = _accrue(eid)
        assert charges == [] and float(emp.balance) == 0      # was ~68 months of salary


def test_salary_starts_with_the_first_cycle_that_ends_after_the_employee_was_added(app, client):
    hdr = make_tenant(client, "Biz", "nb2")
    eid = _add(client, hdr, "2021-03-15")
    with app.app_context():
        emp = appmod.db.session.get(appmod.Employee, eid)
        # Added six weeks ago: exactly one monthly cycle has ended since then.
        emp.accrual_start = datetime.utcnow() - relativedelta(weeks=6)
        appmod.db.session.commit()
        emp, charges = _accrue(eid)
        assert len(charges) in (1, 2)     # 6 weeks span one or two monthly anniversaries
        assert all(c.date > emp.accrual_start for c in charges)
        assert float(emp.balance) == 100 * len(charges)
        # Idempotent.
        emp, again = _accrue(eid)
        assert len(again) == len(charges)


def test_reactivating_an_employee_does_not_charge_the_inactive_months(app, client):
    hdr = make_tenant(client, "Biz", "nb3")
    eid = _add(client, hdr, "2026-01-10")
    with app.app_context():
        emp = appmod.db.session.get(appmod.Employee, eid)
        emp.accrual_start = None                       # in the system since hired
        emp.active = False
        # Last salary charge five months ago, then switched off.
        last = datetime.utcnow() - relativedelta(months=5)
        appmod.db.session.add(appmod.SalaryCharge(
            tenant_id=emp.tenant_id, employee_id=eid, type="salary", amount=100,
            period=(last - relativedelta(months=1)).strftime("%Y-%m"), date=last))
        emp.balance = 100
        appmod.db.session.commit()

    r = client.put(f"/api/employees/{eid}", headers=hdr, json={"active": True})
    assert r.status_code == 200, r.get_data(as_text=True)

    with app.app_context():
        emp, charges = _accrue(eid)
        assert len(charges) == 1 and float(emp.balance) == 100    # was 5 more months


def test_a_month_with_a_manual_salary_charge_is_not_charged_again(app, client):
    hdr = make_tenant(client, "Biz", "nb4")
    eid = _add(client, hdr, "2026-01-01")
    with app.app_context():
        emp = appmod.db.session.get(appmod.Employee, eid)
        emp.accrual_start = None
        appmod.db.session.commit()
        emp, charges = _accrue(eid)
        periods = sorted(c.period for c in charges)
        assert len(periods) == len(set(periods)) >= 2
        # Back-date the newest charge so its month falls due again, as a manual
        # entry for that same month would leave it.
        newest = max(charges, key=lambda c: c.date)
        newest.date = newest.date - relativedelta(months=1, days=3)
        appmod.db.session.commit()
        emp, after = _accrue(eid)
        periods_after = sorted(c.period for c in after)
        assert len(periods_after) == len(set(periods_after)), periods_after


def test_latest_salary_charge_cannot_be_turned_into_a_bonus(app, client):
    hdr = make_tenant(client, "Biz", "nb5")
    eid = _add(client, hdr, "2026-06-01")
    with app.app_context():
        emp = appmod.db.session.get(appmod.Employee, eid)
        emp.accrual_start = None
        appmod.db.session.commit()
        emp, charges = _accrue(eid)
        latest = max(charges, key=lambda c: (c.date, c.id)).id
        older = min(charges, key=lambda c: (c.date, c.id)).id

    assert client.put(f"/api/employees/{eid}/charges/{latest}", headers=hdr,
                      json={"type": "bonus"}).status_code == 400
    assert client.put(f"/api/employees/{eid}/charges/{older}", headers=hdr,
                      json={"type": "bonus"}).status_code == 200
