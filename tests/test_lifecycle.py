import app as appmod
from tests.conftest import make_tenant
from tests.test_superadmin import _superadmin_headers


def _suspend(app, slug):
    with app.app_context():
        appmod.Tenant.query.filter_by(slug=slug).first().status = "suspended"
        appmod.db.session.commit()


def test_suspended_tenant_blocked_from_data_but_can_reach_billing(app, client):
    hdr = make_tenant(client, "Biz A", "a_admin")
    _suspend(app, "biz-a")
    # Data route is blocked with 402...
    assert client.get("/api/customers", headers=hdr).status_code == 402
    # ...but export and billing remain reachable so the tenant can pay/leave.
    assert client.get("/api/tenant/export", headers=hdr).status_code == 200
    # Billing checkout gets past the suspend gate (free plan -> 400, not 402).
    assert client.post("/api/billing/checkout", headers=hdr, json={"plan": "free"}).status_code == 400


def test_export_contains_tenant_data(client):
    hdr = make_tenant(client, "Biz E", "e_admin")
    pid = client.post("/api/subscription_plans", headers=hdr,
                      json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr,
                json={"name": "Cust", "phone": "1", "address": "a",
                      "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})
    export = client.get("/api/tenant/export", headers=hdr).get_json()
    assert any(c["name"] == "Cust" for c in export["customer"])
    assert len(export["subscription_plan"]) == 1


def test_export_redacts_whish_callback_token(app, client):
    # A tenant must never be able to read back its own BillingPaymentAttempt
    # callback_token via /api/tenant/export -- that token is what gates
    # billing_whish_success, so leaking it lets a tenant self-grant Pro
    # without paying (see billing_whish_checkout / _apply_whish_payment_success).
    hdr = make_tenant(client, "Biz Whish Export", "whish_export_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(slug="biz-whish-export").first()
        attempt = appmod.BillingPaymentAttempt(
            tenant_id=tenant.id, billing_cycle="monthly", amount=120.0, currency="USD",
            whish_external_id="secret-ext-id", callback_token="super-secret-token",
            status="pending",
        )
        appmod.db.session.add(attempt)
        appmod.db.session.commit()

    export = client.get("/api/tenant/export", headers=hdr).get_json()
    rows = export["billing_payment_attempt"]
    assert len(rows) == 1
    assert "callback_token" not in rows[0]
    assert "whish_external_id" not in rows[0]
    # The response body as a whole must not leak the secret values either.
    import json as _json
    body = _json.dumps(export)
    assert "super-secret-token" not in body
    assert "secret-ext-id" not in body


def test_export_redacts_network_agent_token_hash(app, client):
    # Final review, Also-fix: _EXPORT_COLUMN_DENYLIST exists precisely for
    # secrets like this, and NetworkAgent was never added to it -- a tenant
    # could read back the bearer-secret hash gating its own on-prem agent's
    # access via its own data export.
    hdr = make_tenant(client, "Biz Agent Export", "agent_export_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Agent Export").first()
        agent = appmod.NetworkAgent(tenant_id=tenant.id, name="Box", token_hash="x")
        appmod.db.session.add(agent)
        appmod.db.session.commit()
        appmod._issue_agent_token(agent)  # overwrites the "x" placeholder
        appmod.db.session.commit()

    export = client.get("/api/tenant/export", headers=hdr).get_json()
    rows = export["network_agent"]
    assert len(rows) == 1
    assert "token_hash" not in rows[0]


def test_superadmin_delete_removes_only_that_tenant(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    make_tenant(client, "Biz B", "b_admin")
    pid = client.post("/api/subscription_plans", headers=a,
                      json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    client.post("/api/customers", headers=a,
                json={"name": "Cust", "phone": "1", "address": "a",
                      "subscription_plan_id": pid, "subscription_start_date": "2026-01-01"})
    sa = _superadmin_headers(app, client)
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        b_tid = appmod.Tenant.query.filter_by(slug="biz-b").first().id

    assert client.delete(f"/api/admin/tenants/{a_tid}", headers=sa).status_code == 200
    with app.app_context():
        assert appmod.db.session.get(appmod.Tenant, a_tid) is None
        assert appmod.Customer.query.filter_by(tenant_id=a_tid).count() == 0
        assert appmod.User.query.filter_by(tenant_id=a_tid).count() == 0
        assert appmod.db.session.get(appmod.Tenant, b_tid) is not None   # B untouched


def test_tenant_owned_models_all_in_delete_order():
    """Guards against the exact bug found and fixed twice before (Phase 3:
    MonthlyProfitEstimate, Phase 4a: BillingPaymentAttempt) -- a model present
    in TENANT_OWNED_MODELS but missing from _TENANT_DELETE_ORDER causes a
    ForeignKeyViolation on Postgres (not SQLite, which doesn't enforce FKs)
    when a tenant is deleted. Known pre-existing gaps are excluded here (see
    the separate _TENANT_DELETE_ORDER cleanup follow-up, not part of this
    plan) so this test only guards against NEW regressions -- starting with
    WhatsAppTemplate, added by this task."""
    known_pre_existing_gaps = {
        appmod.Employee, appmod.SalaryCharge, appmod.SalaryPayment,
        appmod.UpstreamProvider, appmod.UpstreamProviderPayment, appmod.MikrotikServer,
        appmod.ExchangeRate,
    }
    missing = set(appmod.TENANT_OWNED_MODELS) - set(appmod._TENANT_DELETE_ORDER) - known_pre_existing_gaps
    assert missing == set(), f"Models missing from _TENANT_DELETE_ORDER: {missing}"
