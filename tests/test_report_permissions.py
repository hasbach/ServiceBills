"""Report permissions (roadmap 1B, step 18): financial reports, the
dashboard and expense categories answer only admins and finance users. The
pages that show them were already hidden from other roles; the server now
refuses too."""
import pytest

from tests.conftest import make_tenant, auth_headers

REPORTS = [
    "/api/reports/total-sales",
    "/api/reports/unpaid-payments",
    "/api/reports/customer-whish-payments",
    "/api/reports/customer-numbers",
    "/api/reports/expenses-total",
    "/api/reports/monthly-revenue",
    "/api/dashboard",
    "/api/reports/revenue",
    "/api/reports/overdue",
    "/api/reports/active-subscriptions-by-plan",
    "/api/reports/collector-progress",
    "/api/reports/daily-cash",
    "/api/reports/financial",
    "/api/expense_categories",
]


@pytest.mark.parametrize("role", ["cashier", "collector", "employee", "technician"])
def test_other_roles_are_refused(app, client, role):
    make_tenant(client, "B", f"rp_{role}_admin")
    hdr = auth_headers(client, f"rp_{role}", role=role)
    for path in REPORTS:
        r = client.get(path, headers=hdr)
        assert r.status_code == 403, (role, path, r.status_code)
    r = client.post("/api/expense_categories", headers=hdr, json={"name": "Fuel"})
    assert r.status_code == 403


@pytest.mark.parametrize("role", ["admin", "finance"])
def test_admin_and_finance_are_let_through(app, client, role):
    a = make_tenant(client, "B", f"rp_ok_{role}_admin")
    hdr = a if role == "admin" else auth_headers(client, "rp_ok_finance", role="finance")
    for path in REPORTS:
        r = client.get(path, headers=hdr)
        assert r.status_code != 403, (role, path, r.get_data(as_text=True)[:200])
