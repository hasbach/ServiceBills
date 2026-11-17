"""Books (roadmap 1C), task 4: balance changes with no record behind them,
deleting a customer, and the `unexplained` safety net.

Spec: docs/superpowers/specs/2026-11-17-ledger-design.md ("Deleting a
customer", "Balance changes with no record behind them")."""
from datetime import datetime

import pytest

import app as appmod
from tests.conftest import make_tenant
from tests.test_receive_payment import _customer, _bill, _prepay
from tests.test_expenses_payroll_supplier import _make_supplier
from tests.test_profit_includes_resellers_and_upstream import _reseller
from tests.test_customer_import import _commit, _row, _plan, _tenant_id
from tests.test_ledger_posting import (
    _start_books_for_test, _books_party_balance, _stored_balance, _account, _assert_balanced,
    _assert_customer_matches, _started_customer, _pay, _tid)
from app import (
    app as flask_app, db, Customer, Payment, Supplier, Reseller, UpstreamProvider,
    JournalEntry, JournalLine)


def _kinds(tid):
    with flask_app.app_context():
        return sorted(e.kind for e in JournalEntry.query.filter_by(tenant_id=tid))


def _entries(tid, kind):
    with flask_app.app_context():
        return [(e.memo, e.entry_date) for e in JournalEntry.query.filter_by(tenant_id=tid, kind=kind)]


