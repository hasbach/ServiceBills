"""Books (roadmap 1C): ledger tables, the nightly books check and the
read-only Books API (task 6).

Spec: docs/superpowers/specs/2026-11-17-ledger-design.md ("Nightly check",
"What 'the books and the reports agree' means", "Books view (admin)")."""
import json
from datetime import date, datetime, timedelta

from sqlalchemy import text

import app as appmod
from tests.conftest import make_tenant
from tests.test_superadmin import _superadmin_headers
from tests.test_receive_payment import _customer, _bill, _prepay
from tests.test_expenses_payroll_supplier import (
    _make_category, _make_supplier, _make_employee, _pay_supplier, _pay_employee)
from tests.test_profit_includes_resellers_and_upstream import _reseller, _provider
from tests.test_cash_entries_transfers import _flow
from tests.test_ledger_posting import _today, _tid, _start_books_for_test, _pay
from app import app as flask_app, db, LedgerState, Supplier, JournalEntry


def test_ledger_tables_exist(app, client):
    make_tenant(client, "Books Biz", "books_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="books-biz").first().id
        acct = appmod.LedgerAccount(tenant_id=tid, key="cash", number=1000,
                                    name="Cash", type="asset")
        entry = appmod.JournalEntry(tenant_id=tid, entry_date=date(2026, 11, 17),
                                    kind="event", memo="test")
        appmod.db.session.add_all([acct, entry])
        appmod.db.session.flush()
        line = appmod.JournalLine(tenant_id=tid, entry_id=entry.id, account_id=acct.id,
                                  debit=10, credit=0, entry_date=entry.entry_date,
                                  source_type="payment", source_id=1)
        appmod.db.session.add(line)
        appmod.db.session.commit()

        assert appmod.LedgerAccount.query.filter_by(tenant_id=tid, key="cash").one().number == 1000
        assert appmod.JournalEntry.query.filter_by(tenant_id=tid).one().kind == "event"
        assert appmod.JournalLine.query.filter_by(tenant_id=tid).one().debit == 10

    assert client.delete(f"/api/admin/tenants/{tid}", headers=sa).status_code == 200
    with app.app_context():
        assert appmod.LedgerAccount.query.filter_by(tenant_id=tid).count() == 0
        assert appmod.JournalEntry.query.filter_by(tenant_id=tid).count() == 0
        assert appmod.JournalLine.query.filter_by(tenant_id=tid).count() == 0


# --- helpers ---------------------------------------------------------------------

def _check(tid):
    with flask_app.app_context():
        result = appmod.run_books_check(tid)
        db.session.commit()
        return result


def _stored_check(tid):
    with flask_app.app_context():
        state = db.session.get(LedgerState, tid)
        return json.loads(state.last_check or "{}"), state.last_check_at


def _set_opening(client, hdr, cash, whish):
    body = {"date": (_today() - timedelta(days=10)).isoformat(), "amount": cash, "whish_amount": whish}
    r = client.put("/api/reports/cash-opening", headers=hdr, json=body)
    assert r.status_code == 200, r.get_data(as_text=True)


def _start_yesterday(tid):
    """Books started yesterday: today's activity is all inside a compared
    month range (the first month is compared from the day after start_day)."""
    with flask_app.app_context():
        appmod.start_books(tid, now=datetime.utcnow() - timedelta(days=1))
        db.session.commit()


def _user(client, admin_hdr, username, role):
    r = client.post("/api/users", headers=admin_hdr, json={"username": username, "password": "pw", "role": role})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    tok = client.post("/api/login", json={"username": username, "password": "pw"}).get_json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


# --- the nightly check -----------------------------------------------------------

def test_check_clean_after_normal_activity(app, client):
    a = make_tenant(client, "Books", "lk1")
    tid = _tid()
    _set_opening(client, a, 100, 20)
    # Customers first: the test helper wipes the creation bill behind the
    # listener's back, which after the start would be an `unexplained` entry.
    cid = _customer(client, a)
    two = _customer(client, a, "Two")
    _start_yesterday(tid)
    today = _today().isoformat()

    pid = _bill(client, a, cid, 40, date=today)
    _pay(client, a, pid)                                   # 40 cash in
    _bill(client, a, cid, 25, date=today)                  # open bill
    _prepay(client, a, two, 15)                            # 15 cash in
    cat = _make_category(client, a, "Tools")
    r = client.post("/api/expenses", headers=a, json={"category": cat, "amount": 12, "description": "Fuel",
                                                       "date": today})
    assert r.status_code == 201, r.get_data(as_text=True)
    sid = _make_supplier(client, a)
    r = client.post("/api/expenses", headers=a, json={"category": cat, "amount": 70, "description": "Cable",
                                                       "date": today, "is_credit": True, "supplier_id": sid})
    assert r.status_code == 201, r.get_data(as_text=True)
    _pay_supplier(client, a, sid, amount=30, date=today)
    eid = _make_employee(client, a, monthly_salary=0)
    _pay_employee(client, a, eid, amount=9, date=today)
    rid = _reseller(client, a)
    assert client.post(f"/api/resellers/{rid}/add_credit", headers=a, json={"amount": 80}).status_code == 200
    assert client.post(f"/api/resellers/{rid}/collect_payment", headers=a,
                       json={"amount": 50, "paid_via": "whish"}).status_code == 200
    up = _provider(client, a)
    assert client.post(f"/api/upstream-providers/{up}/topup", headers=a, json={"amount": 25}).status_code == 200
    r = client.post("/api/cash-entries", headers=a, json={"account": "cash", "amount": 10, "reason": "Owner",
                                                          "date": today})
    assert r.status_code == 201, r.get_data(as_text=True)
    r = client.post("/api/account-transfers", headers=a, json={"from_account": "cash", "to_account": "whish",
                                                               "amount": 20, "note": "deposit", "date": today})
    assert r.status_code == 201, r.get_data(as_text=True)
    flow = _flow(client, a, today)
    r = client.post("/api/day-closes", headers=a, json={"day": today, "counted_cash": flow["cash_end"] - 3,
                                                        "counted_whish": flow["whish_end"]})
    assert r.status_code == 201, r.get_data(as_text=True)

    result = _check(tid)
    assert result["unbalanced_entries"] == []
    assert result["party_mismatches"] == []
    assert result["cash"]["books"] == result["cash"]["cash_flow"] == flow["cash_end"] - 3
    assert result["whish"]["books"] == result["whish"]["cash_flow"] == flow["whish_end"]
    assert result["unexplained_count"] == 0
    assert result["problem_count"] == 0
    months = result["profit_by_month"]
    assert months and months[-1]["month"] == _today().strftime("%Y-%m")
    for m in months:
        assert m["books_cash_basis"] == m["reports"], m
    # 40 + 15 + 50 in; 12 + 30 + 9 + 25 out (credit expense and day close are not cash-basis profit)
    assert months[-1]["books_cash_basis"] == 105 - 76

    stored, at = _stored_check(tid)
    assert at is not None
    assert stored["party_mismatches"] == [] and stored["checked_at"] == result["checked_at"]


def test_check_flags_party_mismatch(app, client):
    a = make_tenant(client, "Books", "lk2")
    tid = _tid()
    _start_books_for_test(tid)
    sid = _make_supplier(client, a, "Raw Supplies")
    assert client.put(f"/api/suppliers/{sid}", headers=a,
                      json={"balance": 40, "balance_reason": "old invoice"}).status_code == 200
    with flask_app.app_context():
        db.session.execute(text("UPDATE supplier SET balance = 55 WHERE id = :id"), {"id": sid})
        db.session.commit()
        assert JournalEntry.query.filter_by(tenant_id=tid, kind="unexplained").count() == 0

    result = _check(tid)
    assert result["party_mismatches"] == [
        {"party_type": "supplier", "party_id": sid, "name": "Raw Supplies", "stored": 55.0, "books": 40.0}]
    stored, _ = _stored_check(tid)
    assert stored["party_mismatches"] == result["party_mismatches"]


def test_check_keeps_cash_opening_unknown_and_changes_no_data(app, client):
    a = make_tenant(client, "Books", "lk3")
    tid = _tid()
    cid = _customer(client, a)
    _start_books_for_test(tid)                 # no Cash Flow opening -> unknown
    assert _stored_check(tid)[0] == {"cash_opening_unknown": True}
    _pay(client, a, _bill(client, a, cid, 40, date=_today().isoformat()))
    with flask_app.app_context():
        lines_before = appmod.JournalLine.query.filter_by(tenant_id=tid).count()
        entries_before = JournalEntry.query.filter_by(tenant_id=tid).count()
    result = _check(tid)
    assert result["cash"] == {"books": 40.0, "cash_flow": None}
    assert result["whish"]["cash_flow"] is None
    stored, _ = _stored_check(tid)
    assert stored["cash_opening_unknown"] is True
    assert "party_mismatches" in stored
    with flask_app.app_context():
        assert appmod.JournalLine.query.filter_by(tenant_id=tid).count() == lines_before
        assert JournalEntry.query.filter_by(tenant_id=tid).count() == entries_before


def test_check_counts_unexplained_and_problems(app, client):
    a = make_tenant(client, "Books", "lk4")
    tid = _tid()
    _start_books_for_test(tid)
    sid = _make_supplier(client, a)
    with flask_app.app_context():
        db.session.get(Supplier, sid).balance = 33   # an unmapped path: the safety net posts it
        db.session.add(appmod.LedgerProblem(tenant_id=tid, source_type="payment", source_id=1, error="boom"))
        db.session.commit()
    result = _check(tid)
    assert result["unexplained_count"] == 1
    assert result["problem_count"] == 1
    assert result["party_mismatches"] == []


def test_ledger_nightly_runs_the_check_for_started_tenants(app, client):
    make_tenant(client, "Books", "lk5")
    tid = _tid()
    _start_books_for_test(tid)
    appmod.ledger_nightly()
    stored, at = _stored_check(tid)
    assert at is not None and "profit_by_month" in stored


# --- Books API -------------------------------------------------------------------

def test_accounts_totals_balance(app, client):
    a = make_tenant(client, "Books", "lk6")
    tid = _tid()
    _set_opening(client, a, 100, 0)
    cid = _customer(client, a)
    _start_books_for_test(tid)
    _pay(client, a, _bill(client, a, cid, 40, date=_today().isoformat()))
    _bill(client, a, cid, 25, date=_today().isoformat())

    r = client.get("/api/books/accounts", headers=a, query_string={"as_of": _today().isoformat()})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["totals"]["debit"] == body["totals"]["credit"] > 0
    by_key = {acc["key"]: acc for acc in body["accounts"]}
    assert by_key["cash"]["balance"] == 140 and by_key["cash"]["number"] == 1000
    assert by_key["cash"]["type"] == "asset" and by_key["cash"]["name"] == "Cash"
    assert by_key["customers"]["balance"] == 25
    assert by_key["income_subscriptions"]["balance"] == 65    # natural side: credit - debit
    assert by_key["income_subscriptions"]["credit"] == 65
    assert sum(acc["debit"] for acc in body["accounts"]) == body["totals"]["debit"]

    r = client.get("/api/books/accounts", headers=a,
                   query_string={"as_of": (_today() - timedelta(days=1)).isoformat()})
    assert r.status_code == 200
    assert r.get_json()["totals"] == {"debit": 0, "credit": 0}    # opening and activity are today
    assert client.get("/api/books/accounts", headers=a, query_string={"as_of": "nope"}).status_code == 400


def test_journal_filters_by_account_and_party(app, client):
    a = make_tenant(client, "Books", "lk7")
    tid = _tid()
    c1 = _customer(client, a, "Alice")
    c2 = _customer(client, a, "Bob")
    _start_books_for_test(tid)
    today = _today().isoformat()
    p1 = _bill(client, a, c1, 40, date=today)
    _pay(client, a, p1)
    _bill(client, a, c2, 25, date=today)
    sid = _make_supplier(client, a, "Acme")
    _pay_supplier(client, a, sid, amount=30, date=today)

    r = client.get("/api/books/journal", headers=a)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["total"] == len(body["items"]) >= 3
    ids = [e["id"] for e in body["items"]]
    order = [(e["date"], e["id"]) for e in body["items"]]
    assert order == sorted(order, reverse=True)                # newest first

    r = client.get("/api/books/journal", headers=a, query_string={"account": "suppliers"})
    items = r.get_json()["items"]
    assert len(items) == 1
    lines = items[0]["lines"]
    assert {l["account"] for l in lines} == {"suppliers", "cash"}
    sup = next(l for l in lines if l["account"] == "suppliers")
    assert sup["party_type"] == "supplier" and sup["party_id"] == sid and sup["party_name"] == "Acme"
    assert sup["debit"] == 30 and sup["credit"] == 0 and sup["source_type"] == "supplier_payment"

    r = client.get("/api/books/journal", headers=a, query_string={"party_type": "customer", "party_id": c1})
    items = r.get_json()["items"]
    assert items and all(any(l["party_type"] == "customer" and l["party_id"] == c1 for l in e["lines"])
                         for e in items)
    names = {l["party_name"] for e in items for l in e["lines"] if l["party_type"] == "customer"}
    assert "Alice" in names and "Bob" not in names

    r = client.get("/api/books/journal", headers=a, query_string={"per_page": 1, "page": 2})
    page = r.get_json()
    assert len(page["items"]) == 1 and page["items"][0]["id"] == ids[1] and page["total"] == len(ids)

    yesterday = (_today() - timedelta(days=1)).isoformat()
    r = client.get("/api/books/journal", headers=a, query_string={"start_date": yesterday, "end_date": yesterday})
    assert r.get_json() == {"items": [], "total": 0}


def test_books_check_route_returns_state(app, client):
    a = make_tenant(client, "Books", "lk8")
    tid = _tid()
    _start_books_for_test(tid)
    _check(tid)
    r = client.get("/api/books/check", headers=a)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["start_day"] == _today().isoformat()
    assert body["started_at"] and body["last_check"]["party_mismatches"] == []


def test_books_check_route_before_start(app, client):
    a = make_tenant(client, "Books", "lk9")
    r = client.get("/api/books/check", headers=a)
    assert r.status_code == 200
    assert r.get_json() == {"started_at": None, "start_day": None, "last_check": None}


def test_books_api_admin_only(app, client):
    a = make_tenant(client, "Books", "lk10")
    tid = _tid()
    _start_books_for_test(tid)
    for role in ("finance", "cashier", "collector", "employee"):
        hdr = _user(client, a, f"lk10{role}", role)
        for url in ("/api/books/accounts", "/api/books/journal", "/api/books/check"):
            assert client.get(url, headers=hdr).status_code == 403, (role, url)


def test_books_api_is_tenant_scoped(app, client):
    a = make_tenant(client, "Books A", "lk11a")
    tid_a = _tid()
    cid = _customer(client, a)
    _start_books_for_test(tid_a)
    _pay(client, a, _bill(client, a, cid, 40, date=_today().isoformat()))
    b = make_tenant(client, "Books B", "lk11b")
    r = client.get("/api/books/journal", headers=b)
    assert r.get_json() == {"items": [], "total": 0}
    r = client.get("/api/books/accounts", headers=b)
    assert r.get_json()["totals"] == {"debit": 0, "credit": 0}
