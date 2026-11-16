"""Finance roadmap 1A, fix 6: money resellers pay in is income, and upstream
top-ups are a cost, in every profit figure -- not only in the cash flow."""
from datetime import datetime, timedelta

from tests.conftest import make_tenant


def _reseller(client, hdr, name="Res"):
    r = client.post("/api/resellers", headers=hdr, json={"name": name, "phone": "70000000", "type": "type1"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    body = r.get_json()
    return (body.get("reseller") or body)["id"]


def _provider(client, hdr, name="Upstream"):
    r = client.post("/api/upstream-providers", headers=hdr, json={"name": name})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    body = r.get_json()
    return (body.get("provider") or body.get("upstream_provider") or body)["id"]


def _setup(client, username):
    a = make_tenant(client, "Biz", username)
    res, prov = _reseller(client, a), _provider(client, a)
    # A charge on the reseller's account is not money: only the collection is.
    assert client.post(f"/api/resellers/{res}/add_credit", headers=a, json={"amount": 500}).status_code == 200
    assert client.post(f"/api/resellers/{res}/collect_payment", headers=a, json={"amount": 200}).status_code == 200
    assert client.post(f"/api/upstream-providers/{prov}/topup", headers=a, json={"amount": 80}).status_code in (200, 201)
    return a


def _range():
    now = datetime.utcnow()
    return {"start_date": (now - timedelta(days=2)).strftime("%Y-%m-%d"),
            "end_date": (now + timedelta(days=2)).strftime("%Y-%m-%d")}


def test_dashboard_counts_reseller_income_and_upstream_cost(app, client):
    a = _setup(client, "pr1")
    for params in ({}, _range()):
        d = client.get("/api/dashboard", headers=a, query_string=params).get_json()
        assert d["resellerRevenue"] == 200 and d["customerRevenue"] == 0
        assert d["totalRevenue"] == 200
        assert d["upstreamExpenses"] == 80
        assert d["totalExpenses"] == 80


def test_financial_report_counts_reseller_income_and_upstream_cost(app, client):
    a = _setup(client, "pr2")
    r = client.get("/api/reports/financial", headers=a, query_string=_range())
    assert r.status_code == 200, r.get_data(as_text=True)
    totals = r.get_json()["totals"]
    assert totals["income"] == 200 and totals["income_resellers"] == 200 and totals["income_customers"] == 0
    assert totals["expenses"] == 80 and totals["expenses_upstream"] == 80
    assert totals["profit"] == 120
    month = r.get_json()["monthly_data"][-1]
    assert month["income_resellers"] == 200 and month["expenses_upstream"] == 80 and month["profit"] == 120


def test_monthly_reports_count_reseller_income_and_upstream_cost(app, client):
    a = _setup(client, "pr3")
    net = client.get("/api/reports/monthly-revenue", headers=a).get_json()
    assert sum(m["value"] for m in net) == 120
    expenses = client.get("/api/reports/expenses-total", headers=a).get_json()
    assert sum(m["value"] for m in expenses) == 80
    assert sum(m["upstream"] for m in expenses) == 80
