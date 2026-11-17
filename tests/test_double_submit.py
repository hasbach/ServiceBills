"""Double-submit and concurrency protection (roadmap 1B, step 16): the same
money request sent twice in a few seconds is recorded once, and two changes
to one balance made at the same time both count."""
from datetime import datetime, timedelta

from tests.conftest import make_tenant
from tests.test_receive_payment import _customer
from tests.test_cash_entries_transfers import _entry
from app import app as flask_app, db, Customer, CashEntry, SubmitGuard


def test_the_same_cash_entry_sent_twice_is_recorded_once(app, client):
    a = make_tenant(client, "B", "ds1")
    first = _entry(client, a, amount=70)
    second = _entry(client, a, amount=70)
    assert first.status_code in (200, 201), first.get_data(as_text=True)
    assert second.status_code == 409
    assert second.get_json()["duplicate"] is True
    with flask_app.app_context():
        assert CashEntry.query.count() == 1


def test_a_different_amount_is_not_a_duplicate(app, client):
    a = make_tenant(client, "B", "ds2")
    assert _entry(client, a, amount=70).status_code in (200, 201)
    assert _entry(client, a, amount=71).status_code in (200, 201)


def test_another_user_sending_the_same_thing_is_not_a_duplicate(app, client):
    a = make_tenant(client, "B", "ds3")
    b = make_tenant(client, "C", "ds3b")
    assert _entry(client, a, amount=70).status_code in (200, 201)
    assert _entry(client, b, amount=70).status_code in (200, 201)


def test_the_same_request_is_accepted_again_after_the_window(app, client):
    a = make_tenant(client, "B", "ds4")
    assert _entry(client, a, amount=70).status_code in (200, 201)
    with flask_app.app_context():
        for g in SubmitGuard.query.all():
            g.created_at = datetime.utcnow() - timedelta(seconds=11)
        db.session.commit()
    assert _entry(client, a, amount=70).status_code in (200, 201)


def test_a_refused_request_can_be_sent_again_at_once(app, client):
    a = make_tenant(client, "B", "ds5")
    bad = _entry(client, a, amount=-5)
    assert bad.status_code == 400
    again = _entry(client, a, amount=-5)
    assert again.status_code == 400, "an error must give the claim back, not turn into 409"


def test_a_double_clicked_payment_charges_the_customer_once(app, client):
    a = make_tenant(client, "B", "ds6")
    cid = _customer(client, a)
    body = {"customer_id": cid, "amount": 30, "reason": "Advance", "pre_payment": True}
    assert client.post("/api/payments", headers=a, json=body).status_code == 201
    assert client.post("/api/payments", headers=a, json=body).status_code == 409
    with flask_app.app_context():
        assert round(float(db.session.get(Customer, cid).balance), 2) == 30


def test_two_changes_to_one_balance_at_the_same_time_both_count(app, client):
    """Two sessions read the same balance, each adds its own amount, the
    second commits after the first. Before step 16 the second overwrote the
    first (100 + 20 = 120, not 150)."""
    a = make_tenant(client, "B", "ds7")
    cid = _customer(client, a)
    with flask_app.app_context():
        db.session.get(Customer, cid).balance = 100
        db.session.commit()

        first = db.session.get(Customer, cid)
        first_read = float(first.balance)
        # A second request, on its own connection, reads the same 100 and adds 30.
        from sqlalchemy.orm import Session
        with Session(db.engine) as s2:
            c2 = s2.get(Customer, cid)
            assert float(c2.balance) == 100
            c2.balance = float(c2.balance) + 30
            s2.commit()
        # The first request still holds 100 and adds 20.
        first.balance = first_read + 20
        db.session.commit()
        assert round(float(db.session.get(Customer, cid).balance), 2) == 150


def test_paying_again_after_a_revert_is_not_a_duplicate(app, client):
    """Refund, revert the refund, refund again: the revert in between shows
    the repeat is deliberate."""
    from tests.test_refund_limit_and_lock_guards import _paid_bill
    from tests.test_refund_channel import _refund
    a = make_tenant(client, "B", "ds8")
    cust, pid = _paid_bill(client, a)
    refund_id = _refund(client, a, pid)["refund_payment_id"]
    assert client.put(f"/api/payments/{refund_id}/revert", headers=a, json={"reason": "x"}).status_code == 200
    _refund(client, a, pid)  # asserts success itself
