from tests.conftest import make_tenant


def _make_category(client, hdr, name="Office Supplies"):
    # Rent/Payroll/Electricity are now auto-seeded per tenant (see
    # seed_default_expense_categories) -- use a name that isn't one of those,
    # so this helper's own creation doesn't collide with what already exists.
    r = client.post("/api/expense_categories", headers=hdr, json={"name": name})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["name"]


def _make_expense(client, hdr, category, amount=100, description="Office rent", date="2026-06-15"):
    r = client.post("/api/expenses", headers=hdr,
                    json={"category": category, "amount": amount, "description": description,
                          "date": date, "is_credit": False})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


def _make_supplier(client, hdr, name="Acme Supplies"):
    r = client.post("/api/suppliers", headers=hdr, json={"name": name})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["supplier"]["id"] if "supplier" in r.get_json() else r.get_json()["id"]


def _pay_supplier(client, hdr, supplier_id, amount=200, note="Invoice #123", date="2026-06-10"):
    r = client.post(f"/api/suppliers/{supplier_id}/payments", headers=hdr,
                    json={"amount": amount, "payment_method": "bank", "reference_note": note,
                          "payment_date": date})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


def _make_employee(client, hdr, name="Jane Doe", monthly_salary=1000):
    r = client.post("/api/employees", headers=hdr, json={"name": name, "monthly_salary": monthly_salary})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["id"] if "id" in r.get_json() else r.get_json()["employee"]["id"]


def _pay_employee(client, hdr, employee_id, amount=500, method="cash", date="2026-06-20"):
    r = client.post(f"/api/employees/{employee_id}/payments", headers=hdr,
                    json={"amount": amount, "method": method, "payment_date": date})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()


def test_expenses_list_includes_supplier_and_payroll_payments(app, client):
    a = make_tenant(client, "Biz A", "a_exp1")
    category = _make_category(client, a)
    _make_expense(client, a, category, amount=100, description="Office rent")

    supplier_id = _make_supplier(client, a)
    _pay_supplier(client, a, supplier_id, amount=200, note="Invoice #123")

    employee_id = _make_employee(client, a)
    _pay_employee(client, a, employee_id, amount=500)

    r = client.get("/api/expenses", headers=a,
                   query_string={"start_date": "2026-06-01", "end_date": "2026-06-30"})
    assert r.status_code == 200, r.get_data(as_text=True)
    rows = r.get_json()

    manual_rows = [x for x in rows if x["source"] == "manual"]
    supplier_row = next(x for x in rows if x["source"] == "supplier_payment")
    # Payroll payments are now real Expense rows (see record_employee_payment) --
    # they come back with source='manual' like any other real expense, just
    # tagged with employee_id and the Payroll category.
    payroll_row = next(x for x in manual_rows if x["employee_id"] is not None)
    manual = next(x for x in manual_rows if x["employee_id"] is None)

    assert manual["category"] == category
    assert manual["amount"] == 100

    assert supplier_row["category"] == "Supplier Payments"
    assert supplier_row["amount"] == 200
    assert supplier_row["supplier_name"] == "Acme Supplies"
    assert supplier_row["description"] == "Invoice #123"

    assert payroll_row["category"] == "Payroll"
    assert payroll_row["amount"] == 500
    assert payroll_row["employee_name"] == "Jane Doe"
    assert "Jane Doe" in payroll_row["description"]

    # Payroll is a real Expense row -- a real integer id, editable/deletable
    # like any other expense. Only the supplier-payment merge is synthetic
    # and read-only.
    assert isinstance(payroll_row["id"], int)
    assert isinstance(supplier_row["id"], str) and supplier_row["id"].startswith("supplier_payment-")


def test_expenses_list_respects_date_range_for_all_sources(app, client):
    a = make_tenant(client, "Biz A", "a_exp2")
    category = _make_category(client, a)
    _make_expense(client, a, category, amount=50, date="2026-01-15")  # out of range

    supplier_id = _make_supplier(client, a)
    _pay_supplier(client, a, supplier_id, amount=75, date="2026-01-10")  # out of range

    employee_id = _make_employee(client, a)
    _pay_employee(client, a, employee_id, amount=90)  # defaults to "now", in range

    r = client.get("/api/expenses", headers=a,
                   query_string={"start_date": "2026-06-01", "end_date": "2026-12-31"})
    rows = r.get_json()
    assert not any(x["employee_id"] is None and x["source"] == "manual" for x in rows)  # the out-of-range Office Supplies row
    assert not any(x["source"] == "supplier_payment" for x in rows)
    assert any(x["employee_id"] is not None for x in rows)  # the in-range payroll payment


def test_expenses_total_breakdown_sums_to_value(app, client):
    a = make_tenant(client, "Biz A", "a_exp3")
    category = _make_category(client, a)
    _make_expense(client, a, category, amount=100)
    supplier_id = _make_supplier(client, a)
    _pay_supplier(client, a, supplier_id, amount=200)
    employee_id = _make_employee(client, a)
    _pay_employee(client, a, employee_id, amount=300)

    rows = client.get("/api/reports/expenses-total", headers=a).get_json()
    row = next(r for r in rows if r["month"] == "2026-06")
    assert row["manual"] == 100
    assert row["supplier"] == 200
    assert row["payroll"] == 300
    assert row["value"] == 600


def test_financial_report_breakdown_sums_to_expenses(app, client):
    a = make_tenant(client, "Biz A", "a_exp4")
    category = _make_category(client, a)
    _make_expense(client, a, category, amount=100)
    supplier_id = _make_supplier(client, a)
    _pay_supplier(client, a, supplier_id, amount=200)
    employee_id = _make_employee(client, a)
    _pay_employee(client, a, employee_id, amount=300)

    r = client.get("/api/reports/financial", headers=a, query_string={
        "start_date": "2026-06-01T00:00:00Z", "end_date": "2026-06-30T23:59:59Z"
    })
    body = r.get_json()
    month = next(m for m in body["monthly_data"] if m["month"] == "2026-06")
    assert month["expenses_manual"] == 100
    assert month["expenses_supplier"] == 200
    assert month["expenses_payroll"] == 300
    assert month["expenses"] == 600
    assert body["totals"]["expenses_manual"] == 100
    assert body["totals"]["expenses_supplier"] == 200
    assert body["totals"]["expenses_payroll"] == 300


def test_dashboard_metrics_expense_breakdown_sums_to_total(app, client):
    a = make_tenant(client, "Biz A", "a_exp5")
    category = _make_category(client, a)
    _make_expense(client, a, category, amount=100)
    supplier_id = _make_supplier(client, a)
    _pay_supplier(client, a, supplier_id, amount=200)
    employee_id = _make_employee(client, a)
    _pay_employee(client, a, employee_id, amount=300)

    body = client.get("/api/dashboard", headers=a).get_json()
    assert body["manualExpenses"] == 100
    assert body["supplierExpenses"] == 200
    assert body["payrollExpenses"] == 300
    assert body["totalExpenses"] == 600
