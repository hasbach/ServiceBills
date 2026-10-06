"""Refunds reduce revenue in the month they were paid out, everywhere revenue
is summed; the original payment keeps its own month."""
from datetime import datetime

from tests.conftest import make_tenant
from app import app as flask_app, db, Payment
from tests.test_daily_cash_report import _make_plan, _make_customer
from tests.test_daily_cash_flow import _tenant_id

SEPT = datetime(2026, 9, 10, 12)
OCT = datetime(2026, 10, 5, 12)


def _setup(client, username):
    """$40 paid in September, $15 of it refunded in October, plus a voided
    (reverted) refund that must count nowhere."""
    a = make_tenant(client, "Biz A", username)
    tid = _tenant_id(username)
    cust = _make_customer(client, a, _make_plan(client, a, price=40))
    with flask_app.app_context():
        # Drop the auto-created bill so only our rows are in play.
        Payment.query.filter_by(tenant_id=tid, customer_id=cust).delete()
        db.session.add_all([
            Payment(tenant_id=tid, customer_id=cust, amount=40, currency="USD", fx_rate_to_reporting=1,
                    date=SEPT, paid=True, paid_at=SEPT, pre_payment=True),
            Payment(tenant_id=tid, customer_id=cust, amount=15, currency="USD", fx_rate_to_reporting=1,
                    date=OCT, paid=True, paid_at=OCT, is_refund=True),
            Payment(tenant_id=tid, customer_id=cust, amount=7, currency="USD", fx_rate_to_reporting=1,
                    date=OCT, paid=False, is_refund=True, reverted_at=OCT),
        ])
        db.session.commit()
    return a, cust


def _by_month(rows, key="value"):
    return {r["month"]: r[key] for r in rows}


def test_total_sales_and_monthly_revenue_subtract_refunds_in_refund_month(app, client):
    a, _ = _setup(client, "rr1")
    sales = _by_month(client.get("/api/reports/total-sales", headers=a).get_json())
    assert sales == {"2026-09": 40, "2026-10": -15}
    net = _by_month(client.get("/api/reports/monthly-revenue", headers=a).get_json())
    assert net == {"2026-09": 40, "2026-10": -15}


def test_financial_report_income_is_net_of_refunds(app, client):
    a, _ = _setup(client, "rr2")
    fin = client.get("/api/reports/financial", headers=a, query_string={
        "start_date": "2026-09-01T00:00:00Z", "end_date": "2026-10-31T00:00:00Z"}).get_json()
    income = _by_month(fin["monthly_data"], "income")
    assert income["2026-09"] == 40 and income["2026-10"] == -15


def test_revenue_report_and_dashboard_are_net_of_refunds(app, client):
    a, _ = _setup(client, "rr3")
    rev = client.get("/api/reports/revenue", headers=a).get_json()
    assert (rev["total_revenue"], rev["refund_total"], rev["payment_count"]) == (25, 15, 1)
    assert rev["plan_revenue"] == {"Basic": 25}

    oct_only = client.get("/api/reports/revenue", headers=a, query_string={
        "start_date": "2026-10-01T00:00:00Z", "end_date": "2026-10-31T00:00:00Z"}).get_json()
    assert oct_only["total_revenue"] == -15

    dash = client.get("/api/dashboard", headers=a).get_json()
    assert dash["totalRevenue"] == 25


def test_payments_page_totals_are_net_of_refunds(app, client):
    a, cust = _setup(client, "rr4")
    totals = client.get("/api/payments", headers=a, query_string={"customer_id": cust}).get_json()["totals"]
    assert totals["paid"] == {"count": 1, "amount": 25}
    # The voided refund is not a bill: it's in no unpaid bucket.
    assert totals["unpaid"]["count"] == 0 and totals["uncollected"]["count"] == 0
