from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import (call_tool, connect, enable_connector, enable_writes, mcp, tool_data,
                               tool_error_text)


def _setup(client, username="wx_admin", business="WX Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    enable_writes(username)
    tok, _, _ = connect(client, hdr)
    return hdr, tok


def test_expense_add_update_delete(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/expense_categories", headers=hdr, json={"name": "Fuel"})
    assert "Fuel" in tool_data(call_tool(client, tok, "list_expense_categories", {}))["categories"]
    d = tool_data(call_tool(client, tok, "add_expense", {"amount": 25, "category": "Fuel",
                                                          "description": "generator"}))
    eid = d["expense"]["id"]
    tool_data(call_tool(client, tok, "update_expense", {"expense_id": eid, "amount": 30}))
    assert float(appmod.db.session.get(appmod.Expense, eid).amount) == 30.0
    assert "not found" in tool_error_text(call_tool(client, tok, "add_expense",
                                                    {"amount": 5, "category": "Nope"}))
    tool_data(call_tool(client, tok, "delete_expense", {"expense_id": eid, "reason": "duplicate"}))
    assert appmod.db.session.get(appmod.Expense, eid) is None


def test_expense_paid_via(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/expense_categories", headers=hdr, json={"name": "Fuel"})
    a = tool_data(call_tool(client, tok, "add_expense", {"amount": 5, "category": "Fuel", "paid_via": "whish"}))
    b = tool_data(call_tool(client, tok, "add_expense", {"amount": 6, "category": "Fuel"}))
    assert appmod.db.session.get(appmod.Expense, a["expense"]["id"]).paid_via == "whish"
    assert appmod.db.session.get(appmod.Expense, b["expense"]["id"]).paid_via is None
    before = appmod.Expense.query.count()
    msg = tool_error_text(call_tool(client, tok, "add_expense", {"amount": 7, "category": "Fuel", "paid_via": "bank"}))
    assert "must be one of" in msg
    assert appmod.Expense.query.count() == before


def test_pay_employee_paid_via_and_schemas(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/employees", headers=hdr, json={"name": "Rami"})
    emp_id = appmod.Employee.query.filter_by(name="Rami").one().id
    tool_data(call_tool(client, tok, "pay_employee", {"employee_id": emp_id, "amount": 20, "paid_via": "whish"}))
    exp = appmod.Expense.query.filter_by(employee_id=emp_id).one()
    assert exp.paid_via == "whish"
    tools = {t["name"]: t for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    for name in ("add_expense", "update_expense", "pay_supplier", "pay_employee",
                 "reseller_collect_payment", "refund_payment"):
        assert "paid_via" in tools[name]["inputSchema"]["properties"], name
    assert "paid_via" not in tools["reseller_add_credit"]["inputSchema"]["properties"]


def test_cash_entry_and_transfer(client):
    import app as appmod
    _, tok = _setup(client)
    d = tool_data(call_tool(client, tok, "add_cash_entry", {"account": "cash", "amount": 50, "reason": "float"}))
    eid = d["entry"]["id"]
    d = tool_data(call_tool(client, tok, "transfer_between_accounts",
                            {"from_account": "cash", "to_account": "whish", "amount": 20}))
    tid = d["transfer"]["id"]
    tool_data(call_tool(client, tok, "delete_transfer", {"transfer_id": tid, "reason": "typo"}))
    tool_data(call_tool(client, tok, "delete_cash_entry", {"entry_id": eid, "reason": "typo"}))
    assert appmod.CashEntry.query.count() == 0 and appmod.AccountTransfer.query.count() == 0


def test_supplier_payment(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/suppliers", headers=hdr, json={"name": "Fiber Co"})
    sid = appmod.Supplier.query.filter_by(name="Fiber Co").one().id
    d = tool_data(call_tool(client, tok, "pay_supplier", {"supplier_id": sid, "amount": 100}))
    pid = d["payment"]["id"]
    tool_data(call_tool(client, tok, "delete_supplier_payment",
                        {"supplier_id": sid, "payment_id": pid, "reason": "wrong supplier"}))
    assert appmod.db.session.get(appmod.SupplierPayment, pid) is None


def test_payroll_admin_only(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/employees", headers=hdr, json={"name": "Sami"})
    emp_id = appmod.Employee.query.filter_by(name="Sami").one().id
    d = tool_data(call_tool(client, tok, "add_employee_charge",
                            {"employee_id": emp_id, "type": "salary", "amount": 400}))
    assert d["employee"]["balance"] == 400.0
    d = tool_data(call_tool(client, tok, "pay_employee", {"employee_id": emp_id, "amount": 150, "is_advance": True}))
    assert d["employee"]["balance"] == 250.0
    assert "period" in tool_error_text(call_tool(client, tok, "add_employee_charge",
                                                 {"employee_id": emp_id, "type": "bonus", "amount": 1,
                                                  "period": "2026-13"}))
    fin = auth_headers(client, username="wx_fin", role="finance")
    tok_fin, _, _ = connect(client, fin)
    names = {t["name"] for t in mcp(client, tok_fin, "tools/list").get_json()["result"]["tools"]}
    assert "pay_employee" not in names and "add_expense" in names


def test_reseller_credit_and_collect(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/resellers", headers=hdr, json={"name": "R1", "phone": "1", "type": "type1"})
    rid = appmod.Reseller.query.filter_by(name="R1").one().id
    d = tool_data(call_tool(client, tok, "reseller_add_credit", {"reseller_id": rid, "amount": 30}))
    assert "Credit added" in d["result"]
    tool_data(call_tool(client, tok, "reseller_collect_payment", {"reseller_id": rid, "amount": 10}))
