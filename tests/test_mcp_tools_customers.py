from datetime import datetime, timedelta

from tests.conftest import make_tenant
from tests.mcp_helpers import call_tool, connect, enable_connector, mcp, tool_data


def _plan(client, hdr):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "Basic", "price": 10, "billing_cycle": "monthly"})
    return r.get_json()["plan"]["id"]


def _customer(client, hdr, plan_id, name, phone, expiry_in_days, balance=0.0, **fields):
    import app as appmod
    r = client.post("/api/customers", headers=hdr, json={
        "name": name, "phone": phone, "address": "Main st", "subscription_plan_id": plan_id,
        "subscription_start_date": "2026-01-01"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    c = appmod.Customer.query.filter_by(name=name).one()
    # "Today" in the business timezone (Asia/Beirut by default), the same one
    # the tools use -- a UTC midnight would be off by a day after 21:00 UTC.
    biz_today = datetime.now(appmod._zone_for(None)).date()
    c.subscription_expiry_date = datetime.combine(biz_today + timedelta(days=expiry_in_days), datetime.min.time())
    c.balance = balance
    for k, v in fields.items():
        setattr(c, k, v)
    appmod.db.session.commit()
    return c.id


def _setup(client, username="cu_admin", business="CU Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    plan = _plan(client, hdr)
    tok, _, _ = connect(client, hdr)
    return hdr, plan, tok


def test_search_by_name_phone_and_mac(client):
    hdr, plan, tok = _setup(client)
    ali = _customer(client, hdr, plan, "Ali Haddad", "03123456", 5, cpe_mac_address="AA-BB-CC-DD-EE-FF")
    _customer(client, hdr, plan, "Rami Saad", "70111222", 20)
    by_name = tool_data(call_tool(client, tok, "search_customers", {"query": "hadd"}))
    assert [c["id"] for c in by_name["customers"]] == [ali]
    by_phone = tool_data(call_tool(client, tok, "search_customers", {"query": "+961 3 123 456"}))
    assert [c["id"] for c in by_phone["customers"]] == [ali]
    by_mac = tool_data(call_tool(client, tok, "search_customers", {"query": "aa:bb:cc:dd:ee:ff"}))
    assert [c["id"] for c in by_mac["customers"]] == [ali]
    none = tool_data(call_tool(client, tok, "search_customers", {"query": "zzz"}))
    assert none["count"] == 0 and "hint" in none and "as_of" in none


def test_search_never_crosses_tenants(client):
    hdr_a, plan_a, tok_a = _setup(client, "ta_admin", "Biz A")
    _customer(client, hdr_a, plan_a, "Shared Name A", "03999888", 3)
    _, _, tok_b = _setup(client, "tb_admin", "Biz B")
    assert tool_data(call_tool(client, tok_b, "search_customers", {"query": "Shared"}))["count"] == 0
    assert tool_data(call_tool(client, tok_b, "search_customers", {"query": "03999888"}))["count"] == 0


def test_get_customer_details_and_other_tenant_is_not_found(client):
    hdr, plan, tok = _setup(client)
    cid = _customer(client, hdr, plan, "Owes Money", "03222333", 2, balance=-25.0)
    d = tool_data(call_tool(client, tok, "get_customer", {"customer_id": cid}))
    assert d["found"] is True
    c = d["customer"]
    assert c["owes"] == 25.0 and c["balance"] == -25.0 and c["plan"] == "Basic"
    assert c["days_to_expiry"] == 2
    assert isinstance(d["recent_payments"], list) and isinstance(d["open_tickets"], list)
    _, _, tok_other = _setup(client, "other_admin", "Other Biz")
    assert tool_data(call_tool(client, tok_other, "get_customer", {"customer_id": cid}))["found"] is False


def test_list_customers_filters(client):
    hdr, plan, tok = _setup(client)
    soon = _customer(client, hdr, plan, "Soon", "03000001", 3)
    _customer(client, hdr, plan, "Later", "03000002", 40)
    owing = _customer(client, hdr, plan, "Owing", "03000003", 40, balance=-10.0)
    gone = _customer(client, hdr, plan, "Expired", "03000004", -2)
    d = tool_data(call_tool(client, tok, "list_customers", {"expiring_within_days": 7}))
    assert [c["id"] for c in d["customers"]] == [soon]
    d = tool_data(call_tool(client, tok, "list_customers", {"owing": True}))
    assert [c["id"] for c in d["customers"]] == [owing] and d["total_owed"] == 10.0
    d = tool_data(call_tool(client, tok, "list_customers", {"expired_within_days": 7}))
    assert [c["id"] for c in d["customers"]] == [gone]
    d = tool_data(call_tool(client, tok, "list_customers", {"plan": "basic"}))
    assert d["total"] == 4


def test_list_customers_caps_rows(client):
    hdr, plan, tok = _setup(client)
    for i in range(3):
        _customer(client, hdr, plan, f"Bulk {i}", f"0300010{i}", 10)
    d = tool_data(call_tool(client, tok, "list_customers", {"limit": 2}))
    assert d["total"] == 3 and d["returned"] == 2 and "note" in d


def test_bad_arguments_are_tool_errors(client):
    _, _, tok = _setup(client)
    r = call_tool(client, tok, "get_customer", {"customer_id": "abc"})
    assert r["isError"] is True and "whole number" in r["content"][0]["text"]
    r = call_tool(client, tok, "list_customers", {"status": "weird"})
    assert r["isError"] is True


def test_customer_tools_listed(client):
    _, _, tok = _setup(client)
    names = {t["name"] for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert {"search_customers", "get_customer", "list_customers"} <= names
