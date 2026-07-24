from datetime import datetime
import app as appmod
from tests.conftest import make_tenant


def _names(resp):
    data = resp.get_json()
    items = data if isinstance(data, list) else data.get("items", [])
    return {i.get("name") for i in items}


def test_employee_model_defaults(app):
    with app.app_context():
        tenant = appmod.Tenant(name="Biz", slug="biz")
        appmod.db.session.add(tenant)
        appmod.db.session.flush()

        employee = appmod.Employee(
            tenant_id=tenant.id,
            name="Jane Doe",
            monthly_salary=1500.0,
            hire_date=datetime(2026, 1, 1),
        )
        appmod.db.session.add(employee)
        appmod.db.session.commit()

        assert employee.active is True
        assert employee.balance == 0.0
        d = employee.to_dict()
        assert d["name"] == "Jane Doe"
        assert d["monthly_salary"] == 1500.0
        assert d["active"] is True
        assert d["balance"] == 0.0

        charge = appmod.SalaryCharge(
            tenant_id=tenant.id, employee_id=employee.id,
            type="bonus", amount=200.0, period="2026-01", reason="Holiday bonus"
        )
        appmod.db.session.add(charge)
        appmod.db.session.commit()
        assert charge.to_dict()["type"] == "bonus"
        assert charge.to_dict()["amount"] == 200.0

        payment = appmod.SalaryPayment(
            tenant_id=tenant.id, employee_id=employee.id, amount=500.0, method="cash"
        )
        appmod.db.session.add(payment)
        appmod.db.session.commit()
        assert payment.to_dict()["amount"] == 500.0
        assert payment.to_dict()["is_advance"] is False


def test_employee_isolation_and_crud(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    r = client.post("/api/employees", headers=a,
                     json={"name": "EmployeeA", "monthly_salary": 1000, "hire_date": "2026-01-01"})
    assert r.status_code == 201
    emp = r.get_json()
    assert emp["balance"] == 0.0
    assert emp["active"] is True

    # Tenant B sees none of tenant A's employees.
    assert _names(client.get("/api/employees", headers=b)) == set()
    assert "EmployeeA" in _names(client.get("/api/employees", headers=a))

    r2 = client.put(f"/api/employees/{emp['id']}", headers=a,
                     json={"monthly_salary": 1200, "active": False})
    assert r2.status_code == 200
    assert r2.get_json()["monthly_salary"] == 1200
    assert r2.get_json()["active"] is False

    # Tenant B cannot reach tenant A's employee.
    r3 = client.put(f"/api/employees/{emp['id']}", headers=b, json={"monthly_salary": 1})
    assert r3.status_code == 404

    r4 = client.delete(f"/api/employees/{emp['id']}", headers=a)
    assert r4.status_code == 200


def test_cannot_delete_employee_with_linked_charges_or_payments(app, client):
    a = make_tenant(client, "Biz A", "a_admin")

    # Create first employee to test SalaryCharge guard
    r = client.post("/api/employees", headers=a,
                     json={"name": "EmployeeA", "monthly_salary": 1000, "hire_date": "2026-01-01"})
    assert r.status_code == 201
    emp_id_1 = r.get_json()["id"]

    # Create second employee to test SalaryPayment guard
    r = client.post("/api/employees", headers=a,
                     json={"name": "EmployeeB", "monthly_salary": 1000, "hire_date": "2026-01-01"})
    assert r.status_code == 201
    emp_id_2 = r.get_json()["id"]

    # Inside app context, add SalaryCharge to first employee
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(slug="biz-a").first()

        # Add SalaryCharge to emp_id_1
        charge = appmod.SalaryCharge(
            tenant_id=tenant.id,
            employee_id=emp_id_1,
            type="bonus",
            amount=100.0,
            period="2026-01"
        )
        appmod.db.session.add(charge)
        appmod.db.session.commit()

        # Add SalaryPayment to emp_id_2
        payment = appmod.SalaryPayment(
            tenant_id=tenant.id,
            employee_id=emp_id_2,
            amount=500.0,
            method="cash"
        )
        appmod.db.session.add(payment)
        appmod.db.session.commit()

    # Test: DELETE with linked SalaryCharge should return 400
    r1 = client.delete(f"/api/employees/{emp_id_1}", headers=a)
    assert r1.status_code == 400
    assert "salary charges" in r1.get_json()["error"].lower()

    # Test: DELETE with linked SalaryPayment should return 400
    r2 = client.delete(f"/api/employees/{emp_id_2}", headers=a)
    assert r2.status_code == 400
    assert "payments" in r2.get_json()["error"].lower()
