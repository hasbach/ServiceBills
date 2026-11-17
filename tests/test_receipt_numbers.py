"""Receipt numbers (roadmap 1B, step 17): every generated slip gets a number,
sequential per business and never reused, and a slip cannot be deleted and
regenerated with different figures."""
from tests.conftest import make_tenant
from tests.test_receive_payment import _customer, _bill
from app import app as flask_app, db, GeneratedReceipt, ReceiptCounter, Payment


def _generate(client, hdr, year=2026, month=9):
    r = client.post("/api/receipts/generate", headers=hdr, json={"year": year, "month": month})
    assert r.status_code == 200, r.get_data(as_text=True)


def _numbers(client, hdr):
    r = client.get("/api/receipts", headers=hdr)
    assert r.status_code == 200
    return sorted((x["receipt_number"], x["customer_name"]) for x in r.get_json())


def test_slips_are_numbered_in_order(app, client):
    a = make_tenant(client, "B", "rn1")
    c1 = _customer(client, a, name="Ali")
    c2 = _customer(client, a, name="Bassam")
    _bill(client, a, c1, 25, "2026-09-01")
    _bill(client, a, c2, 25, "2026-09-05")
    _generate(client, a)
    assert _numbers(client, a) == [(1, "Ali"), (2, "Bassam")]


def test_generating_again_numbers_only_the_new_slips(app, client):
    a = make_tenant(client, "B", "rn2")
    c1 = _customer(client, a, name="Ali")
    _bill(client, a, c1, 25, "2026-09-01")
    _generate(client, a)
    c2 = _customer(client, a, name="Bassam")
    _bill(client, a, c2, 25, "2026-09-10")
    _generate(client, a)
    _generate(client, a, month=10)  # nothing new: no number used
    assert _numbers(client, a) == [(1, "Ali"), (2, "Bassam")]


def test_each_business_counts_on_its_own(app, client):
    a = make_tenant(client, "Biz A", "rn3")
    _bill(client, a, _customer(client, a, name="Ali"), 25, "2026-09-01")
    _generate(client, a)
    b = make_tenant(client, "Biz B", "rn4")
    _bill(client, b, _customer(client, b, name="Rima"), 25, "2026-09-01")
    _generate(client, b)
    assert _numbers(client, a) == [(1, "Ali")]
    assert _numbers(client, b) == [(1, "Rima")]


def test_a_slip_cannot_be_deleted(app, client):
    a = make_tenant(client, "B", "rn5")
    _bill(client, a, _customer(client, a), 25, "2026-09-01")
    _generate(client, a)
    rid = client.get("/api/receipts", headers=a).get_json()[0]["id"]
    r = client.delete(f"/api/receipts/{rid}", headers=a)
    assert r.status_code == 409
    with flask_app.app_context():
        assert db.session.get(GeneratedReceipt, rid) is not None


def test_a_number_is_not_reused_after_its_bill_is_deleted(app, client):
    a = make_tenant(client, "B", "rn6")
    c1 = _customer(client, a, name="Ali")
    pid = _bill(client, a, c1, 25, "2026-09-01")
    _generate(client, a)
    r = client.delete(f"/api/payments/{pid}", headers=a, json={"reason": "wrong bill"})
    assert r.status_code == 200, r.get_data(as_text=True)
    c2 = _customer(client, a, name="Bassam")
    _bill(client, a, c2, 25, "2026-09-10")
    _generate(client, a)
    assert _numbers(client, a) == [(2, "Bassam")]


def test_counter_starts_after_slips_numbered_before_it_existed(app, client):
    """A business whose slips were numbered by the migration but has no
    counter row yet continues from its highest number."""
    a = make_tenant(client, "B", "rn7")
    c1 = _customer(client, a, name="Ali")
    _bill(client, a, c1, 25, "2026-09-01")
    _generate(client, a)
    with flask_app.app_context():
        ReceiptCounter.query.delete()
        GeneratedReceipt.query.update({GeneratedReceipt.receipt_number: 41})
        db.session.commit()
    _bill(client, a, _customer(client, a, name="Bassam"), 25, "2026-09-10")
    _generate(client, a)
    assert _numbers(client, a) == [(41, "Ali"), (42, "Bassam")]
