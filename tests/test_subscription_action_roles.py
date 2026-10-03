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
# Serves walk-in customers: adds and fully edits customers, renews/cancels/
# activates (single and bulk), adds charges, collects payments. Below
# finance: can never record money as received (confirm payment, pre-payment,
# or edit the balance), and cannot delete.

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


def test_cashier_can_fully_edit_but_not_the_balance(client):
    admin_hdr = make_tenant(client, "Biz F", "f_admin")
    cashier_hdr = _create_user(client, admin_hdr, "f_cashier", "cashier")
    customer_id = _setup_customer(client, admin_hdr)

    r = client.put(f"/api/customers/{customer_id}", headers=cashier_hdr,
                   json={"name": "C", "discount": 5, "cost_override": 1, "reseller_id": "",
                         "upstream_username": "x", "onu_mac_address": ""})
    assert r.status_code == 200, r.get_json()
    customer = appmod.Customer.query.get(customer_id)
    assert float(customer.discount) == 5 and customer.upstream_username == "x"

    before = float(customer.balance)
    r = client.put(f"/api/customers/{customer_id}", headers=cashier_hdr, json={"name": "C", "balance": 100})
    assert r.status_code == 403
    assert float(appmod.Customer.query.get(customer_id).balance) == before


def test_cashier_can_add_customers_and_run_bulk_renew_cancel(client):
    admin_hdr = make_tenant(client, "Biz G", "g_admin")
    cashier_hdr = _create_user(client, admin_hdr, "g_cashier", "cashier")
    customer_id = _setup_customer(client, admin_hdr)
    plan_id = appmod.Customer.query.get(customer_id).subscription_plan_id

    r = client.post("/api/customers", headers=cashier_hdr,
                    json={"name": "Walk-in", "phone": "9", "address": "z", "subscription_plan_id": plan_id,
                          "subscription_start_date": "2026-01-01"})
    assert r.status_code in (200, 201), r.get_json()
    assert client.get("/api/customer-form-options", headers=cashier_hdr).status_code == 200
    assert client.post("/api/customers/bulk_renew_subscription", headers=cashier_hdr,
                       json={"customer_ids": [customer_id]}).status_code == 200
    assert client.post("/api/customers/bulk_cancel_subscription", headers=cashier_hdr,
                       json={"customer_ids": [customer_id]}).status_code == 200


def test_cashier_cannot_delete(client):
    admin_hdr = make_tenant(client, "Biz K", "k_admin")
    cashier_hdr = _create_user(client, admin_hdr, "k_cashier", "cashier")
    customer_id = _setup_customer(client, admin_hdr)

    assert client.delete(f"/api/customers/{customer_id}", headers=cashier_hdr).status_code == 403
    assert client.post("/api/customers/bulk_delete", headers=cashier_hdr,
                       json={"customer_ids": [customer_id]}).status_code == 403


def test_cashier_can_add_a_charge_but_not_a_received_payment(client):
    admin_hdr = make_tenant(client, "Biz L", "l_admin")
    cashier_hdr = _create_user(client, admin_hdr, "l_cashier", "cashier")
    customer_id = _setup_customer(client, admin_hdr)

    r = client.post("/api/payments", headers=cashier_hdr,
                    json={"customer_id": customer_id, "amount": 5, "reason": "router"})
    assert r.status_code in (200, 201), r.get_json()
    r = client.post("/api/payments", headers=cashier_hdr,
                    json={"customer_id": customer_id, "amount": 5, "reason": "prepaid", "pre_payment": True})
    assert r.status_code == 403
    r = client.post("/api/payments/generate_future", headers=cashier_hdr, json={"customer_id": customer_id, "until_date": "2026-12-31"})
    assert r.status_code == 200, r.get_json()


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


