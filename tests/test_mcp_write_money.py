from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import (call_tool, connect, enable_connector, enable_writes, tool_data,
                               tool_error_text)


def _setup(client, username="wm_admin", business="WM Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    enable_writes(username)
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "Basic", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    tok, _, _ = connect(client, hdr)
    cid = tool_data(call_tool(client, tok, "create_customer", {
        "name": "Payer", "phone": "03222333", "subscription_plan_id": plan}))["customer"]["id"]
    return hdr, tok, cid


def _paid_payment(client, tok, cid):
    pays = tool_data(call_tool(client, tok, "find_payments", {"customer_id": cid}))["payments"]
    return next(p for p in pays if p["paid"] and not p["reverted"] and not p["is_refund"])


def test_receive_payment_then_revert(client):
    import app as appmod
    _, tok, cid = _setup(client)
    d = tool_data(call_tool(client, tok, "receive_payment", {"customer_id": cid, "amount": 10}))
    assert d["customer"]["balance"] >= 0
    p = _paid_payment(client, tok, cid)
    assert "'reason' is required" in tool_error_text(call_tool(client, tok, "revert_payment",
                                                                {"payment_id": p["id"]}))
    tool_data(call_tool(client, tok, "revert_payment", {"payment_id": p["id"], "reason": "wrong customer"}))
    assert appmod.db.session.get(appmod.Payment, p["id"]).reverted_at is not None


def test_refund(client):
    _, tok, cid = _setup(client)
    tool_data(call_tool(client, tok, "receive_payment", {"customer_id": cid, "amount": 10}))
    p = _paid_payment(client, tok, cid)
    d = tool_data(call_tool(client, tok, "refund_payment", {"payment_id": p["id"], "amount": 4, "reason": "outage"}))
    assert "result" in d
    msg = tool_error_text(call_tool(client, tok, "refund_payment", {"payment_id": p["id"], "amount": 50,
                                                                    "reason": "too much"}))
    assert "cannot exceed" in msg


def test_collector_collects_and_admin_confirms(client):
    _, tok, cid = _setup(client)
    col = auth_headers(client, username="wm_col", role="collector")
    tok_col, _, _ = connect(client, col)
    d = tool_data(call_tool(client, tok_col, "receive_payment", {"customer_id": cid, "amount": 10}))
    assert "awaiting confirmation" in d["result"]
    assert "Only finance or admin" in tool_error_text(call_tool(
        client, tok_col, "receive_payment", {"customer_id": cid, "amount": 10, "action": "pay"}))
    d = tool_data(call_tool(client, tok, "confirm_collected", {"customer_id": cid}))
    assert "Confirmed" in d["result"]


def test_amount_validated_before_call(client):
    import app as appmod
    _, tok, cid = _setup(client)
    n = appmod.Payment.query.count()
    assert "at most 2 decimals" in tool_error_text(call_tool(client, tok, "receive_payment",
                                                             {"customer_id": cid, "amount": 1.234}))
    assert appmod.Payment.query.count() == n
