"""Deleting or reverting payment rows must leave the customer's balance equal
to what the remaining rows explain (finance roadmap 1A, fixes 1 and 2).

Balance model: creating a bill debits the balance; paying it (cash or gratis)
credits it back; a prepayment credits it; a bill settled from existing credit
does NOT touch it (the prepayment behind it already did)."""
from tests.conftest import make_tenant
from tests.test_receive_payment import _customer, _bill, _prepay, _state
from app import app as flask_app, Payment


def _rows(cid, **filters):
    with flask_app.app_context():
        return [p.id for p in Payment.query.filter_by(customer_id=cid, **filters).order_by(Payment.id).all()]


def test_delete_credit_settled_bill_gives_the_credit_back(app, client):
    a = make_tenant(client, "B", "cs1")
    cid = _customer(client, a)
    _prepay(client, a, cid, 50)
    bill = _bill(client, a, cid, 25)
    assert _state(cid) == (25, [(25, True)])

    assert client.delete(f"/api/payments/{bill}", headers=a).status_code == 200
    assert _state(cid) == (50, [])


def test_bulk_delete_credit_settled_bill_gives_the_credit_back(app, client):
    a = make_tenant(client, "B", "cs2")
    cid = _customer(client, a)
    _prepay(client, a, cid, 50)
    bill = _bill(client, a, cid, 25)

    r = client.post("/api/payments/bulk_delete", headers=a, json={"payment_ids": [bill]})
    assert r.get_json()["succeeded"] == [bill]
    assert _state(cid) == (50, [])


def test_revert_of_credit_settled_bill_is_refused(app, client):
    """No money was collected for it, so there is nothing to revert; the old
    behaviour took the amount off the balance a second time."""
    a = make_tenant(client, "B", "cs3")
    cid = _customer(client, a)
    _prepay(client, a, cid, 50)
    bill = _bill(client, a, cid, 25)

    r = client.put(f"/api/payments/{bill}/revert", headers=a, json={"reason": "mistake"})
    assert r.status_code == 400, r.get_data(as_text=True)
    assert _state(cid) == (25, [(25, True)])


def test_revert_then_delete_prepayment_leaves_no_phantom_credit(app, client):
    a = make_tenant(client, "B", "cs4")
    cid = _customer(client, a)
    _prepay(client, a, cid, 50)
    prepay = _rows(cid, pre_payment=True)[0]

    r = client.put(f"/api/payments/{prepay}/revert", headers=a, json={"reason": "typed twice"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _state(cid)[0] == 0

    assert client.delete(f"/api/payments/{prepay}", headers=a).status_code == 200
    assert _state(cid)[0] == 0


def test_bulk_delete_reverted_prepayment_leaves_no_phantom_credit(app, client):
    a = make_tenant(client, "B", "cs5")
    cid = _customer(client, a)
    _prepay(client, a, cid, 50)
    prepay = _rows(cid, pre_payment=True)[0]
    client.put(f"/api/payments/{prepay}/revert", headers=a, json={"reason": "typed twice"})

    client.post("/api/payments/bulk_delete", headers=a, json={"payment_ids": [prepay]})
    assert _state(cid)[0] == 0


def test_delete_paid_prepayment_removes_its_credit(app, client):
    a = make_tenant(client, "B", "cs6")
    cid = _customer(client, a)
    _prepay(client, a, cid, 50)
    prepay = _rows(cid, pre_payment=True)[0]

    assert client.delete(f"/api/payments/{prepay}", headers=a).status_code == 200
    assert _state(cid)[0] == 0


def test_delete_unpaid_bill_removes_the_debt(app, client):
    a = make_tenant(client, "B", "cs7")
    cid = _customer(client, a)
    bill = _bill(client, a, cid, 25)
    assert _state(cid) == (-25, [(25, False)])

    assert client.delete(f"/api/payments/{bill}", headers=a).status_code == 200
    assert _state(cid) == (0, [])


def test_delete_cash_paid_bill_leaves_no_debt_without_a_bill(app, client):
    """The charge and the money that paid it go together: the customer ends
    square, not owing an amount no bill explains."""
    a = make_tenant(client, "B", "cs8")
    cid = _customer(client, a)
    bill = _bill(client, a, cid, 25)
    r = client.put(f"/api/payments/{bill}/mark_paid", headers=a, json={"action": "pay"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _state(cid) == (0, [(25, True)])

    assert client.delete(f"/api/payments/{bill}", headers=a).status_code == 200
    assert _state(cid) == (0, [])