def test_customer_list_filters_status_and_keeps_requested_order_on_the_server(client):
    admin_hdr = make_tenant(client, "Biz M", "m_admin")
    plan_id = client.post("/api/subscription_plans", headers=admin_hdr,
                          json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    ids = {}
    for name, start in [("Charlie", "2026-01-01"), ("Alice", "2026-03-01"), ("Bob", "2026-02-01")]:
        ids[name] = client.post("/api/customers", headers=admin_hdr,
                                json={"name": name, "phone": "1", "address": "a", "subscription_plan_id": plan_id,
                                      "subscription_start_date": start}).get_json()["customer_id"]
    client.put(f"/api/customers/{ids['Bob']}/cancel_subscription", headers=admin_hdr)

    def names(**params):
        r = client.get("/api/customers", headers=admin_hdr, query_string={"sort_desc": "false", **params}).get_json()
        return [c["name"] for c in r["customers"]], r["total"]

    assert names(status="active", sort_by="name") == (["Alice", "Charlie"], 2)
    assert names(status="active", sort_by="expiry_date") == (["Charlie", "Alice"], 2)
    assert names(status="canceled") == (["Bob"], 1)
    assert names(sort_by="name")[1] == 3  # default 'all' for callers that don't send status


def test_customer_list_filters_by_expiry_day_of_month(client):
    admin_hdr = make_tenant(client, "Biz N", "n_admin")
    plan_id = client.post("/api/subscription_plans", headers=admin_hdr,
                          json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    from datetime import datetime
    for name, expiry in [("Day1 A", datetime(2026, 10, 1, 9)), ("Day1 B", datetime(2026, 11, 1, 20)),
                         ("Day15", datetime(2026, 10, 15))]:
        cid = client.post("/api/customers", headers=admin_hdr,
                          json={"name": name, "phone": "1", "address": "a", "subscription_plan_id": plan_id,
                                "subscription_start_date": "2026-09-01"}).get_json()["customer_id"]
        appmod.Customer.query.get(cid).subscription_expiry_date = expiry
    appmod.db.session.commit()

    def names(**params):
        r = client.get("/api/customers", headers=admin_hdr, query_string={"sort_by": "name", "sort_desc": "false", **params})
        return [c["name"] for c in r.get_json()["customers"]]

    assert names(expiry_day=1) == ["Day1 A", "Day1 B"]
    assert names(expiry_day=15) == ["Day15"]
    assert names(expiry_day=2) == []
    assert len(names(expiry_day="")) == 3  # blank = no filter
    assert len(names(expiry_day=40)) == 3  # out of range = no filter


def test_cashier_reads_whatsapp_deeplink_settings_without_secrets(client):
    """The cashier's Collect only opens a wa.me link if the page can see that
    WhatsApp is on -- the full settings endpoint is admin/finance (it holds
    the Meta token), so the page reads the secrets-free slice instead."""
    admin_hdr = make_tenant(client, "Biz WA", "wa_admin")
    cashier_hdr = _create_user(client, admin_hdr, "wa_cashier", "cashier")
    collector_hdr = _create_user(client, admin_hdr, "wa_collector", "collector")
    employee_hdr = _create_user(client, admin_hdr, "wa_employee", "employee")
    client.post("/api/whatsapp-settings", headers=admin_hdr, json={
        "mode": "deeplink", "enabled": True, "access_token": "SECRET-TOKEN",
        "app_secret": "SECRET-APP", "deeplink_msg_payment": "Got ${amount}, {customer_name}"})

    assert client.get("/api/whatsapp-settings", headers=cashier_hdr).status_code == 403
    for hdr in (cashier_hdr, collector_hdr, admin_hdr):
        r = client.get("/api/whatsapp-settings/deeplink", headers=hdr)
        assert r.status_code == 200
        s = r.get_json()["settings"]
        assert s["enabled"] is True and s["mode"] == "deeplink"
        assert s["deeplink_msg_payment"] == "Got ${amount}, {customer_name}"
        assert "SECRET" not in r.get_data(as_text=True)
    assert client.get("/api/whatsapp-settings/deeplink", headers=employee_hdr).status_code == 403


def test_reminder_and_balance_deeplink_texts_are_editable(client):
    """The Subscriptions page reminder dialog builds its wa.me text from these
    two settings; unset, they fall back to the texts it used to hardcode."""
    admin_hdr = make_tenant(client, "Biz WA3", "wa3_admin")
    cashier_hdr = _create_user(client, admin_hdr, "wa3_cashier", "cashier")
    client.post("/api/whatsapp-settings", headers=admin_hdr, json={"mode": "deeplink", "enabled": True})

    s = client.get("/api/whatsapp-settings/deeplink", headers=cashier_hdr).get_json()["settings"]
    assert "{balance}" in s["deeplink_msg_payment_reminder"]
    assert "{balance}" in s["deeplink_msg_current_balance"] and "{expiry_date}" in s["deeplink_msg_current_balance"]

    client.post("/api/whatsapp-settings", headers=admin_hdr, json={
        "deeplink_msg_payment_reminder": "Pay ${balance} please, {customer_name}",
        "deeplink_msg_current_balance": "You owe ${balance} until {expiry_date}"})
    s = client.get("/api/whatsapp-settings/deeplink", headers=cashier_hdr).get_json()["settings"]
    assert s["deeplink_msg_payment_reminder"] == "Pay ${balance} please, {customer_name}"
    assert s["deeplink_msg_current_balance"] == "You owe ${balance} until {expiry_date}"
    full = client.get("/api/whatsapp-settings", headers=admin_hdr).get_json()["settings"]
    assert full["deeplink_msg_payment_reminder"] == "Pay ${balance} please, {customer_name}"


def test_cashier_collect_sends_api_template(client, monkeypatch):
    admin_hdr = make_tenant(client, "Biz WA2", "wa2_admin")
    cashier_hdr = _create_user(client, admin_hdr, "wa2_cashier", "cashier")
    customer_id = _setup_customer(client, admin_hdr)
    payment_id = _first_unpaid_payment_id(client, admin_hdr, customer_id)
    sent = []
    monkeypatch.setattr(appmod, "send_whatsapp_message",
                        lambda customer, event_type, context=None: sent.append((customer.id, event_type, context)))

    r = client.put(f"/api/payments/{payment_id}/mark_paid", headers=cashier_hdr, json={"action": "collect"})
    assert r.status_code == 200
    assert sent and sent[0][0] == customer_id and sent[0][1] == "payment_paid"
