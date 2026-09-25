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


# --- 'cashier': the office front-desk role ---------------------------------
# Serves walk-in customers: collects payments, edits/renews/cancels/activates
# a single subscription. Below finance: cannot confirm a payment as received,
# delete customers, run bulk actions, or touch money/network fields on edit.

def _first_unpaid_payment_id(client, admin_hdr, customer_id):
    payments = client.get(f"/api/payments?customer_id={customer_id}", headers=admin_hdr).get_json()["payments"]
    return next(p["id"] for p in payments if not p["paid"])


def test_cashier_can_edit_renew_cancel_and_activate(client):
    admin_hdr = make_tenant(client, "Biz E", "e_admin")
    cashier_hdr = _create_user(client, admin_hdr, "e_cashier", "cashier")
    customer_id = _setup_customer(client, admin_hdr)

    assert client.get("/api/subscription_plans", headers=cashier_hdr).status_code == 200
    r = client.put(f"/api/customers/{customer_id}", headers=cashier_hdr,
                   json={"name": "New Name", "phone": "2", "address": "b"})
    assert r.status_code == 200, r.get_json()
    assert client.post(f"/api/customers/{customer_id}/renew_subscription", headers=cashier_hdr).status_code == 200
    assert client.put(f"/api/customers/{customer_id}/cancel_subscription", headers=cashier_hdr).status_code == 200
    assert client.put(f"/api/customers/{customer_id}/activate_subscription", headers=cashier_hdr).status_code == 200


def test_cashier_cannot_edit_money_or_network_fields(client):
    admin_hdr = make_tenant(client, "Biz F", "f_admin")
    cashier_hdr = _create_user(client, admin_hdr, "f_cashier", "cashier")
    customer_id = _setup_customer(client, admin_hdr)

    for field, value in [("balance", 100), ("discount", 5), ("cost_override", 1),
                         ("reseller_id", ""), ("upstream_username", "x"), ("onu_mac_address", "")]:
        r = client.put(f"/api/customers/{customer_id}", headers=cashier_hdr, json={"name": "C", field: value})
        assert r.status_code == 403, field

    customer = appmod.Customer.query.get(customer_id)
    assert float(customer.balance) != 100


def test_cashier_cannot_delete_or_run_bulk_actions(client):
    admin_hdr = make_tenant(client, "Biz G", "g_admin")
    cashier_hdr = _create_user(client, admin_hdr, "g_cashier", "cashier")
    customer_id = _setup_customer(client, admin_hdr)

    assert client.delete(f"/api/customers/{customer_id}", headers=cashier_hdr).status_code == 403
    assert client.post("/api/customers/bulk_renew_subscription", headers=cashier_hdr,
                       json={"customer_ids": [customer_id]}).status_code == 403
    assert client.post("/api/customers/bulk_cancel_subscription", headers=cashier_hdr,
                       json={"customer_ids": [customer_id]}).status_code == 403
    assert client.post("/api/customers/bulk_delete", headers=cashier_hdr,
                       json={"customer_ids": [customer_id]}).status_code == 403


def test_cashier_can_collect_but_not_confirm_payment(client):
    admin_hdr = make_tenant(client, "Biz H", "h_admin")
    cashier_hdr = _create_user(client, admin_hdr, "h_cashier", "cashier")
    customer_id = _setup_customer(client, admin_hdr)
    payment_id = _first_unpaid_payment_id(client, admin_hdr, customer_id)

    r = client.put(f"/api/payments/{payment_id}/mark_paid", headers=cashier_hdr, json={"action": "pay"})
    assert r.status_code == 403
    r = client.put(f"/api/payments/{payment_id}/mark_paid", headers=cashier_hdr, json={"action": "collect"})
    assert r.status_code == 200, r.get_json()
    payment = appmod.Payment.query.get(payment_id)
    assert payment.collected is True and payment.paid is False

    assert client.put(f"/api/payments/{payment_id}/mark_gratis", headers=cashier_hdr, json={}).status_code == 403


def test_employee_still_cannot_edit_customer(client):
    admin_hdr = make_tenant(client, "Biz I", "i_admin")
    employee_hdr = _create_user(client, admin_hdr, "i_employee", "employee")
    customer_id = _setup_customer(client, admin_hdr)

    assert client.put(f"/api/customers/{customer_id}", headers=employee_hdr, json={"name": "X"}).status_code == 403


def test_customer_list_includes_upstream_provider_name(client):
    admin_hdr = make_tenant(client, "Biz J", "j_admin")
    customer_id = _setup_customer(client, admin_hdr)
    customer = appmod.Customer.query.get(customer_id)
    provider = appmod.UpstreamProvider(tenant_id=customer.tenant_id, name="Terra")
    appmod.db.session.add(provider)
    appmod.db.session.flush()
    customer.upstream_provider_id = provider.id
    appmod.db.session.commit()

    rows = client.get("/api/customers", headers=admin_hdr).get_json()["customers"]
    assert rows[0]["upstream_provider_name"] == "Terra"
