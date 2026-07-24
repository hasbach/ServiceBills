import app as appmod
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

        # An inactive employee never accrues.
        employee_a.active = False
        appmod.db.session.commit()
        charges_before = appmod.SalaryCharge.query.filter_by(tenant_id=a_tid).count()
        appmod.generate_missing_salary_charges(a_tid)
        assert appmod.SalaryCharge.query.filter_by(tenant_id=a_tid).count() == charges_before
