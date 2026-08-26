"""Phase 3: the partial-payment branch of mark_paid used to mutate balance
and create a "remaining amount" row unconditionally, unlike the full-payment
branch (which already guarded on `not payment.paid`). A repeated/duplicate
request for the same payment could double-credit balance. This confirms the
fix: calling mark_paid twice with partial_payment=True on the same payment
is a safe no-op the second time, exactly like the full-payment branch already
was before this fix."""
from tests.conftest import make_tenant


def _make_plan(client, hdr, name="Basic", price=100):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": name, "price": price, "billing_cycle": "monthly"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["plan"]["id"]


def _make_customer(client, hdr, plan_id, name="Cust"):
    r = client.post("/api/customers", headers=hdr,
                    json={"name": name, "phone": "111", "address": "addr",
                          "subscription_plan_id": plan_id,
                          "subscription_start_date": "2026-01-01"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["customer_id"]


def _unpaid_payment_id(client, hdr, customer_id):
    payments = client.get("/api/payments", headers=hdr,
                          query_string={"customer_id": customer_id}).get_json()["payments"]
    return next(p["id"] for p in payments if not p["paid"])


def test_double_submitted_partial_full_payment_does_not_double_credit(app, client):
    """partial_amount >= payment.amount takes the 'full payment via partial
    amount input' path inside the partial branch -- this is the exact path
    that had no idempotency guard before this fix."""
    a = make_tenant(client, "Biz A", "a_partial1")
    plan = _make_plan(client, a, price=100)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    balance_before = client.get("/api/payments", headers=a,
                                 query_string={"customer_id": cust_id}).get_json()

    r1 = client.put(f"/api/payments/{payment_id}/mark_paid", headers=a,
                     json={"action": "pay", "partial_payment": True, "partial_amount": 100})
    assert r1.status_code == 200, r1.get_data(as_text=True)
    balance_after_first = r1.get_json()["customer_new_balance"]

    # Simulate a double-click / retried request for the SAME payment.
    r2 = client.put(f"/api/payments/{payment_id}/mark_paid", headers=a,
                     json={"action": "pay", "partial_payment": True, "partial_amount": 100})
    assert r2.status_code == 200, r2.get_data(as_text=True)
    balance_after_second = r2.get_json()["customer_new_balance"]

    assert balance_after_second == balance_after_first, (
        f"balance changed on a repeated partial-payment request: "
        f"{balance_after_first} -> {balance_after_second}"
    )
    assert r2.get_json()["amount_received_in_this_transaction"] == 0.0


def test_double_submitted_true_partial_payment_does_not_duplicate_remaining_row(app, client):
    """partial_amount < payment.amount takes the 'actual partial payment'
    path, which also creates a new Payment row for the remaining balance --
    the second call must not create a second remaining-balance row."""
    a = make_tenant(client, "Biz A", "a_partial2")
    plan = _make_plan(client, a, price=100)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    r1 = client.put(f"/api/payments/{payment_id}/mark_paid", headers=a,
                     json={"action": "pay", "partial_payment": True, "partial_amount": 30})
    assert r1.status_code == 200, r1.get_data(as_text=True)
    balance_after_first = r1.get_json()["customer_new_balance"]

    payments_after_first = client.get("/api/payments", headers=a,
                                       query_string={"customer_id": cust_id}).get_json()["payments"]
    count_after_first = len(payments_after_first)

    r2 = client.put(f"/api/payments/{payment_id}/mark_paid", headers=a,
                     json={"action": "pay", "partial_payment": True, "partial_amount": 30})
    assert r2.status_code == 200, r2.get_data(as_text=True)
    balance_after_second = r2.get_json()["customer_new_balance"]

    payments_after_second = client.get("/api/payments", headers=a,
                                        query_string={"customer_id": cust_id}).get_json()["payments"]

    assert balance_after_second == balance_after_first
    assert len(payments_after_second) == count_after_first, (
        "a duplicate 'remaining amount' payment row was created on the repeated request"
    )
