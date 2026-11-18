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


def test_new_reseller_on_tenant_without_books_does_not_flush_early(app, client, monkeypatch):
    a = make_tenant(client, "Books", "lc2b")
    tid = _tid()
    # The helper decides "no books" before touching the session at all.
    with flask_app.app_context():
        r = Reseller(tenant_id=tid, name="Nobooks", phone="70000001")
        flushed = []
        monkeypatch.setattr(db.session, "flush", lambda *a, **k: flushed.append(1))
        appmod._ledger_note_manual(r, "reseller", 60, "opening")
        monkeypatch.undo()
        assert flushed == []
    # And the route itself still works and posts nothing.
    resp = client.post("/api/resellers", headers=a,
                       json={"name": "Rami", "phone": "70000000", "type": "type1", "balance": 60})
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    with flask_app.app_context():
        assert JournalEntry.query.filter_by(tenant_id=tid).count() == 0


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


# --- starting the books (task 5) -------------------------------------------------
# Spec: "Starting the books (cut-over)", "Fixing records from before the books
# started".

import json as _json
from datetime import date as _date, timedelta as _timedelta, timezone as _timezone

from tests.test_ledger_posting import _today, _account_by_day
from tests.test_expenses_payroll_supplier import _make_category, _make_expense, _make_employee
from sqlalchemy.exc import IntegrityError
from app import LedgerState, LedgerStartLine, BusinessSettings, Employee, DayClose, Tenant


