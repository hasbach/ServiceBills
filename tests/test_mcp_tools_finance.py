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


def test_list_payments_flags_reverted_but_not_repaid(client):
    import app as appmod
    hdr = _seed(client)
    c = appmod.Customer.query.filter_by(name="Payer").one()
    # re-paid: reverted at 10:00, paid again at 12:00 -> not reverted any more
    appmod.db.session.add(appmod.Payment(
        tenant_id=c.tenant_id, customer_id=c.id, amount=11.0, currency="USD", date=datetime(2026, 10, 6),
        paid=True, paid_at=datetime(2026, 10, 6, 12, 0), reverted_at=datetime(2026, 10, 6, 10, 0)))
    # reverted, never re-paid
    appmod.db.session.add(appmod.Payment(
        tenant_id=c.tenant_id, customer_id=c.id, amount=22.0, currency="USD", date=datetime(2026, 10, 7),
        paid=False, paid_at=datetime(2026, 10, 7, 8, 0), reverted_at=datetime(2026, 10, 7, 9, 0)))
    appmod.db.session.commit()
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_payments", {}))
    by_amount = {p["amount"]: p for p in d["payments"]}
    assert by_amount[11.0]["reverted"] is False
    assert by_amount[22.0]["reverted"] is True
    assert by_amount[42.0]["reverted"] is False
    assert all("reverted_at" not in p for p in d["payments"])
    assert {"collected_amount", "settled_from_credit"} <= set(by_amount[42.0])
    assert "timestamps" not in d


def test_list_payments_times_are_business_local(client):
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_payments", {}))
    p = [p for p in d["payments"] if p["amount"] == 42.0][0]
    # stored 09:00 UTC; Asia/Beirut is UTC+2 or +3 (DST) in October
    assert p["paid_at"] in ("2026-10-05 12:00", "2026-10-05 11:00")


def test_financial_summary_month_must_be_exact(client):
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)
    r = call_tool(client, tok, "get_financial_summary", {"month": "2026-10-15"})
    assert r["isError"] is True and "2026-10" in tool_error_text(r)


def test_5xx_from_wrapped_endpoint_is_generic(client, monkeypatch):
    import app as appmod
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)

    def boom(*a, **k):
        raise RuntimeError("secret internal detail")
    monkeypatch.setitem(appmod.app.view_functions, "get_payments", boom)
    text = tool_error_text(call_tool(client, tok, "list_payments", {}))
    assert "couldn't load this (500)" in text and "secret internal detail" not in text

    def soft_500(*a, **k):
        return appmod.jsonify({"error": "secret internal detail"}), 500
    monkeypatch.setitem(appmod.app.view_functions, "get_payments", soft_500)
    text = tool_error_text(call_tool(client, tok, "list_payments", {}))
    assert "couldn't load this (500)" in text and "secret internal detail" not in text


def test_4xx_from_wrapped_endpoint_passes_only_safe_messages(client, monkeypatch):
    import app as appmod
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)

    def respond(status, key, text):
        def view(*a, **k):
            return appmod.jsonify({key: text}), status
        monkeypatch.setitem(appmod.app.view_functions, "get_payments", view)
        return tool_error_text(call_tool(client, tok, "list_payments", {}))

    # 400 catch-alls can carry str(exception): never passed on, whatever the key
    for key in ("msg", "error", "message"):
        t = respond(400, key, "secret internal detail")
        assert "couldn't load this (400)" in t and "secret internal detail" not in t
    t = respond(422, "msg", "secret internal detail")
    assert "couldn't load this (422)" in t and "secret internal detail" not in t
    # 403/404 pass `msg` through, but not `error`
    assert "Not allowed here" in respond(403, "msg", "Not allowed here")
    assert "Nothing there" in respond(404, "msg", "Nothing there")
    t = respond(404, "error", "secret internal detail")
    assert "couldn't load this (404)" in t and "secret internal detail" not in t
    t = respond(403, "error", "secret internal detail")
    assert "secret internal detail" not in t
