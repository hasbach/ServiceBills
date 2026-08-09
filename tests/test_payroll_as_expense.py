from tests.conftest import make_tenant


def _make_employee(client, hdr, name="Jane Doe", monthly_salary=1000):
    r = client.post("/api/employees", headers=hdr, json={"name": name, "monthly_salary": monthly_salary})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["id"] if "id" in r.get_json() else r.get_json()["employee"]["id"]


def test_new_tenant_gets_default_expense_categories(app, client):
    a = make_tenant(client, "Biz A", "a_seed1")
    names = {c["name"] for c in client.get("/api/expense_categories", headers=a).get_json()}
    assert {"Rent", "Payroll", "Electricity"}.issubset(names)


def test_add_expense_with_employee_deducts_balance_and_shows_in_history(app, client):
    a = make_tenant(client, "Biz A", "a_payexp1")
    employee_id = _make_employee(client, a)

    r = client.post("/api/expenses", headers=a, json={
        "category": "Payroll", "amount": 400, "date": "2026-06-15",
        "employee_id": employee_id, "description": ""
    })
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body["category"] == "Payroll"
    assert body["employee_id"] == employee_id
    assert body["employee_name"] == "Jane Doe"
    assert body["is_credit"] is False
    # No description given -- auto-filled rather than left blank.
    assert "Jane Doe" in body["description"]

    employee = client.get("/api/employees", headers=a).get_json()[0]
    assert employee["balance"] == -400.0

    history = client.get(f"/api/employees/{employee_id}/history", headers=a).get_json()["history"]
    payment_entry = next(h for h in history if h["type"] == "payment")
    assert payment_entry["amount"] == -400.0


def test_add_expense_requires_valid_employee(app, client):
    a = make_tenant(client, "Biz A", "a_payexp2")
    r = client.post("/api/expenses", headers=a, json={
        "category": "Payroll", "amount": 100, "date": "2026-06-15",
        "employee_id": 999999, "description": ""
    })
    assert r.status_code == 400


def test_update_expense_moving_between_employees_adjusts_both_balances(app, client):
    a = make_tenant(client, "Biz A", "a_payexp3")
    emp1 = _make_employee(client, a, name="Emp One")
    emp2 = _make_employee(client, a, name="Emp Two")

    r = client.post("/api/expenses", headers=a, json={
        "category": "Payroll", "amount": 300, "date": "2026-06-15",
        "employee_id": emp1, "description": "pay"
    })
    expense_id = r.get_json()["id"]

    employees = {e["id"]: e for e in client.get("/api/employees", headers=a).get_json()}
    assert employees[emp1]["balance"] == -300.0
    assert employees[emp2]["balance"] == 0.0

    # Re-point the same expense at emp2 instead.
    r2 = client.put(f"/api/expenses/{expense_id}", headers=a, json={"employee_id": emp2})
    assert r2.status_code == 200, r2.get_data(as_text=True)

    employees = {e["id"]: e for e in client.get("/api/employees", headers=a).get_json()}
    assert employees[emp1]["balance"] == 0.0  # reverted
    assert employees[emp2]["balance"] == -300.0  # applied


def test_delete_payroll_expense_reverses_employee_balance(app, client):
    a = make_tenant(client, "Biz A", "a_payexp4")
    employee_id = _make_employee(client, a)

    r = client.post("/api/expenses", headers=a, json={
        "category": "Payroll", "amount": 250, "date": "2026-06-15",
        "employee_id": employee_id, "description": "pay"
    })
    expense_id = r.get_json()["id"]
    assert client.get("/api/employees", headers=a).get_json()[0]["balance"] == -250.0

    r2 = client.delete(f"/api/expenses/{expense_id}", headers=a)
    assert r2.status_code == 200

    assert client.get("/api/employees", headers=a).get_json()[0]["balance"] == 0.0


def test_pay_salary_button_and_add_expense_form_are_the_same_underlying_record(app, client):
    """Confirms the two entry points (Payroll page's Pay button, Expenses page's
    Add Expense form) both produce real Expense rows -- one source of truth,
    not two different tables that reports have to reconcile."""
    a = make_tenant(client, "Biz A", "a_payexp5")
    employee_id = _make_employee(client, a)

    r1 = client.post(f"/api/employees/{employee_id}/payments", headers=a,
                      json={"amount": 200, "method": "cash", "payment_date": "2026-06-10"})
    assert r1.status_code == 201
    assert r1.get_json()["payment"]["category"] == "Payroll"
    assert r1.get_json()["payment"]["employee_id"] == employee_id

    r2 = client.post("/api/expenses", headers=a, json={
        "category": "Payroll", "amount": 100, "date": "2026-06-11",
        "employee_id": employee_id, "description": "extra bonus payout"
    })
    assert r2.status_code == 201

    employee = client.get("/api/employees", headers=a).get_json()[0]
    assert employee["balance"] == -300.0  # both payments deducted, same ledger

    expenses = client.get("/api/expenses", headers=a,
                          query_string={"start_date": "2026-06-01", "end_date": "2026-06-30"}).get_json()
    payroll_rows = [e for e in expenses if e.get("employee_id") == employee_id]
    assert len(payroll_rows) == 2
    assert {r["amount"] for r in payroll_rows} == {200, 100}


def test_dashboard_date_range_filters_revenue_and_expenses(app, client):
    a = make_tenant(client, "Biz A", "a_dash1")
    employee_id = _make_employee(client, a)

    client.post("/api/expenses", headers=a, json={
        "category": "Payroll", "amount": 500, "date": "2026-01-15",
        "employee_id": employee_id, "description": "old payment"
    })
    client.post("/api/expenses", headers=a, json={
        "category": "Payroll", "amount": 700, "date": "2026-06-15",
        "employee_id": employee_id, "description": "recent payment"
    })

    # No range -- both count.
    body_all = client.get("/api/dashboard", headers=a).get_json()
    assert body_all["payrollExpenses"] == 1200.0

    # Scoped to June -- only the recent one counts.
    body_june = client.get("/api/dashboard", headers=a, query_string={
        "start_date": "2026-06-01", "end_date": "2026-06-30"
    }).get_json()
    assert body_june["payrollExpenses"] == 700.0

    # Customer counts are a snapshot, unaffected by the date range.
    assert body_june["totalCustomers"] == body_all["totalCustomers"]
