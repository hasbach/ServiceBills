"""Finance roadmap 1A, fix 11: no money-out route accepts a negative, zero,
non-numeric or NaN amount (NaN passed the old `amount <= 0` checks and would
have poisoned the stored balance)."""
import pytest

from tests.conftest import make_tenant
from tests.test_day_close import _cat
from tests.test_profit_includes_resellers_and_upstream import _reseller, _provider

BAD = [-5, 0, "abc", "nan", "inf", None]


def _supplier(client, hdr):
    r = client.post("/api/suppliers", headers=hdr, json={"name": "Sup"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    body = r.get_json()
    return (body.get("supplier") or body)["id"]


def _employee(client, hdr):
    return client.post("/api/employees", headers=hdr,
                       json={"name": "Emp", "monthly_salary": 100}).get_json()["id"]


@pytest.mark.parametrize("bad", BAD)
def test_bad_amounts_are_rejected_everywhere(app, client, bad):
    a = make_tenant(client, "Biz", "val1")
    res, prov, sup, emp = _reseller(client, a), _provider(client, a), _supplier(client, a), _employee(client, a)
    cat = _cat(client, a)
    body = {"amount": bad}

    posts = [
        f"/api/resellers/{res}/add_credit",
        f"/api/resellers/{res}/apply_discount",
        f"/api/resellers/{res}/collect_payment",
        f"/api/upstream-providers/{prov}/topup",
        f"/api/upstream-providers/{prov}/renewal-cost",
        f"/api/suppliers/{sup}/payments",
        f"/api/employees/{emp}/payments",
    ]
    for url in posts:
        r = client.post(url, headers=a, json=body)
        assert r.status_code == 400, (url, bad, r.status_code, r.get_data(as_text=True))

    r = client.post(f"/api/employees/{emp}/charges", headers=a, json={"amount": bad, "type": "bonus"})
    assert r.status_code == 400, (bad, r.get_data(as_text=True))

    r = client.post("/api/expenses", headers=a,
                    json={"amount": bad, "category": cat, "description": "x", "date": "2026-11-01"})
    assert r.status_code == 400, (bad, r.get_data(as_text=True))


@pytest.mark.parametrize("bad", [-5, 0, "nan"])
def test_expense_cannot_be_edited_to_a_bad_amount(app, client, bad):
    a = make_tenant(client, "Biz", "val2")
    cat = _cat(client, a)
    r = client.post("/api/expenses", headers=a,
                    json={"amount": 10, "category": cat, "description": "x", "date": "2026-11-01"})
    assert r.status_code == 201, r.get_data(as_text=True)
    eid = r.get_json()["id"]

    r = client.put(f"/api/expenses/{eid}", headers=a, json={"amount": bad})
    assert r.status_code == 400, (bad, r.get_data(as_text=True))
    rows = client.get("/api/expenses", headers=a).get_json()
    rows = rows.get("expenses", rows) if isinstance(rows, dict) else rows
    assert next(e for e in rows if e["id"] == eid)["amount"] == 10
