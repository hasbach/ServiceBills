"""Audit log (roadmap 1B, step 13): every create / edit / delete of a
financial record is recorded with who, when and what changed."""
from tests.conftest import make_tenant, auth_headers
from tests.test_receive_payment import _customer, _bill, _prepay
from tests.test_cash_entries_transfers import _entry, _transfer, DAY
from app import app as flask_app, db, AuditLog, Payment, generate_missing_payments, Tenant


def _log(client, hdr, **params):
    r = client.get("/api/audit-log", headers=hdr, query_string=params)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_creating_a_bill_is_logged_with_who_and_what(app, client):
    a = make_tenant(client, "B", "al1")
    cid = _customer(client, a, name="Rima")
    pid = _bill(client, a, cid, 25)

    items = _log(client, a, entity="payment", entity_id=pid)["items"]
    # (the helper wipes the customer's first bill with a bulk delete, so
    # SQLite may hand its id out again: take the newest row for this id)
    row = items[0]
    assert row["action"] == "create" and row["changed_by"] == "al1"
    assert row["amount"] == 25 and row["party_type"] == "customer" and row["party_id"] == cid
    assert row["party_name"] == "Rima"
    assert row["changes"]["amount"] == 25 and row["changes"]["paid"] is False
    assert row["date"].endswith("Z")


def test_paying_a_bill_logs_old_and_new_values(app, client):
    a = make_tenant(client, "B", "al2")
    cid = _customer(client, a)
    pid = _bill(client, a, cid, 25)
    r = client.put(f"/api/payments/{pid}/mark_paid", headers=a, json={})
    assert r.status_code == 200, r.get_data(as_text=True)

    updates = _log(client, a, entity="payment", entity_id=pid, action="update")["items"]
    assert updates, "paying the bill should be logged"
    merged = {}
    for u in reversed(updates):
        merged.update(u["changes"])
    assert merged["paid"] == [False, True]
    assert merged["paid_at"][0] is None and merged["paid_at"][1]


def test_a_deleted_record_can_still_be_read_in_the_log(app, client):
    a = make_tenant(client, "B", "al3")
    r = _entry(client, a, amount=70, reason="Owner top-up")
    eid = r.get_json()["id"]
    assert client.delete(f"/api/cash-entries/{eid}", headers=a).status_code == 200

    items = _log(client, a, entity="cash_entry", entity_id=eid)["items"]
    assert [i["action"] for i in items] == ["delete", "create"]
    gone = items[0]
    assert gone["changed_by"] == "al3" and gone["amount"] == 70
    assert gone["changes"]["reason"] == "Owner top-up" and gone["changes"]["account"] == "cash"


def test_an_edit_that_changes_nothing_is_not_logged(app, client):
    a = make_tenant(client, "B", "al4")
    eid = _entry(client, a, amount=50).get_json()["id"]
    before = _log(client, a, entity="cash_entry")["total"]
    with flask_app.app_context():
        from app import CashEntry
        e = db.session.get(CashEntry, eid)
        e.amount = 50          # same value
        db.session.commit()
    assert _log(client, a, entity="cash_entry")["total"] == before


def test_transfers_expenses_and_day_close_are_logged(app, client):
    a = make_tenant(client, "B", "al5")
    assert _transfer(client, a).status_code == 201
    r = client.put("/api/reports/cash-opening", headers=a, json={"date": DAY, "amount": 100, "whish_amount": 0})
    assert r.status_code == 200, r.get_data(as_text=True)
    r = client.post("/api/day-closes", headers=a, json={"day": DAY, "counted_cash": 0, "counted_whish": 0})
    assert r.status_code == 201, r.get_data(as_text=True)
    close_id = r.get_json()["id"]
    assert client.delete(f"/api/day-closes/{close_id}", headers=a).status_code == 200

    entities = [(i["entity"], i["action"]) for i in _log(client, a)["items"]]
    assert ("account_transfer", "create") in entities
    assert ("day_close", "create") in entities
    assert ("day_close", "delete") in entities            # the reopen
    reopen = _log(client, a, entity="day_close", action="delete")["items"][0]
    assert reopen["changes"]["day"] == DAY and reopen["changed_by"] == "al5"


