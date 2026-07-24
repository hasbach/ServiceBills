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
