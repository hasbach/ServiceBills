"""Finance roadmap 1A, fixes 3 and 7: a payment can never be refunded for more
than was received, only real money can be refunded, an already-paid payment
cannot be "collected" again, and a payment that was reverted and then paid
again is still protected by the day lock."""
from tests.conftest import make_tenant
from tests.test_daily_cash_report import _make_plan, _make_customer, _unpaid_payment_id
from tests.test_day_close import _today, _set_opening, _close, _blocked
from tests.test_receive_payment import _customer, _bill, _prepay
from tests.test_refund_channel import _refund, _balance
from app import app as flask_app, db, Payment


def _paid_bill(client, hdr, price=40):
    cust = _make_customer(client, hdr, _make_plan(client, hdr, price=price))
    pid = _unpaid_payment_id(client, hdr, cust)
    r = client.put(f"/api/payments/{pid}/mark_paid", headers=hdr, json={"action": "pay"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return cust, pid


def _try_refund(client, hdr, pid, **body):
    body.setdefault("reason", "test refund")
    return client.post(f"/api/payments/{pid}/refund", headers=hdr, json=body)


# --------------------------------------------------------------- refund limit

def test_full_refund_cannot_be_issued_twice(app, client):
    a = make_tenant(client, "Biz A", "rl1")
    cust, pid = _paid_bill(client, a)
    _refund(client, a, pid)
    balance = _balance(cust)

    r = _try_refund(client, a, pid)
    assert r.status_code == 400, r.get_data(as_text=True)
    assert "already been refunded" in r.get_json()["message"]
    assert _balance(cust) == balance


def test_partial_refunds_are_capped_at_the_original_amount(app, client):
    a = make_tenant(client, "Biz A", "rl2")
    cust, pid = _paid_bill(client, a, price=40)
    _refund(client, a, pid, amount=25)

    r = _try_refund(client, a, pid, amount=20)
    assert r.status_code == 400, r.get_data(as_text=True)
    assert "15" in r.get_json()["message"]

    _refund(client, a, pid, amount=15)  # exactly what is left
    assert _try_refund(client, a, pid, amount=1).status_code == 400


def test_reverting_a_refund_makes_the_amount_refundable_again(app, client):
    a = make_tenant(client, "Biz A", "rl3")
    cust, pid = _paid_bill(client, a)
    refund_id = _refund(client, a, pid)["refund_payment_id"]
    r = client.put(f"/api/payments/{refund_id}/revert", headers=a, json={"reason": "wrong customer"})
    assert r.status_code == 200, r.get_data(as_text=True)

    _refund(client, a, pid)


def test_a_refund_row_cannot_be_refunded(app, client):
    a = make_tenant(client, "Biz A", "rl4")
    cust, pid = _paid_bill(client, a)
    refund_id = _refund(client, a, pid)["refund_payment_id"]

    assert _try_refund(client, a, refund_id).status_code == 400


def test_a_gratis_bill_cannot_be_refunded(app, client):
    a = make_tenant(client, "Biz A", "rl5")
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    pid = _unpaid_payment_id(client, a, cust)
    assert client.put(f"/api/payments/{pid}/mark_gratis", headers=a, json={}).status_code == 200
    balance = _balance(cust)

    assert _try_refund(client, a, pid).status_code == 400
    assert _balance(cust) == balance


def test_a_bill_paid_from_credit_cannot_be_refunded(app, client):
    a = make_tenant(client, "Biz A", "rl6")
    cid = _customer(client, a)
    _prepay(client, a, cid, 50)
    bill = _bill(client, a, cid, 25)

    assert _try_refund(client, a, bill).status_code == 400
    assert _balance(cid) == 25


# ------------------------------------------------------- collect / day lock

def test_collect_is_refused_on_an_already_paid_payment(app, client):
    a = make_tenant(client, "Biz A", "rl7")
    cust, pid = _paid_bill(client, a)
    with flask_app.app_context():
        before = db.session.get(Payment, pid)
        paid_at, collected_at = before.paid_at, before.collected_at

    r = client.put(f"/api/payments/{pid}/mark_paid", headers=a,
                   json={"action": "collect", "method": "whish_transfer", "reference": "X1"})
    assert r.status_code == 400, r.get_data(as_text=True)
    with flask_app.app_context():
        after = db.session.get(Payment, pid)
        assert after.paid_at == paid_at and after.collected_at == collected_at
        assert after.collected_via is None


def test_day_lock_covers_a_payment_reverted_and_paid_again(app, client):
    a = make_tenant(client, "Biz A", "rl8")
    cust, pid = _paid_bill(client, a, price=40)
    assert client.put(f"/api/payments/{pid}/revert", headers=a,
                      json={"reason": "wrong amount"}).status_code == 200
    assert client.put(f"/api/payments/{pid}/mark_paid", headers=a,
                      json={"action": "pay"}).status_code == 200

    today = _today()
    _set_opening(client, a, today)
    r = _close(client, a, today, 140, 20)
    assert r.status_code in (200, 201), r.get_data(as_text=True)

    _blocked(client.delete(f"/api/payments/{pid}", headers=a), today)
    _blocked(client.post("/api/payments/bulk_delete", headers=a, json={"payment_ids": [pid]}), today)
    _blocked(client.put(f"/api/payments/{pid}/revert", headers=a, json={"reason": "again"}), today)
    _blocked(client.put(f"/api/payments/{pid}/method", headers=a, json={"method": "whish_transfer"}), today)
