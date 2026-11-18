"""Books (roadmap 1C), task 3: every change to a financial record posts its
journal lines through the after_flush listener `_post_ledger`.

Spec: docs/superpowers/specs/2026-11-17-ledger-design.md ("The posting rule",
"Failure handling", "Performance")."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import event, func

import ledger
from tests.conftest import make_tenant
from tests.test_receive_payment import _customer, _bill, _prepay
from tests.test_expenses_payroll_supplier import (
    _make_category, _make_supplier, _make_employee, _pay_supplier, _pay_employee)
from tests.test_profit_includes_resellers_and_upstream import _reseller, _provider
from tests.test_cash_entries_transfers import _entry, _transfer, DAY
from app import (
    app as flask_app, db, Tenant, Customer, Payment, Supplier, Employee, Reseller,
    UpstreamProvider, UpstreamProviderPayment, SubscriptionPlan, LedgerAccount,
    JournalEntry, JournalLine, LedgerState, LedgerProblem, generate_missing_payments,
    _mark_payment_fully_paid, start_books)


# --- helpers -------------------------------------------------------------------

def _today():
    return datetime.now(ZoneInfo("Asia/Beirut")).date()


def _tid():
    with flask_app.app_context():
        return Tenant.query.order_by(Tenant.id.desc()).first().id


def _start_books_for_test(tenant_id):
    """Start the tenant's books with the real start_books (kept as a helper:
    other test files import it)."""
    with flask_app.app_context():
        start_books(tenant_id)
        db.session.commit()


def _books_party_balance(party_type, party_id):
    with flask_app.app_context():
        total = db.session.query(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0)).filter(
            JournalLine.party_type == party_type, JournalLine.party_id == party_id).scalar()
        return round(ledger.party_balance_from_books(party_type, float(total or 0)), 4)


def _stored_balance(model, pk):
    with flask_app.app_context():
        return round(float(db.session.get(model, pk).balance or 0), 4)


def _account(tid, key):
    """Dr - Cr total of one account."""
    with flask_app.app_context():
        total = db.session.query(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0)).join(
            LedgerAccount, LedgerAccount.id == JournalLine.account_id).filter(
            JournalLine.tenant_id == tid, LedgerAccount.key == key).scalar()
        return round(float(total or 0), 4)


def _account_by_day(tid, key):
    with flask_app.app_context():
        rows = db.session.query(JournalLine.entry_date,
                                func.sum(JournalLine.debit - JournalLine.credit)).join(
            LedgerAccount, LedgerAccount.id == JournalLine.account_id).filter(
            JournalLine.tenant_id == tid, LedgerAccount.key == key).group_by(JournalLine.entry_date).all()
        return {d: round(float(v), 4) for d, v in rows if abs(float(v)) > 1e-9}


def _assert_balanced(tid):
    """Debits equal credits, overall and per entry."""
    with flask_app.app_context():
        dr, cr = db.session.query(func.coalesce(func.sum(JournalLine.debit), 0),
                                  func.coalesce(func.sum(JournalLine.credit), 0)).filter(
            JournalLine.tenant_id == tid).one()
        assert round(float(dr), 4) == round(float(cr), 4)
        per_entry = db.session.query(JournalLine.entry_id,
                                     func.sum(JournalLine.debit - JournalLine.credit)).filter(
            JournalLine.tenant_id == tid).group_by(JournalLine.entry_id).all()
        assert all(abs(float(v)) < 1e-6 for _, v in per_entry)
        for entry in JournalEntry.query.filter_by(tenant_id=tid).all():
            dates = {l.entry_date for l in JournalLine.query.filter_by(entry_id=entry.id)}
            assert dates <= {entry.entry_date}


def _assert_customer_matches(tid, cid):
    _assert_balanced(tid)
    assert _books_party_balance("customer", cid) == _stored_balance(Customer, cid)


def _started_customer(client, username):
    a = make_tenant(client, "Books", username)
    cid = _customer(client, a)
    tid = _tid()
    _start_books_for_test(tid)
    return a, tid, cid


def _pay(client, hdr, pid, **body):
    r = client.put(f"/api/payments/{pid}/mark_paid", headers=hdr, json=body)
    assert r.status_code == 200, r.get_data(as_text=True)


# --- customer payments ---------------------------------------------------------

def test_new_bill_posts_receivable_and_income(app, client):
    a, tid, cid = _started_customer(client, "lp1")
    _bill(client, a, cid, 25, "2026-09-01")
    bill_day = datetime(2026, 9, 1).date()
    assert _account_by_day(tid, "customers") == {bill_day: 25}
    assert _account_by_day(tid, "income_subscriptions") == {bill_day: -25}
    with flask_app.app_context():
        entry = JournalEntry.query.filter_by(tenant_id=tid).one()
        assert entry.kind == "event" and entry.entry_date == bill_day
        assert entry.memo.startswith("Bill")
        lines = JournalLine.query.filter_by(entry_id=entry.id).all()
        assert {l.source_type for l in lines} == {"payment"}
    _assert_customer_matches(tid, cid)


def test_marking_paid_posts_cash_receipt(app, client):
    a, tid, cid = _started_customer(client, "lp2")
    pid = _bill(client, a, cid, 25)
    _pay(client, a, pid)
    assert _account_by_day(tid, "cash") == {_today(): 25}
    assert _account(tid, "customers") == 0
    _assert_customer_matches(tid, cid)


def test_revert_then_repay_nets_to_one_receipt(app, client):
    a, tid, cid = _started_customer(client, "lp3")
    pid = _bill(client, a, cid, 25)
    _pay(client, a, pid)
    r = client.put(f"/api/payments/{pid}/revert", headers=a, json={"reason": "wrong one"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _account(tid, "cash") == 0
    assert _account(tid, "customers") == 25
    _assert_customer_matches(tid, cid)
    _pay(client, a, pid)
    assert _account(tid, "cash") == 25
    assert _account(tid, "income_subscriptions") == -25
    _assert_customer_matches(tid, cid)
    with flask_app.app_context():
        # bill and first payment are events (only new keys); the revert and
        # the re-pay change lines already posted
        kinds = sorted(e.kind for e in JournalEntry.query.filter_by(tenant_id=tid))
        assert kinds == ["correction", "correction", "event", "event"]


def test_method_change_moves_money_between_cash_and_whish(app, client):
    a, tid, cid = _started_customer(client, "lp4")
    pid = _bill(client, a, cid, 25)
    _pay(client, a, pid)
    r = client.put(f"/api/payments/{pid}/method", headers=a, json={"method": "whish_transfer"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _account(tid, "cash") == 0
    assert _account(tid, "whish") == 25
    _assert_customer_matches(tid, cid)


def test_partial_payment_split_balances(app, client):
    a, tid, cid = _started_customer(client, "lp5")
    pid = _bill(client, a, cid, 25)
    _pay(client, a, pid, partial_payment=True, partial_amount=10)
    assert _account(tid, "cash") == 10
    assert _account(tid, "customers") == 15
    assert _account(tid, "income_subscriptions") == -25
    _assert_customer_matches(tid, cid)


def test_bill_settled_from_credit_posts_no_money(app, client):
    a, tid, cid = _started_customer(client, "lp6")
    _prepay(client, a, cid, 25)
    assert _account(tid, "cash") == 25
    _bill(client, a, cid, 25)
    with flask_app.app_context():
        assert Payment.query.filter_by(customer_id=cid, pre_payment=False).one().settled_from_credit
    assert _account(tid, "cash") == 25            # no second receipt
    assert _account(tid, "customers") == 0
    assert _account(tid, "income_subscriptions") == -25
    _assert_customer_matches(tid, cid)


def test_refund_posts_money_out(app, client):
    a, tid, cid = _started_customer(client, "lp7")
    pid = _bill(client, a, cid, 25)
    _pay(client, a, pid)
    r = client.post(f"/api/payments/{pid}/refund", headers=a, json={"amount": 10, "reason": "outage"})
    assert r.status_code == 201, r.get_data(as_text=True)
    assert _account(tid, "cash") == 15
    assert _account(tid, "customers") == 10
    _assert_customer_matches(tid, cid)


def test_delete_unpaid_bill_reverses_it_dated_today(app, client):
    a, tid, cid = _started_customer(client, "lp8")
    pid = _bill(client, a, cid, 25, "2026-09-01")
    r = client.delete(f"/api/payments/{pid}", headers=a)
    assert r.status_code == 200, r.get_data(as_text=True)
    bill_day = datetime(2026, 9, 1).date()
    today = _today()
    assert _account_by_day(tid, "customers") == ({bill_day: 25, today: -25} if bill_day != today else {})
    assert _account(tid, "income_subscriptions") == 0
    with flask_app.app_context():
        correction = JournalEntry.query.filter_by(tenant_id=tid, kind="correction").one()
        assert correction.entry_date == today
    _assert_customer_matches(tid, cid)


# --- other records ------------------------------------------------------------

def test_expense_supplier_credit_and_payment(app, client):
    a = make_tenant(client, "Books", "lp9")
    tid = _tid()
    _start_books_for_test(tid)
    cat = _make_category(client, a, "Tools")
    sid = _make_supplier(client, a)
    r = client.post("/api/expenses", headers=a, json={
        "category": cat, "amount": 300, "description": "Cable roll", "date": "2026-10-01",
        "is_credit": True, "supplier_id": sid})
    assert r.status_code == 201, r.get_data(as_text=True)
    _pay_supplier(client, a, sid, amount=120, date="2026-10-02")
    r = client.post("/api/expenses", headers=a, json={
        "category": cat, "amount": 40, "description": "Fuel", "date": "2026-10-03"})
    assert r.status_code == 201, r.get_data(as_text=True)

    with flask_app.app_context():
        acct = LedgerAccount.query.filter(LedgerAccount.tenant_id == tid,
                                          LedgerAccount.key.like("expense:%")).one()
        assert acct.name == "Tools" and acct.type == "expense" and 6000 <= acct.number < 7000
        memos = [e.memo for e in JournalEntry.query.filter_by(tenant_id=tid)]
        assert "Expense: Fuel" in memos
    assert _account(tid, acct.key) == 340
    assert _account(tid, "suppliers") == -180
    assert _account(tid, "cash") == -160
    _assert_balanced(tid)
    assert _books_party_balance("supplier", sid) == _stored_balance(Supplier, sid) == 180


def test_payroll_charge_and_payment(app, client):
    a = make_tenant(client, "Books", "lp10")
    tid = _tid()
    _start_books_for_test(tid)
    eid = _make_employee(client, a, monthly_salary=0)
    r = client.post(f"/api/employees/{eid}/charges", headers=a,
                    json={"type": "salary", "amount": 1000, "date": "2026-10-01"})
    assert r.status_code == 201, r.get_data(as_text=True)
    r = client.post(f"/api/employees/{eid}/charges", headers=a,
                    json={"type": "deduction", "amount": 50, "date": "2026-10-02"})
    assert r.status_code == 201, r.get_data(as_text=True)
    _pay_employee(client, a, eid, amount=600, date="2026-10-05")
    assert _account(tid, "cost_payroll") == 950
    assert _account(tid, "cash") == -600
    _assert_balanced(tid)
    assert _books_party_balance("employee", eid) == _stored_balance(Employee, eid) == 350


def test_reseller_charge_collection_discount(app, client):
    a = make_tenant(client, "Books", "lp11")
    tid = _tid()
    _start_books_for_test(tid)
    rid = _reseller(client, a)
    assert client.post(f"/api/resellers/{rid}/add_credit", headers=a, json={"amount": 500}).status_code == 200
    assert client.post(f"/api/resellers/{rid}/collect_payment", headers=a,
                       json={"amount": 200, "paid_via": "whish"}).status_code == 200
    assert client.post(f"/api/resellers/{rid}/apply_discount", headers=a, json={"amount": 30}).status_code == 200
    assert _account(tid, "income_resellers") == -500
    assert _account(tid, "whish") == 200
    assert _account(tid, "discounts") == 30
    _assert_balanced(tid)
    assert _books_party_balance("reseller", rid) == _stored_balance(Reseller, rid) == 270


def test_upstream_topup_and_correction(app, client):
    a = make_tenant(client, "Books", "lp12")
    tid = _tid()
    _start_books_for_test(tid)
    pid = _provider(client, a)
    assert client.post(f"/api/upstream-providers/{pid}/topup", headers=a, json={"amount": 80}).status_code == 200
    with flask_app.app_context():
        eid = UpstreamProviderPayment.query.filter_by(upstream_provider_id=pid).one().id
    r = client.put(f"/api/upstream-providers/{pid}/entries/{eid}", headers=a, json={"amount": 100})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _account(tid, "cash") == -100
    assert _account(tid, "upstream") == 100
    with flask_app.app_context():
        assert JournalEntry.query.filter_by(tenant_id=tid, kind="correction").count() == 1
    _assert_balanced(tid)
    assert _books_party_balance("upstream", pid) == _stored_balance(UpstreamProvider, pid) == 100


def test_cash_entry_transfer_and_day_close(app, client):
    a = make_tenant(client, "Books", "lp13")
    tid = _tid()
    _start_books_for_test(tid)
    assert _entry(client, a, amount=50).status_code == 201
    assert _transfer(client, a, amount=40).status_code == 201
    assert _account(tid, "cash") == 10
    assert _account(tid, "whish") == 40
    assert _account(tid, "money_in") == -50

    r = client.put("/api/reports/cash-opening", headers=a, json={"date": DAY, "amount": 0, "whish_amount": 0})
    assert r.status_code == 200, r.get_data(as_text=True)
    r = client.post("/api/day-closes", headers=a, json={"day": DAY, "counted_cash": 7, "counted_whish": 40})
    assert r.status_code == 201, r.get_data(as_text=True)
    close_id = r.get_json()["id"]
    assert _account(tid, "over_short") == 3          # 3 short in the cash drawer
    assert _account(tid, "cash") == 7
    assert client.delete(f"/api/day-closes/{close_id}", headers=a).status_code == 200
    assert _account(tid, "over_short") == 0
    assert _account(tid, "cash") == 10
    _assert_balanced(tid)


# --- system paths, volume, isolation, failure ---------------------------------

def test_whish_callback_payment_posts_with_record_tenant(app, client):
    a, tid, cid = _started_customer(client, "lp14")
    pid = _bill(client, a, cid, 25)
    with flask_app.app_context():  # no request, no JWT, like the Whish callback
        p = db.session.get(Payment, pid)
        p.collected_via = "whish"
        _mark_payment_fully_paid(p, db.session.get(Customer, cid))
        db.session.commit()
    assert _account(tid, "whish") == 25
    with flask_app.app_context():
        assert JournalLine.query.filter(JournalLine.tenant_id != tid).count() == 0
    _assert_customer_matches(tid, cid)


def test_bulk_billing_posts_one_entry_per_date(app, client):
    a = make_tenant(client, "Books", "lp15")
    tid = _tid()
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=75)
    with flask_app.app_context():
        plan = SubscriptionPlan(tenant_id=tid, name="Bulk", price=20, billing_cycle="monthly")
        db.session.add(plan)
        db.session.flush()
        for i in range(200):
            db.session.add(Customer(tenant_id=tid, name=f"C{i}", phone="1", address="x",
                                    subscription_plan_id=plan.id, subscription_start_date=start,
                                    subscription_expiry_date=start, is_subscription_active=True,
                                    balance=0))
        db.session.commit()
    _start_books_for_test(tid)

    selects = []

    def count(conn, cursor, statement, params, context, executemany):
        s = statement.lstrip().upper()
        if s.startswith("SELECT") and "JOURNAL_LINE" in s:
            selects.append(statement)

    with flask_app.app_context():
        engine = db.engine
        event.listen(engine, "before_cursor_execute", count)
        try:
            generate_missing_payments(tid)
        finally:
            event.remove(engine, "before_cursor_execute", count)

        bill_days = {p.date.date() for p in Payment.query.filter_by(tenant_id=tid)}
        assert len(bill_days) >= 2
        assert Payment.query.filter_by(tenant_id=tid).count() == 200 * len(bill_days)
        entries = JournalEntry.query.filter_by(tenant_id=tid).all()
        assert sorted(e.entry_date for e in entries) == sorted(bill_days)
        assert all(e.kind == "event" for e in entries)
        some = Customer.query.filter_by(tenant_id=tid).first().id
    print("journal_line SELECTs during bulk billing:", len(selects))
    assert len(selects) <= 5, selects
    assert _account(tid, "customers") == 20 * 200 * len(bill_days)
    _assert_customer_matches(tid, some)


def test_tenant_without_books_gets_no_lines(app, client):
    a = make_tenant(client, "No books", "lp16")
    cid = _customer(client, a)
    pid = _bill(client, a, cid, 25)
    _pay(client, a, pid)
    with flask_app.app_context():
        assert JournalLine.query.count() == 0
        assert JournalEntry.query.count() == 0
        assert LedgerProblem.query.count() == 0


def test_posting_error_does_not_block_payment(app, client, monkeypatch):
    a, tid, cid = _started_customer(client, "lp17")

    def boom(*args, **kwargs):
        raise RuntimeError("mapping bug")
    monkeypatch.setattr(ledger, "desired_lines", boom)

    r = client.post("/api/payments", headers=a, json={
        "customer_id": cid, "amount": 25, "reason": "Subscription", "date": "2026-09-01"})
    assert r.status_code == 201, r.get_data(as_text=True)
    pid = r.get_json()["payment"]["id"]
    with flask_app.app_context():
        assert db.session.get(Payment, pid) is not None
        problem = LedgerProblem.query.filter_by(tenant_id=tid).one()
        assert problem.source_type == "payment" and problem.source_id == pid
        assert "mapping bug" in problem.error
        assert JournalLine.query.filter_by(tenant_id=tid, source_type="payment").count() == 0
        # The gap shows: the safety net (task 4) posts the balance change unexplained.
        assert [e.kind for e in JournalEntry.query.filter_by(tenant_id=tid)] == ["unexplained"]
    assert _stored_balance(Customer, cid) == -25
    _assert_customer_matches(tid, cid)


def test_pre_books_bill_paid_later_posts_only_the_receipt(app, client):
    """A bill older than the books has starting-point lines; paying it posts
    the receipt only, not its income a second time."""
    a = make_tenant(client, "Books", "lp18")
    cid = _customer(client, a)
    pid = _bill(client, a, cid, 25, "2026-09-01")
    tid = _tid()
    _start_books_for_test(tid)                      # opening: customers +25 / opening -25
    _pay(client, a, pid)
    assert _account_by_day(tid, "cash") == {_today(): 25}
    assert _account_by_day(tid, "customers") == {}  # +25 opening, -25 receipt, both today
    assert _account(tid, "income_subscriptions") == 0
    assert _account(tid, "opening") == -25
    _assert_customer_matches(tid, cid)

    # Deleting it later: the income side goes to Opening balances.
    r = client.put(f"/api/payments/{pid}/revert", headers=a, json={"reason": "x"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert client.delete(f"/api/payments/{pid}", headers=a).status_code == 200
    assert _account(tid, "income_subscriptions") == 0
    assert _account(tid, "opening") == 0            # prior-period income reversed
    assert _account(tid, "customers") == 0
    assert _account(tid, "cash") == 0               # the receipt was reverted
    _assert_customer_matches(tid, cid)


def test_start_lines_without_a_day_are_matched(app, client):
    """Starting-point rows written without a day (day NULL) are dated by
    matching the record's desired lines, so an unchanged record diffs to
    nothing and paying it posts only the receipt."""
    a = make_tenant(client, "Books", "lp18b")
    cid = _customer(client, a)
    pid = _bill(client, a, cid, 25, "2026-09-01")
    tid = _tid()
    _start_books_for_test(tid)
    with flask_app.app_context():
        from app import LedgerStartLine
        rows = LedgerStartLine.query.filter_by(tenant_id=tid, source_type="payment", source_id=pid).all()
        assert len(rows) == 2
        for row in rows:
            row.day = None
        db.session.commit()
    _pay(client, a, pid)
    assert _account_by_day(tid, "cash") == {_today(): 25}
    assert _account(tid, "income_subscriptions") == 0
    assert _account(tid, "opening") == -25
    _assert_customer_matches(tid, cid)


def test_expired_record_is_still_posted(app, client):
    """A record loaded, committed (attributes expired), then changed or
    deleted is posted like any other."""
    a, tid, cid = _started_customer(client, "lp19")
    pid = _bill(client, a, cid, 25)
    other = _bill(client, a, cid, 30)
    with flask_app.app_context():
        p = db.session.get(Payment, pid)
        q = db.session.get(Payment, other)
        c = db.session.get(Customer, cid)
        db.session.commit()                      # expires p, q, c
        assert "tenant_id" not in db.inspect(p).dict
        c.balance = float(c.balance) + 25
        p.paid = True
        p.paid_at = datetime.utcnow()
        db.session.commit()
        assert "tenant_id" not in db.inspect(q).dict
        c.balance = float(c.balance) + 30
        db.session.delete(q)
        db.session.commit()
    assert _account(tid, "cash") == 25
    assert _account(tid, "income_subscriptions") == -25
    with flask_app.app_context():
        assert LedgerProblem.query.count() == 0
    _assert_customer_matches(tid, cid)


def test_rolled_back_savepoint_leaves_no_lines_or_party_totals(app, client):
    a, tid, cid = _started_customer(client, "lp20")
    with flask_app.app_context():
        sp = db.session.begin_nested()
        db.session.add(Payment(tenant_id=tid, customer_id=cid, amount=25, reason="x",
                               date=datetime(2026, 9, 1)))
        db.session.flush()
        assert db.session.info["ledger_party_posted"][(tid, "customer", cid)] == 25
        sp.rollback()
        assert not db.session.info.get("ledger_party_posted")
        db.session.commit()
        assert JournalLine.query.count() == 0
        assert JournalEntry.query.count() == 0


def test_listener_failure_flags_every_pending_record(app, client, monkeypatch):
    import app as appmod
    a, tid, cid = _started_customer(client, "lp21")
    pid = _bill(client, a, cid, 25)

    def boom(*args, **kwargs):
        raise RuntimeError("lookup down")
    monkeypatch.setattr(appmod, "_ledger_already", boom)
    _pay(client, a, pid)
    with flask_app.app_context():
        assert db.session.get(Payment, pid).paid
        problem = LedgerProblem.query.filter_by(tenant_id=tid).one()
        assert problem.source_type == "payment" and problem.source_id == pid
        assert "lookup down" in problem.error
    assert _account(tid, "cash") == 0


def _entries(tid):
    with flask_app.app_context():
        return [(e.kind, e.memo) for e in JournalEntry.query.filter_by(tenant_id=tid).order_by(JournalEntry.id)]


def test_bill_paid_on_its_own_bill_day_is_an_event(app, client):
    a, tid, cid = _started_customer(client, "lp22")
    pid = _bill(client, a, cid, 25, _today().isoformat())
    _pay(client, a, pid)
    entries = _entries(tid)
    assert [k for k, _ in entries] == ["event", "event"]
    assert entries[1][1].startswith("Payment received")
    _assert_customer_matches(tid, cid)


def test_gratis_on_its_own_bill_day_is_an_event(app, client):
    a, tid, cid = _started_customer(client, "lp23")
    pid = _bill(client, a, cid, 25, _today().isoformat())
    r = client.put(f"/api/payments/{pid}/mark_gratis", headers=a, json={"note": "goodwill"})
    assert r.status_code == 200, r.get_data(as_text=True)
    entries = _entries(tid)
    assert [k for k, _ in entries] == ["event", "event"]
    assert entries[1][1].startswith("Bill forgiven")
    assert _account(tid, "discounts") == 25
    _assert_customer_matches(tid, cid)


def test_changes_to_posted_lines_are_corrections_even_on_the_bill_day(app, client):
    a, tid, cid = _started_customer(client, "lp24")
    pid = _bill(client, a, cid, 25, _today().isoformat())
    _pay(client, a, pid)
    r = client.put(f"/api/payments/{pid}/method", headers=a, json={"method": "whish_transfer"})
    assert r.status_code == 200, r.get_data(as_text=True)
    r = client.put(f"/api/payments/{pid}/revert", headers=a, json={"reason": "wrong"})
    assert r.status_code == 200, r.get_data(as_text=True)
    _pay(client, a, pid)
    assert client.delete(f"/api/payments/{pid}", headers=a).status_code == 200
    kinds = [k for k, _ in _entries(tid)]
    assert kinds[:2] == ["event", "event"]
    assert kinds[2:] and set(kinds[2:]) == {"correction"}
    _assert_customer_matches(tid, cid)


def test_listener_failure_keeps_the_specific_record_errors(app, client, monkeypatch):
    """A record that failed on its own keeps its own error when the posting
    step for the rest of the flush fails afterwards."""
    import app as appmod
    a, tid, cid = _started_customer(client, "lp25")
    real = ledger.desired_lines

    def lines(kind, rec, local_day):
        if float(rec.amount) == 25:
            raise RuntimeError("mapping bug")
        return real(kind, rec, local_day)

    def guard_breaks():
        raise RuntimeError("guard")
    monkeypatch.setattr(ledger, "desired_lines", lines)
    monkeypatch.setattr(appmod, "_ledger_who", guard_breaks)
    with flask_app.app_context():
        db.session.add_all([
            Payment(tenant_id=tid, customer_id=cid, amount=25, reason="a", date=datetime(2026, 9, 1)),
            Payment(tenant_id=tid, customer_id=cid, amount=30, reason="b", date=datetime(2026, 9, 1)),
        ])
        db.session.commit()
        by_amount = {}
        for prob in LedgerProblem.query.filter_by(tenant_id=tid):
            by_amount[float(db.session.get(Payment, prob.source_id).amount)] = prob.error
    assert "mapping bug" in by_amount[25]
    assert "guard" in by_amount[30]
    assert _account(tid, "customers") == 0
