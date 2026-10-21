from datetime import datetime

from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import call_tool, connect, enable_connector, tool_data, tool_error_text


def _seed(client):
    """Tenant with one customer and one paid payment on 2026-10-05."""
    import app as appmod
    hdr = make_tenant(client, "Fin Biz", "fin_admin")
    enable_connector("fin_admin")
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "Basic", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr, json={
        "name": "Payer", "phone": "03555666", "address": "x", "subscription_plan_id": plan,
        "subscription_start_date": "2026-10-01"})
    c = appmod.Customer.query.filter_by(name="Payer").one()
    appmod.db.session.add(appmod.Payment(
        tenant_id=c.tenant_id, customer_id=c.id, amount=42.0, currency="USD",
        date=datetime(2026, 10, 5), paid=True, paid_at=datetime(2026, 10, 5, 9, 0)))
    appmod.db.session.commit()
    return hdr


def test_list_payments_matches_payments_endpoint(client):
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_payments",
                            {"paid_from": "2026-10-05", "paid_to": "2026-10-05", "status": "paid"}))
    web = client.get("/api/payments", headers=hdr, query_string={
        "paid_date_start": "2026-10-05", "paid_date_end": "2026-10-05", "status": "paid"}).get_json()
    assert d["total"] == web["total"] >= 1
    assert d["totals"] == web["totals"]
    assert any(p["amount"] == 42.0 and p["customer_name"] == "Payer" for p in d["payments"])


def test_cashier_can_list_payments_but_not_cash_flow(client):
    _seed(client)
    cashier = auth_headers(client, username="fin_cashier", role="cashier")
    tok, _, _ = connect(client, cashier)
    assert "payments" in tool_data(call_tool(client, tok, "list_payments", {}))
    assert "role (cashier)" in tool_error_text(call_tool(client, tok, "get_cash_flow", {"day": "2026-10-05"}))


def test_collector_cannot_list_payments(client):
    _seed(client)
    collector = auth_headers(client, username="fin_collector", role="collector")
    tok, _, _ = connect(client, collector)
    assert "role (collector)" in tool_error_text(call_tool(client, tok, "list_payments", {}))


def test_cash_flow_matches_daily_cash_endpoint(client):
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "get_cash_flow", {"day": "2026-10-05"}))
    web = client.get("/api/reports/daily-cash", headers=hdr, query_string={"day": "2026-10-05"}).get_json()
    assert d["collected_total"] == web["grand_total"]
    assert d["currency"] == web["reporting_currency"]
    assert d["cash_flow"]["day"] == web["cash_flow"]["day"]
    assert d["cash_flow"]["total_in"] == web["cash_flow"]["total_in"]


def test_financial_summary_month(client):
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "get_financial_summary", {"month": "2026-10"}))
    web = client.get("/api/reports/financial", headers=hdr,
                     query_string={"start_date": "2026-10-01", "end_date": "2026-10-31"}).get_json()
    assert d["totals"] == web["totals"]
    assert d["period"] == {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    assert "active_customers" in d["snapshot"]
    r = call_tool(client, tok, "get_financial_summary", {"month": "October"})
    assert r["isError"] is True