def _start(client, hdr):
    r = client.post("/api/books/start", headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _opening_entries(tid):
    with flask_app.app_context():
        return JournalEntry.query.filter_by(tenant_id=tid, kind="opening").all()


def _state_check(tid):
    with flask_app.app_context():
        return _json.loads(db.session.get(LedgerState, tid).last_check or "{}")


def _set_cash_opening(client, hdr, amount, whish=None, days_ago=10):
    body = {"date": (_today() - _timedelta(days=days_ago)).isoformat(), "amount": amount}
    if whish is not None:
        body["whish_amount"] = whish
    r = client.put("/api/reports/cash-opening", headers=hdr, json=body)
    assert r.status_code == 200, r.get_data(as_text=True)


def test_start_books_opening_matches_stored_balances(app, client):
    a = make_tenant(client, "Books", "lc20")
    tid = _tid()
    owes = _customer(client, a, "Owes")
    _bill(client, a, owes, 40)                       # owes 40
    credit = _customer(client, a, "Credit")
    _prepay(client, a, credit, 15)                   # 15 credit, 15 cash today
    sid = _make_supplier(client, a)
    assert client.put(f"/api/suppliers/{sid}", headers=a, json={"balance": 70, "balance_reason": "old invoice"}).status_code == 200
    r = client.post("/api/resellers", headers=a,
                    json={"name": "Rami", "phone": "70000000", "type": "type1", "balance": 60})
    rid = (r.get_json().get("reseller") or r.get_json())["id"]
    r = client.post("/api/upstream-providers", headers=a, json={"name": "Up", "balance": 80})
    up = (r.get_json().get("provider") or r.get_json())["id"]
    emp = _make_employee(client, a)
    with flask_app.app_context():                   # salary owed, before the books exist
        db.session.get(Employee, emp).balance = 300
        db.session.commit()
    _set_cash_opening(client, a, 100, whish=20)

    state = _start(client, a)
    assert state["start_day"] == _today().isoformat()

    for pt, model, pk, want in (("customer", Customer, owes, -40), ("customer", Customer, credit, 15),
                                ("supplier", Supplier, sid, 70), ("reseller", Reseller, rid, 60),
                                ("upstream", UpstreamProvider, up, 80),
                                ("employee", Employee, emp, 300)):
        assert _stored_balance(model, pk) == want
        assert _books_party_balance(pt, pk) == want
    # Cash/Whish = the Cash Flow running balance at the start: the opening
    # plus what already moved (the 15 prepay collected today).
    assert _account(tid, "cash") == 115
    assert _account(tid, "whish") == 20
    entries = _opening_entries(tid)
    assert len(entries) == 1 and entries[0].entry_date == _today()
    assert state["opening_entry_id"] == entries[0].id
    _assert_balanced(tid)
    assert _kinds(tid) == ["opening"]              # nothing unexplained
    assert not _state_check(tid).get("cash_opening_unknown")
    with flask_app.app_context():
        # The starting point: one row per line, each with its own day.
        rows = LedgerStartLine.query.filter_by(tenant_id=tid, source_type="payment").all()
        assert sorted((r.account_key, r.amount, r.day) for r in rows) == sorted([
            ("customers", 40, _date(2026, 9, 1)), ("income_subscriptions", -40, _date(2026, 9, 1)),
            ("cash", 15, _today()), ("customers", -15, _today())])


def test_start_books_is_idempotent(app, client):
    a = make_tenant(client, "Books", "lc21")
    tid = _tid()
    cid = _customer(client, a)
    _bill(client, a, cid, 25)
    first = _start(client, a)
    with flask_app.app_context():
        lines = LedgerStartLine.query.filter_by(tenant_id=tid).count()
        accounts = appmod.LedgerAccount.query.filter_by(tenant_id=tid).count()
    second = _start(client, a)
    assert second == first
    with flask_app.app_context():
        assert appmod.start_books(tid).started_at is not None
        db.session.commit()
        assert LedgerStartLine.query.filter_by(tenant_id=tid).count() == lines
        assert appmod.LedgerAccount.query.filter_by(tenant_id=tid).count() == accounts
    assert len(_opening_entries(tid)) == 1
    _assert_customer_matches(tid, cid)


def test_start_books_admin_only(app, client):
    a = make_tenant(client, "Books", "lc22")
    tid = _tid()
    client.post("/api/users", headers=a, json={"username": "lc22fin", "password": "pw", "role": "finance"})
    tok = client.post("/api/login", json={"username": "lc22fin", "password": "pw"}).get_json()["access_token"]
    r = client.post("/api/books/start", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    with flask_app.app_context():
        assert db.session.get(LedgerState, tid) is None


def test_old_open_bill_paid_after_start_posts_only_the_receipt(app, client):
    a = make_tenant(client, "Books", "lc23")
    tid = _tid()
    cid = _customer(client, a)
    pid = _bill(client, a, cid, 25)
    _start(client, a)
    opening, income = _account(tid, "opening"), _account(tid, "income_subscriptions")
    assert (opening, income) == (-25, 0)
    _pay(client, a, pid)
    assert _account(tid, "cash") == 25
    assert _account(tid, "income_subscriptions") == 0       # not booked a second time
    assert _account(tid, "opening") == opening
    assert _kinds(tid) == ["event", "opening"]
    _assert_customer_matches(tid, cid)


def test_old_bill_deleted_after_start_moves_income_to_opening(app, client):
    a = make_tenant(client, "Books", "lc24")
    tid = _tid()
    cid = _customer(client, a)
    pid = _bill(client, a, cid, 25)
    _start(client, a)
    assert client.delete(f"/api/payments/{pid}", headers=a).status_code == 200
    assert _stored_balance(Customer, cid) == 0
    assert _account(tid, "income_subscriptions") == 0       # this period's income untouched
    assert _account(tid, "opening") == 0                    # -25 opening, +25 prior-period fix
    assert "unexplained" not in _kinds(tid)
    _assert_customer_matches(tid, cid)


def test_old_expense_edited_after_start_moves_expense_to_opening(app, client):
    a = make_tenant(client, "Books", "lc25")
    tid = _tid()
    cat = _make_category(client, a, "Tools")
    eid = _make_expense(client, a, cat, amount=30, date="2026-06-15")["id"]
    _set_cash_opening(client, a, 100, days_ago=200)
    _start(client, a)
    cash, opening = _account(tid, "cash"), _account(tid, "opening")
    r = client.put(f"/api/expenses/{eid}", headers=a, json={"amount": 40})
    assert r.status_code == 200, r.get_data(as_text=True)
    with flask_app.app_context():
        cat_id = appmod.ExpenseCategory.query.filter_by(tenant_id=tid, name="Tools").one().id
    assert _account(tid, f"expense:{cat_id}") == 0          # not this period's expense
    assert _account(tid, "opening") == opening + 10
    assert _account(tid, "cash") == cash - 10
    _assert_balanced(tid)


def test_old_bill_made_gratis_after_start_posts_discount_normally(app, client):
    a = make_tenant(client, "Books", "lc26")
    tid = _tid()
    cid = _customer(client, a)
    pid = _bill(client, a, cid, 25)
    _start(client, a)
    opening = _account(tid, "opening")
    r = client.put(f"/api/payments/{pid}/mark_gratis", headers=a, json={"note": "goodwill"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _account(tid, "discounts") == 25
    assert _account(tid, "opening") == opening
    assert _account(tid, "income_subscriptions") == 0
    _assert_customer_matches(tid, cid)


def test_cash_opening_unknown_flag_when_no_opening_set(app, client):
    a = make_tenant(client, "Books", "lc27")
    tid = _tid()
    cid = _customer(client, a)
    _prepay(client, a, cid, 15)
    _start(client, a)
    assert _account(tid, "cash") == 0
    assert _account(tid, "whish") == 0
    assert _state_check(tid)["cash_opening_unknown"] is True
    _assert_customer_matches(tid, cid)
    assert "unexplained" not in _kinds(tid)


def test_start_day_is_the_tenants_own_local_day(app, client):
    make_tenant(client, "Books", "lc28")
    tid = _tid()
    with flask_app.app_context():
        s = BusinessSettings.query.filter_by(tenant_id=tid).first()
        if s is None:
            s = BusinessSettings(tenant_id=tid, business_name="B", address="", mobile="")
            db.session.add(s)
        s.timezone = "Pacific/Kiritimati"                  # UTC+14
        db.session.commit()
        state = appmod.start_books(tid, now=datetime(2026, 11, 17, 12, 0, tzinfo=_timezone.utc))
        db.session.commit()
        assert state.start_day == _date(2026, 11, 18)


def test_ledger_nightly_starts_unstarted_tenants(app, client, monkeypatch):
    make_tenant(client, "Books A", "lc29")
    first = _tid()
    a2 = make_tenant(client, "Books B", "lc30")
    second = _tid()
    cid = _customer(client, a2)
    _bill(client, a2, cid, 25)
    _set_cash_opening(client, a2, 50)     # read with no request: as that tenant
    with flask_app.app_context():
        appmod.start_books(first)
        db.session.commit()
    checked = []
    monkeypatch.setattr(appmod, "run_books_check", lambda tid: checked.append(tid) or {})
    appmod.ledger_nightly()
    with flask_app.app_context():
        assert db.session.get(LedgerState, second) is not None
    assert first in checked and second not in checked
    _assert_customer_matches(second, cid)
    assert _account(second, "cash") == 50
    assert _kinds(second) == ["opening"]


def test_ledger_nightly_one_tenant_failing_does_not_stop_others(app, client, monkeypatch):
    make_tenant(client, "Books A", "lc31")
    bad = _tid()
    make_tenant(client, "Books B", "lc32")
    good = _tid()
    real = appmod.start_books

    def flaky(tid, now=None):
        if tid == bad:
            raise RuntimeError("boom")
        return real(tid, now)
    monkeypatch.setattr(appmod, "start_books", flaky)
    appmod.ledger_nightly()
    with flask_app.app_context():
        assert db.session.get(LedgerState, bad) is None
        assert db.session.get(LedgerState, good) is not None


def test_ledger_nightly_is_scheduled(app):
    assert appmod.SCHEDULE["ledger_nightly"] == "0 3 * * *"
    assert "ledger_nightly" in appmod._SCHEDULED_JOBS
    assert "ledger_nightly" in appmod.onprem_schedule()


# --- fix round 1 ---------------------------------------------------------------

def test_future_dated_money_is_in_the_cash_opening(app, client):
    """A record that exists at the start but moves money on a later day is in
    the starting point (never posted); the Cash Flow page counts it on its own
    day, so the opening must already include it."""
    a = make_tenant(client, "Books", "lc40")
    tid = _tid()
    cat = _make_category(client, a, "Tools")
    tomorrow = _today() + _timedelta(days=1)
    _make_expense(client, a, cat, amount=30, date=tomorrow.isoformat())
    _set_cash_opening(client, a, 100)
    _start(client, a)
    assert _account(tid, "cash") == 70
    with flask_app.app_context():
        settings = BusinessSettings.query.filter_by(tenant_id=tid).first()
        with appmod._acting_as_tenant(tid):
            assert appmod._cash_flow_for_day(tomorrow, settings)["cash_end"] == 70
    _assert_balanced(tid)


def test_old_record_with_no_lines_gets_a_marker_and_its_edit_goes_to_opening(app, client):
    a = make_tenant(client, "Books", "lc41")
    tid = _tid()
    old = _today() - _timedelta(days=30)
    with flask_app.app_context():
        dc = DayClose(tenant_id=tid, day=old, expected_cash=100, counted_cash=100,
                      expected_whish=0, counted_whish=0)
        db.session.add(dc)
        db.session.commit()
        dcid = dc.id
    _start(client, a)
    with flask_app.app_context():
        rows = LedgerStartLine.query.filter_by(tenant_id=tid, source_type="day_close", source_id=dcid).all()
        assert [(r.account_key, r.amount, r.day) for r in rows] == [("_marker", 0, _today())]
    cash, opening = _account(tid, "cash"), _account(tid, "opening")
    with flask_app.app_context():
        db.session.get(DayClose, dcid).counted_cash = 90       # 10 short, found later
        db.session.commit()
    assert _account(tid, "over_short") == 0                     # not this period's
    assert _account(tid, "opening") == opening + 10
    assert _account(tid, "cash") == cash - 10
    assert "_marker" not in _account_keys(tid)
    _assert_balanced(tid)


def _account_keys(tid):
    with flask_app.app_context():
        return {k for (k,) in db.session.query(appmod.LedgerAccount.key).filter_by(tenant_id=tid)}


def test_start_books_isolation_is_repeatable_read_on_postgres_only():
    assert appmod._start_books_isolation("postgresql") == {"isolation_level": "REPEATABLE READ"}
    assert appmod._start_books_isolation("sqlite") is None


def test_start_books_requests_its_isolation_on_a_fresh_transaction(app, client, monkeypatch):
    """SQLite cannot show a concurrent commit between the reads, so this checks
    the mechanism: the options go to the session's first connection of a
    fresh transaction, and an open transaction is refused."""
    make_tenant(client, "Books", "lc42")
    tid = _tid()
    monkeypatch.setattr(appmod, "_start_books_isolation", lambda name: {"isolation_level": "SERIALIZABLE"})
    seen = []
    real = db.session.connection

    def spy(*args, **kwargs):
        seen.append(kwargs.get("execution_options"))
        return real(*args, **kwargs)
    with flask_app.app_context():
        db.session.commit()
        db.session.get(Tenant, tid)                  # a transaction is now open
        with pytest.raises(RuntimeError):
            appmod.start_books(tid)
        db.session.rollback()
        monkeypatch.setattr(db.session, "connection", spy)
        appmod.start_books(tid)
        db.session.commit()
    assert seen and seen[0] == {"isolation_level": "SERIALIZABLE"}
    with flask_app.app_context():
        assert db.session.get(LedgerState, tid) is not None


def test_start_route_returns_existing_state_on_a_concurrent_start(app, client, monkeypatch):
    a = make_tenant(client, "Books", "lc43")
    tid = _tid()
    first = _start(client, a)

    def racing(tenant_id, now=None):
        raise IntegrityError("INSERT INTO ledger_state", {}, Exception("duplicate key"))
    monkeypatch.setattr(appmod, "start_books", racing)
    assert _start(client, a) == first


def test_ledger_nightly_starts_books_through_the_fresh_transaction_guard(app, client, monkeypatch, caplog):
    """With the Postgres isolation path on (SQLite accepts SERIALIZABLE), the
    nightly job must reach start_books with no transaction open."""
    make_tenant(client, "Books A", "lc50")
    started = _tid()
    a2 = make_tenant(client, "Books B", "lc51")
    fresh = _tid()
    cid = _customer(client, a2)
    _bill(client, a2, cid, 25)
    with flask_app.app_context():
        appmod.start_books(started)
        db.session.commit()
    monkeypatch.setattr(appmod, "_start_books_isolation", lambda name: {"isolation_level": "SERIALIZABLE"})
    with caplog.at_level("ERROR"):
        appmod.ledger_nightly()
    assert "nightly run failed" not in caplog.text
    with flask_app.app_context():
        assert db.session.get(LedgerState, fresh) is not None
    _assert_customer_matches(fresh, cid)