def test_system_changes_are_logged_without_a_user(app, client):
    a = make_tenant(client, "B", "al6")
    cid = _customer(client, a)
    with flask_app.app_context():
        tid = Tenant.query.order_by(Tenant.id.desc()).first().id
        before = AuditLog.query.filter_by(tenant_id=tid).count()
        p = Payment(tenant_id=tid, customer_id=cid, amount=10, reason="auto")
        db.session.add(p)
        db.session.commit()
        row = AuditLog.query.filter_by(tenant_id=tid).order_by(AuditLog.id.desc()).first()
        assert AuditLog.query.filter_by(tenant_id=tid).count() == before + 1
        assert row.changed_by is None and row.entity == "payment" and row.entity_id == p.id
    assert _log(client, a, user="system")["total"] >= 1


def test_a_rolled_back_change_leaves_no_log_row(app, client):
    a = make_tenant(client, "B", "al7")
    cid = _customer(client, a)
    with flask_app.app_context():
        tid = Tenant.query.order_by(Tenant.id.desc()).first().id
        before = AuditLog.query.filter_by(tenant_id=tid).count()
        db.session.add(Payment(tenant_id=tid, customer_id=cid, amount=10, reason="oops"))
        db.session.flush()
        db.session.rollback()
        assert AuditLog.query.filter_by(tenant_id=tid).count() == before


def test_filters_party_user_and_paging(app, client):
    a = make_tenant(client, "B", "al8")
    c1 = _customer(client, a, name="One")
    c2 = _customer(client, a, name="Two")
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        _bill(client, a, c1, 25, day)
    _prepay(client, a, c2, 40)

    one = _log(client, a, party_type="customer", party_id=c1)
    assert one["total"] >= 3 and all(i["party_id"] == c1 for i in one["items"])
    assert _log(client, a, user="nobody")["total"] == 0
    assert "al8" in _log(client, a)["users"]
    page = _log(client, a, per_page=2, page=1)
    assert len(page["items"]) == 2 and page["total"] > 2
    ids = [i["id"] for i in page["items"]]
    assert ids == sorted(ids, reverse=True)
    assert _log(client, a, start_date="2000-01-01", end_date="2000-01-02")["total"] == 0


def test_log_is_admin_only_and_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "al9")
    _entry(client, a)
    fin = auth_headers(client, username="al9fin", role="finance")
    assert client.get("/api/audit-log", headers=fin).status_code == 403
    b = make_tenant(client, "Biz B", "al10")
    assert _log(client, b)["total"] == 0
    assert _log(client, a)["total"] >= 1


def test_the_reason_given_for_a_delete_is_kept(app, client):
    a = make_tenant(client, "B", "al11")
    eid = _entry(client, a).get_json()["id"]
    r = client.delete(f"/api/cash-entries/{eid}", headers=a, json={"reason": "  entered twice  "})
    assert r.status_code == 200
    gone = _log(client, a, entity="cash_entry", action="delete")["items"][0]
    assert gone["note"] == "entered twice"
    assert _log(client, a, entity="cash_entry", action="create")["items"][0]["note"] is None

    cid = _customer(client, a)
    ids = [_bill(client, a, cid, 25), _bill(client, a, cid, 30)]
    r = client.post("/api/payments/bulk_delete", headers=a, json={"payment_ids": ids, "reason": "wrong customer"})
    assert r.status_code == 200, r.get_data(as_text=True)
    notes = [i["note"] for i in _log(client, a, entity="payment", action="delete")["items"]]
    assert notes[:2] == ["wrong customer", "wrong customer"]

    tid = _transfer(client, a).get_json()["id"]
    assert client.delete(f"/api/account-transfers/{tid}?reason=typo", headers=a).status_code == 200
    assert _log(client, a, entity="account_transfer", action="delete")["items"][0]["note"] == "typo"
