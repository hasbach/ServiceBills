"""When credit that already paid bills is taken back (the prepayment is
reverted, deleted or refunded), those bills are open again. Before, they
stayed "paid from credit" while the customer owed the money with no open
bill to collect it against. Found by local testing of the 1A fixes."""
from tests.conftest import make_tenant
from tests.test_receive_payment import _customer, _bill, _prepay, _state
from app import app as flask_app, Payment


def _prepayment_id(cid):
    with flask_app.app_context():
        return Payment.query.filter_by(customer_id=cid, pre_payment=True).one().id


def _setup(client, username, bills=((25, "2026-09-01"),), prepay=50):
    a = make_tenant(client, "B", username)
    cid = _customer(client, a)
    _prepay(client, a, cid, prepay)
    for amount, day in bills:
        _bill(client, a, cid, amount, day)
    return a, cid


def test_reverting_the_prepayment_reopens_the_bill_it_paid(app, client):
    a, cid = _setup(client, "cw1")
    assert _state(cid) == (25, [(25, True)])

    r = client.put(f"/api/payments/{_prepayment_id(cid)}/revert", headers=a, json={"reason": "bounced"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _state(cid) == (-25, [(25, False)])
    with flask_app.app_context():
        assert Payment.query.filter_by(customer_id=cid, pre_payment=False).one().settled_from_credit is False


def test_deleting_the_prepayment_reopens_the_bill_it_paid(app, client):
    a, cid = _setup(client, "cw2")
    assert client.delete(f"/api/payments/{_prepayment_id(cid)}", headers=a).status_code == 200
    assert _state(cid) == (-25, [(25, False)])


def test_bulk_deleting_the_prepayment_reopens_the_bill_it_paid(app, client):
    a, cid = _setup(client, "cw3")
    client.post("/api/payments/bulk_delete", headers=a, json={"payment_ids": [_prepayment_id(cid)]})
    assert _state(cid) == (-25, [(25, False)])


def test_partly_refunding_the_prepayment_reopens_only_what_is_no_longer_covered(app, client):
    a, cid = _setup(client, "cw4", bills=((25, "2026-08-01"), (25, "2026-09-01")))
    assert _state(cid) == (0, [(25, True), (25, True)])

    r = client.post(f"/api/payments/{_prepayment_id(cid)}/refund", headers=a, json={"reason": "moved", "amount": 30})
    assert r.status_code == 201, r.get_data(as_text=True)
    # 20 of credit is left: it still covers 20 of the oldest bill; 30 is owed again.
    balance, bills = _state(cid)
    bills = [b for b in bills if b != (30, True)]        # (the refund row itself)
    assert balance == -30
    assert bills == [(20, True), (5, False), (25, False)]


def test_credit_that_still_covers_the_bills_leaves_them_paid(app, client):
    a, cid = _setup(client, "cw5", prepay=100)
    r = client.post(f"/api/payments/{_prepayment_id(cid)}/refund", headers=a, json={"reason": "part", "amount": 40})
    assert r.status_code == 201, r.get_data(as_text=True)
    balance, bills = _state(cid)
    assert balance == 35 and (25, True) in bills and not any(not paid for _, paid in bills)
