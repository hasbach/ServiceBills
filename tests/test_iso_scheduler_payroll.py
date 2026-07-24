import app as appmod
from dateutil.relativedelta import relativedelta
from tests.conftest import make_tenant


def test_salary_accrual_runs_without_request_and_is_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    client.post("/api/employees", headers=a,
                json={"name": "EmployeeA", "monthly_salary": 1000, "hire_date": "2026-01-01"})
    client.post("/api/employees", headers=b,
                json={"name": "EmployeeB", "monthly_salary": 500, "hire_date": "2026-01-01"})

    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        b_tid = appmod.Tenant.query.filter_by(slug="biz-b").first().id
        b_before = appmod.SalaryCharge.query.filter_by(tenant_id=b_tid).count()

        # The scheduler body runs with NO request context.
        appmod.generate_missing_salary_charges(a_tid)

        a_charges = appmod.SalaryCharge.query.filter_by(tenant_id=a_tid, type="salary").all()
        assert len(a_charges) >= 1
        employee_a = appmod.Employee.query.filter_by(tenant_id=a_tid).first()
        assert employee_a.balance == sum(c.amount for c in a_charges)

        # Tenant B is untouched, and every charge stays under its own tenant.
        assert appmod.SalaryCharge.query.filter_by(tenant_id=b_tid).count() == b_before
        assert appmod.SalaryCharge.query.filter(appmod.SalaryCharge.tenant_id.is_(None)).count() == 0

        # --- An inactive employee never accrues, even when a period is genuinely due. ---
        # Calling generate_missing_salary_charges(a_tid) again immediately would prove
        # nothing: the cadence loop above already advanced employee_a's cursor past
        # utcnow(), so a second call produces zero new charges regardless of the
        # active filter. To actually exercise the active=True gate we must first force
        # a period to be due again by backdating the most recent charge's date, THEN
        # deactivate the employee, so a real accrual opportunity is being suppressed.
        last_charge_a = (
            appmod.SalaryCharge.query
            .filter_by(tenant_id=a_tid, employee_id=employee_a.id, type="salary")
            .order_by(appmod.SalaryCharge.date.desc())
            .first()
        )
        assert last_charge_a is not None
        last_charge_a.date = last_charge_a.date - relativedelta(months=2)
        appmod.db.session.commit()

        employee_a.active = False
        appmod.db.session.commit()

        charges_before_a = appmod.SalaryCharge.query.filter_by(tenant_id=a_tid).count()
        appmod.generate_missing_salary_charges(a_tid)
        assert appmod.SalaryCharge.query.filter_by(tenant_id=a_tid).count() == charges_before_a

        # --- Control: an ACTIVE employee (tenant B) must still accrue once a period
        # is genuinely due. Without this, "no new charge" above could equally mean the
        # whole cadence mechanism silently broke, not that the active filter worked. ---
        appmod.generate_missing_salary_charges(b_tid)  # establish a baseline of charges for B
        employee_b = appmod.Employee.query.filter_by(tenant_id=b_tid).first()
        assert employee_b.active is True

        last_charge_b = (
            appmod.SalaryCharge.query
            .filter_by(tenant_id=b_tid, employee_id=employee_b.id, type="salary")
            .order_by(appmod.SalaryCharge.date.desc())
            .first()
        )
        assert last_charge_b is not None
        last_charge_b.date = last_charge_b.date - relativedelta(months=2)
        appmod.db.session.commit()

        charges_before_b = appmod.SalaryCharge.query.filter_by(tenant_id=b_tid).count()
        appmod.generate_missing_salary_charges(b_tid)
        assert appmod.SalaryCharge.query.filter_by(tenant_id=b_tid).count() == charges_before_b + 1
