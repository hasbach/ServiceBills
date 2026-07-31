from tests.conftest import make_tenant


def _make_plan(client, hdr, name="Basic", price=10):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": name, "price": price, "billing_cycle": "monthly"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["plan"]["id"]


def _make_customer(client, hdr, plan_id, name="Cust", start_date="2026-01-01"):
    r = client.post("/api/customers", headers=hdr,
                    json={"name": name, "phone": "111", "address": "addr",
                          "subscription_plan_id": plan_id,
                          "subscription_start_date": start_date})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["customer_id"]


def _payment_ids(client, hdr):
    return [p["id"] for p in client.get("/api/payments", headers=hdr).get_json()["payments"]]


def test_bulk_mark_paid(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    plan_a = _make_plan(client, a)
    _make_customer(client, a, plan_a, name="CustA")
    ids_a = _payment_ids(client, a)
    assert len(ids_a) >= 2

    plan_b = _make_plan(client, b)
    _make_customer(client, b, plan_b, name="CustB")
    ids_b = _payment_ids(client, b)
    assert ids_b

    # Tenant A bulk-marks its own payments plus (an attempt at) tenant B's payment.
    r = client.post("/api/payments/bulk_mark_paid", headers=a,
                    json={"payment_ids": ids_a + [ids_b[0]]})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert set(body["succeeded"]) == set(ids_a)
    assert {f["id"] for f in body["failed"]} == {ids_b[0]}

    # Verify persisted: tenant A's payments are now paid; tenant B's is untouched.
    payments_a = client.get("/api/payments", headers=a).get_json()["payments"]
    assert all(p["paid"] for p in payments_a if p["id"] in ids_a)

    payments_b = client.get("/api/payments", headers=b).get_json()["payments"]
    assert not [p for p in payments_b if p["id"] == ids_b[0] and p["paid"]]


def test_bulk_delete_payments(client):
    a = make_tenant(client, "Biz A", "a_admin")
    plan_a = _make_plan(client, a)
    _make_customer(client, a, plan_a, name="CustA")
    ids_a = _payment_ids(client, a)
    assert len(ids_a) >= 2

    to_delete = ids_a[:2]
    r = client.post("/api/payments/bulk_delete", headers=a, json={"payment_ids": to_delete})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert set(body["succeeded"]) == set(to_delete)

    remaining_ids = _payment_ids(client, a)
    assert not (set(to_delete) & set(remaining_ids))


def test_bulk_cancel_and_renew_subscriptions(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    plan_a = _make_plan(client, a)
    c1 = _make_customer(client, a, plan_a, name="C1")
    c2 = _make_customer(client, a, plan_a, name="C2")

    plan_b = _make_plan(client, b)
    c_other = _make_customer(client, b, plan_b, name="Other")

    # Bulk cancel: tenant A's two customers succeed, tenant B's customer is rejected as not found.
    r = client.post("/api/customers/bulk_cancel_subscription", headers=a,
                    json={"customer_ids": [c1, c2, c_other]})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert set(body["succeeded"]) == {c1, c2}
    assert {f["id"] for f in body["failed"]} == {c_other}

    # Calling cancel again on the same (already-canceled) ids reports them as failed, not crashed.
    r2 = client.post("/api/customers/bulk_cancel_subscription", headers=a,
                     json={"customer_ids": [c1, c2]})
    assert r2.status_code == 200
    body2 = r2.get_json()
    assert body2["succeeded"] == []
    assert {f["id"] for f in body2["failed"]} == {c1, c2}

    # Tenant B's customer subscription was never touched.
    listed_b = client.get("/api/customers", headers=b).get_json()["customers"]
    assert next(c for c in listed_b if c["id"] == c_other)["is_subscription_active"] is True

    # Bulk renew reactivates both.
    r3 = client.post("/api/customers/bulk_renew_subscription", headers=a,
                     json={"customer_ids": [c1, c2]})
    assert r3.status_code == 200, r3.get_data(as_text=True)
    body3 = r3.get_json()
    assert len(body3["succeeded"]) == 2
    assert body3["failed"] == []

    listed_a = client.get("/api/customers", headers=a).get_json()["customers"]
    assert all(c["is_subscription_active"] for c in listed_a if c["id"] in (c1, c2))


def test_bulk_delete_support_tickets(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    plan_a = _make_plan(client, a)
    c1 = _make_customer(client, a, plan_a, name="C1")

    plan_b = _make_plan(client, b)
    c_other = _make_customer(client, b, plan_b, name="Other")

    def _make_ticket(hdr, customer_id, title):
        r = client.post("/api/support-tickets", headers=hdr,
                        json={"customer_id": customer_id, "title": title,
                              "description": "d", "priority": "medium"})
        assert r.status_code in (200, 201), r.get_data(as_text=True)
        return r.get_json()["id"]

    t1 = _make_ticket(a, c1, "T1")
    t2 = _make_ticket(a, c1, "T2")
    t_other = _make_ticket(b, c_other, "Other")

    r = client.post("/api/support-tickets/bulk_delete", headers=a,
                    json={"ticket_ids": [t1, t2, t_other]})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert set(body["succeeded"]) == {t1, t2}
    assert {f["id"] for f in body["failed"]} == {t_other}

    listed_a = client.get("/api/support-tickets", headers=a).get_json()["tickets"]
    assert listed_a == []

    # Tenant B's ticket survives.
    listed_b = client.get("/api/support-tickets", headers=b).get_json()["tickets"]
    assert len(listed_b) == 1


def test_bulk_delete_customers(client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    plan_a = _make_plan(client, a)
    c1 = _make_customer(client, a, plan_a, name="C1")
    c2 = _make_customer(client, a, plan_a, name="C2")

    plan_b = _make_plan(client, b)
    c_other = _make_customer(client, b, plan_b, name="Other")

    r = client.post("/api/customers/bulk_delete", headers=a,
                    json={"customer_ids": [c1, c2, c_other]})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert set(body["succeeded"]) == {c1, c2}
    assert {f["id"] for f in body["failed"]} == {c_other}

    listed_a = client.get("/api/customers", headers=a).get_json()["customers"]
    assert listed_a == []

    # Tenant B's customer survives.
    listed_b = client.get("/api/customers", headers=b).get_json()["customers"]
    assert len(listed_b) == 1