@pytest.fixture
def no_whatsapp(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('import must not send WhatsApp')
    monkeypatch.setattr(appmod, 'send_whatsapp_message', boom)
    monkeypatch.setattr(appmod, '_maybe_create_customer_payment_link', boom)


# --- balance changes with no record behind them -------------------------------

def test_hand_typed_balance_posts_correction(app, client):
    a = make_tenant(client, "Books", "lc1")
    tid = _tid()
    _start_books_for_test(tid)
    sid = _make_supplier(client, a)
    r = client.put(f"/api/suppliers/{sid}", headers=a,
                   json={"balance": 100, "balance_reason": "found an old invoice"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _kinds(tid) == ["correction"]
    memo, _ = _entries(tid, "correction")[0]
    assert "found an old invoice" in memo
    assert _account(tid, "suppliers") == -100
    assert _account(tid, "corrections") == 100
    _assert_balanced(tid)
    assert _books_party_balance("supplier", sid) == _stored_balance(Supplier, sid) == 100


def test_new_reseller_opening_balance_posts_correction(app, client):
    a = make_tenant(client, "Books", "lc2")
    tid = _tid()
    _start_books_for_test(tid)
    r = client.post("/api/resellers", headers=a,
                    json={"name": "Rami", "phone": "70000000", "type": "type1", "balance": 60})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    rid = (r.get_json().get("reseller") or r.get_json())["id"]
    assert _kinds(tid) == ["correction"]
    assert _account(tid, "resellers") == 60
    _assert_balanced(tid)
    assert _books_party_balance("reseller", rid) == _stored_balance(Reseller, rid) == 60


def test_new_upstream_opening_balance_posts_correction(app, client):
    a = make_tenant(client, "Books", "lc3")
    tid = _tid()
    _start_books_for_test(tid)
    r = client.post("/api/upstream-providers", headers=a, json={"name": "Up", "balance": 80})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    body = r.get_json()
    pid = (body.get("provider") or body)["id"]
    assert _kinds(tid) == ["correction"]
    assert _account(tid, "upstream") == 80
    _assert_balanced(tid)
    assert _books_party_balance("upstream", pid) == _stored_balance(UpstreamProvider, pid) == 80


def test_credit_review_restore_posts_correction(app, client):
    a, tid, cid = _started_customer(client, "lc4")
    with flask_app.app_context():  # a row as the OLD settlement left it
        db.session.add(Payment(tenant_id=tid, customer_id=cid, amount=25, paid=True,
                               paid_at=datetime.utcnow(), pre_payment=False, date=datetime(2026, 7, 1)))
        db.session.commit()
    before = _kinds(tid)
    r = client.post(f"/api/customers/{cid}/credit-review", headers=a, json={"action": "restore"})
    assert r.status_code == 200, r.get_data(as_text=True)
    after = _kinds(tid)
    assert "unexplained" not in after
    assert after.count("correction") == before.count("correction") + 1
    assert _stored_balance(Customer, cid) == 25
    _assert_customer_matches(tid, cid)


def test_import_opening_balance_posts(client, no_whatsapp):
    h = make_tenant(client, "Books", "lc5")
    _plan(client, h)
    tid = _tenant_id(client, h)
    _start_books_for_test(tid)
    assert _commit(client, h, [_row(opening_balance=30)]).status_code == 200
    with flask_app.app_context():
        cid = Customer.query.filter_by(tenant_id=tid).one().id
    assert _kinds(tid) == ["event"]           # the bill, no correction
    assert _account(tid, "customers") == 30
    assert _account(tid, "income_subscriptions") == -30
    _assert_customer_matches(tid, cid)


# --- deleting a customer ---------------------------------------------------------

def test_customer_delete_keeps_money_and_writes_off_balance(app, client):
    a, tid, cid = _started_customer(client, "lc6")
    pid = _bill(client, a, cid, 40)
    _pay(client, a, pid)                      # 40 cash
    _bill(client, a, cid, 25)                 # open bill
    _prepay(client, a, cid, 10)               # 10 cash more
    assert _account(tid, "cash") == 50
    income = _account(tid, "income_subscriptions")
    owed = _books_party_balance("customer", cid)
    assert owed == _stored_balance(Customer, cid) == -15
    corrections_before = _kinds(tid).count("correction")

    r = client.delete(f"/api/customers/{cid}", headers=a)
    assert r.status_code == 200, r.get_data(as_text=True)

    assert _account(tid, "cash") == 50
    assert _account(tid, "income_subscriptions") == income
    assert _books_party_balance("customer", cid) == 0
    assert _account(tid, "corrections") == 15     # what they still owed, written off
    kinds = _kinds(tid)
    assert "unexplained" not in kinds
    assert kinds.count("correction") == corrections_before + 1
    assert any(m == "Customer deleted: Cust" for m, _ in _entries(tid, "correction"))
    _assert_balanced(tid)


# --- the safety net --------------------------------------------------------------

def test_unmapped_balance_change_is_posted_unexplained(app, client):
    a, tid, cid = _started_customer(client, "lc7")
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        c.balance -= 7
        db.session.commit()
    assert _kinds(tid) == ["unexplained"]
    memo, _ = _entries(tid, "unexplained")[0]
    assert memo == "Unexplained balance change (background)"
    assert _account(tid, "customers") == 7
    assert _account(tid, "corrections") == -7
    _assert_customer_matches(tid, cid)


def test_unloaded_balance_overwrite_is_still_seen(app, client):
    a, tid, cid = _started_customer(client, "lc8")
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        db.session.expire(c, ["balance"])
        c.balance = 12                     # old value never loaded
        db.session.commit()
    assert _kinds(tid) == ["unexplained"]
    _assert_customer_matches(tid, cid)


def test_mapped_changes_post_nothing_unexplained(app, client):
    a, tid, cid = _started_customer(client, "lc9")
    pid = _bill(client, a, cid, 25)
    _pay(client, a, pid, partial_payment=True, partial_amount=10)
    _prepay(client, a, cid, 5)
    assert "unexplained" not in _kinds(tid)
    _assert_customer_matches(tid, cid)


def test_tenant_without_books_gets_no_unexplained(app, client):
    a = make_tenant(client, "Books", "lc10")
    cid = _customer(client, a)
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        c.balance -= 7
        db.session.commit()
        assert JournalEntry.query.count() == 0


def test_rollback_drops_captured_deltas(app, client):
    a, tid, cid = _started_customer(client, "lc11")
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        c.balance -= 7
        db.session.flush()
        assert db.session.info["ledger_party_delta"][(tid, "customer", cid)] == -7
        db.session.rollback()
        assert not db.session.info.get("ledger_party_delta")
        db.session.commit()
    assert _kinds(tid) == []


def test_savepoint_rollback_restores_earlier_party_totals(app, client):
    a, tid, cid = _started_customer(client, "lc12")
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        c.balance -= 7                          # before the savepoint: stays
        db.session.flush()
        sp = db.session.begin_nested()
        db.session.add(Payment(tenant_id=tid, customer_id=cid, amount=25, reason="x",
                               date=datetime(2026, 9, 1)))
        c.balance -= 25
        db.session.flush()
        sp.rollback()
        assert db.session.info["ledger_party_delta"] == {(tid, "customer", cid): -7}
        assert not db.session.info.get("ledger_party_posted")
        db.session.commit()
    assert _kinds(tid) == ["unexplained"]
    _assert_customer_matches(tid, cid)


def test_released_savepoint_keeps_party_totals(app, client):
    a, tid, cid = _started_customer(client, "lc13")
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        with db.session.begin_nested():
            db.session.add(Payment(tenant_id=tid, customer_id=cid, amount=25, reason="x",
                                   date=datetime(2026, 9, 1)))
            c.balance -= 25
        db.session.commit()
    assert _kinds(tid) == ["event"]
    _assert_customer_matches(tid, cid)


def test_safety_net_failure_does_not_block_commit(app, client, monkeypatch):
    a, tid, cid = _started_customer(client, "lc14")

    def boom(*args, **kwargs):
        raise RuntimeError("books down")
    monkeypatch.setattr(appmod, "_post_line_groups", boom)
    with flask_app.app_context():
        c = db.session.get(Customer, cid)
        c.balance -= 7
        db.session.commit()
    assert _stored_balance(Customer, cid) == -7
    with flask_app.app_context():
        assert appmod.LedgerProblem.query.filter_by(tenant_id=tid).count() >= 1
