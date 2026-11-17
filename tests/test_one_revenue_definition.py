"""Finance roadmap 1A, fix 9: one definition of revenue. The dashboard, the
revenue / financial / monthly reports and the Payments page totals give the
same figure for the same money, and months and days are the business's own
(Asia/Beirut by default), not UTC."""
from datetime import datetime

from tests.conftest import make_tenant
from app import app as flask_app, db, Payment, User
from tests.test_daily_cash_report import _make_plan, _make_customer
from tests.test_daily_cash_flow import _tenant_id

OCT = datetime(2026, 10, 12, 9)
# 22:30 UTC on 31 Oct is 00:30 on 1 Nov in Beirut (UTC+2 in winter).
JUST_AFTER_LOCAL_MIDNIGHT = datetime(2026, 10, 31, 22, 30)


def _row(tid, cust, amount, when=OCT, **kw):
    kw.setdefault("paid", True)
    return Payment(tenant_id=tid, customer_id=cust, amount=amount, currency="USD", fx_rate_to_reporting=1,
                   date=when, paid_at=when if kw["paid"] else None, **kw)


def _setup(client, username, rows):
    a = make_tenant(client, "Biz A", username)
    tid = _tenant_id(username)
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    with flask_app.app_context():
        Payment.query.filter_by(tenant_id=tid, customer_id=cust).delete()
        db.session.add_all(rows(tid, cust))
        db.session.commit()
    return a


def _figures(client, hdr, start, end):
    """Customer income for start..end from every place that reports it."""
    rng = {"start_date": start, "end_date": end}
    iso = {"start_date": f"{start}T00:00:00", "end_date": f"{end}T00:00:00"}
    months = {start[:7], end[:7]}
    sales = client.get("/api/reports/total-sales", headers=hdr).get_json()
    return {
        "dashboard": client.get("/api/dashboard", headers=hdr, query_string=rng).get_json()["customerRevenue"],
        "revenue_report": client.get("/api/reports/revenue", headers=hdr, query_string=rng).get_json()["total_revenue"],
        "financial": client.get("/api/reports/financial", headers=hdr, query_string=iso).get_json()["totals"]["income"],
        "total_sales": sum(m["value"] for m in sales if m["month"] in months),
    }


def test_every_revenue_figure_agrees(app, client):
    a = _setup(client, "one1", lambda tid, cust: [
        _row(tid, cust, 40),                               # bill paid in cash
        _row(tid, cust, 50, pre_payment=True),             # prepayment
        _row(tid, cust, 25, settled_from_credit=True),     # bill paid out of that credit: not new money
        _row(tid, cust, 60, is_gratis=True),               # forgiven: no money
        _row(tid, cust, 10, is_refund=True),               # money given back
        _row(tid, cust, 30, paid=False),                   # still owed
    ])
    figures = _figures(client, a, "2026-10-01", "2026-10-31")
    assert figures == {name: 80 for name in figures}, figures

    # The Payments page total (all paid rows, no date filter) is the same money.
    totals = client.get("/api/payments", headers=a).get_json()["totals"]
    assert totals["paid"]["amount"] == 80


def test_month_boundary_follows_the_business_calendar(app, client):
    a = _setup(client, "one2", lambda tid, cust: [_row(tid, cust, 40, when=JUST_AFTER_LOCAL_MIDNIGHT)])

    october = _figures(client, a, "2026-10-01", "2026-10-31")
    november = _figures(client, a, "2026-11-01", "2026-11-30")
    assert october == {name: 0 for name in october}, october
    assert november == {name: 40 for name in november}, november

    by_month = {m["month"]: m["value"] for m in client.get("/api/reports/total-sales", headers=a).get_json()}
    assert by_month == {"2026-11": 40}
    net = {m["month"]: m["value"] for m in client.get("/api/reports/monthly-revenue", headers=a).get_json()}
    assert net == {"2026-11": 40}
    fin = client.get("/api/reports/financial", headers=a, query_string={
        "start_date": "2026-10-01T00:00:00", "end_date": "2026-11-30T00:00:00"}).get_json()
    assert {m["month"]: m["income"] for m in fin["monthly_data"] if m["income"]} == {"2026-11": 40}


def test_last_day_of_the_range_is_included_in_full(app, client):
    late = datetime(2026, 10, 20, 20, 15)   # 22:15 / 23:15 Beirut on the 20th
    a = _setup(client, "one3", lambda tid, cust: [_row(tid, cust, 40, when=late)])
    figures = _figures(client, a, "2026-10-20", "2026-10-20")
    assert figures["dashboard"] == 40 and figures["revenue_report"] == 40 and figures["financial"] == 40


def test_collector_progress_counts_what_was_actually_collected(app, client):
    def rows(tid, cust):
        collector = User.query.filter_by(tenant_id=tid).first().id
        return [
            _row(tid, cust, 40, paid=False, collected=True, collected_amount=25,
                 collected_at=OCT, collected_by_id=collector),          # partial collection
            _row(tid, cust, 30, collected=True, collected_at=OCT, collected_by_id=collector),
        ]
    a = _setup(client, "one4", rows)
    r = client.get("/api/reports/collector-progress", headers=a,
                   query_string={"start_date": "2026-10-12", "end_date": "2026-10-12"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert [(c["total_amount"], c["total_payments"]) for c in r.get_json()] == [(55, 2)]   # was 70
