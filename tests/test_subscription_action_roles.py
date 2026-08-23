"""An 'employee' role can view subscriptions but must not be able to call
the mutating actions (renew/cancel/activate/delete) directly -- these are
gated by admin_or_finance_required(), added so that giving employees read
access to the Subscriptions page doesn't also silently hand them the
ability to call these endpoints, bypassing a frontend that merely hides
the buttons. See docs note in app.py's admin_or_finance_required()."""
import app as appmod
from tests.conftest import make_tenant


def _login(client, username, password="pw"):
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _create_user(client, admin_hdr, username, role):
    client.post("/api/users", headers=admin_hdr, json={"username": username, "password": "pw", "role": role})
    return _login(client, username)


def _setup_customer(client, admin_hdr):
    plan_id = client.post("/api/subscription_plans", headers=admin_hdr,
                          json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    resp = client.post("/api/customers", headers=admin_hdr,
                       json={"name": "C", "phone": "1", "address": "a",
                             "subscription_plan_id": plan_id,
                             "subscription_start_date": "2026-01-01"})
    return resp.get_json()["customer_id"]


def test_employee_cannot_renew_cancel_activate_or_delete_subscription(client):
    admin_hdr = make_tenant(client, "Biz A", "a_admin")
    employee_hdr = _create_user(client, admin_hdr, "a_employee", "employee")
    customer_id = _setup_customer(client, admin_hdr)

    assert client.post(f"/api/customers/{customer_id}/renew_subscription", headers=employee_hdr).status_code == 403
    assert client.put(f"/api/customers/{customer_id}/cancel_subscription", headers=employee_hdr).status_code == 403
    assert client.put(f"/api/customers/{customer_id}/activate_subscription", headers=employee_hdr).status_code == 403
    assert client.delete(f"/api/customers/{customer_id}", headers=employee_hdr).status_code == 403
    assert client.post("/api/customers/bulk_renew_subscription", headers=employee_hdr,
                       json={"customer_ids": [customer_id]}).status_code == 403
    assert client.post("/api/customers/bulk_cancel_subscription", headers=employee_hdr,
                       json={"customer_ids": [customer_id]}).status_code == 403
    assert client.post("/api/customers/bulk_delete", headers=employee_hdr,
                       json={"customer_ids": [customer_id]}).status_code == 403


def test_employee_can_still_read_the_customer_list(client):
    admin_hdr = make_tenant(client, "Biz B", "b_admin")
    employee_hdr = _create_user(client, admin_hdr, "b_employee", "employee")
    _setup_customer(client, admin_hdr)

    assert client.get("/api/customers", headers=employee_hdr).status_code == 200


def test_finance_role_can_cancel_and_reactivate_subscription(client):
    admin_hdr = make_tenant(client, "Biz C", "c_admin")
    finance_hdr = _create_user(client, admin_hdr, "c_finance", "finance")
    customer_id = _setup_customer(client, admin_hdr)

    assert client.put(f"/api/customers/{customer_id}/cancel_subscription", headers=finance_hdr).status_code == 200
    assert client.put(f"/api/customers/{customer_id}/activate_subscription", headers=finance_hdr).status_code == 200


def test_admin_role_unaffected_by_the_new_guard(client):
    admin_hdr = make_tenant(client, "Biz D", "d_admin")
    customer_id = _setup_customer(client, admin_hdr)

    assert client.put(f"/api/customers/{customer_id}/cancel_subscription", headers=admin_hdr).status_code == 200
