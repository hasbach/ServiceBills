"""Automated tests for Customer Service AI Agent tool endpoints.

Tests phone normalization, customer lookup, account status,
network diagnostics relaying, payment links, human escalation,
and multi-tenant boundary isolation.
"""
from datetime import datetime, timedelta
import pytest
import app as appmod
from tests.conftest import auth_headers


def _setup_customer(app, tenant_id, name="Georges Khoury", phone="70123456", balance=25.0):
    with app.app_context():
        plan = appmod.SubscriptionPlan(
            tenant_id=tenant_id,
            name="50 Mbps Unlimited",
            price=25.0,
            cost=15.0,
            billing_cycle="monthly"
        )
        appmod.db.session.add(plan)
        appmod.db.session.commit()

        customer = appmod.Customer(
            tenant_id=tenant_id,
            name=name,
            phone=phone,
            address="Beirut, Hamra St",
            subscription_plan_id=plan.id,
            subscription_start_date=datetime.utcnow() - timedelta(days=10),
            subscription_expiry_date=datetime.utcnow() + timedelta(days=20),
            is_subscription_active=True,
            balance=balance,
            onu_mac_address="aa:bb:cc:dd:ee:ff",
            pppoe_username="georges_pppoe"
        )
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        return customer.id, plan.id


def test_normalize_lebanese_phone():
    """Verify phone normalization handles standard Lebanese formats."""
    import cs_agent_tools
    c1 = cs_agent_tools.normalize_lebanese_phone("+961 70 123 456")
    assert "70123456" in c1
    assert "+96170123456" in c1

    c2 = cs_agent_tools.normalize_lebanese_phone("03123456")
    assert "03123456" in c2
    assert "3123456" in c2

    c3 = cs_agent_tools.normalize_lebanese_phone("0096171987654")
    assert "71987654" in c3


def test_cs_agent_config(app, client):
    """GET /api/cs-agent/config returns agent settings."""
    app.config["ELEVENLABS_AGENT_ID"] = "agent_test_123"
    res = client.get("/api/cs-agent/config")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["elevenlabs_agent_id"] == "agent_test_123"
    assert "agent_test_123" in data["ws_url"]


def test_lookup_customer_with_jwt_auth(app, client):
    """An authenticated user can look up a customer by phone."""
    headers = auth_headers(client, "admin_lookup", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        cust_id, _ = _setup_customer(app, tenant.id, "Charbel Nader", "70999888")

    # Look up using Lebanese international format +961
    res = client.get("/api/cs-agent/tools/lookup-customer?phone=%2B96170999888", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["found"] is True
    assert data["primary_customer"]["name"] == "Charbel Nader"
    assert data["primary_customer"]["id"] == cust_id


def test_lookup_customer_with_agent_secret(app, client):
    """An external caller (like ElevenLabs) can authenticate with X-CS-Agent-Secret."""
    app.config["CS_AGENT_SECRET"] = "secret_agent_token_xyz"
    headers = auth_headers(client, "admin_secret", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        cust_id, _ = _setup_customer(app, tenant.id, "Layla Saad", "03112233")

    # 1. Without secret -> 401
    bad_res = client.get(f"/api/cs-agent/tools/lookup-customer?phone=03112233&tenant_id={tenant.id}")
    assert bad_res.status_code == 401

    # 2. With valid secret header -> 200
    good_res = client.get(
        f"/api/cs-agent/tools/lookup-customer?phone=03112233&tenant_id={tenant.id}",
        headers={"X-CS-Agent-Secret": "secret_agent_token_xyz"}
    )
    assert good_res.status_code == 200
    data = good_res.get_json()
    assert data["found"] is True
    assert data["primary_customer"]["name"] == "Layla Saad"


def test_lookup_customer_not_found(app, client):
    """Querying an unknown number returns found=False with Arabic message."""
    headers = auth_headers(client, "admin_notfound", "pw123")
    res = client.get("/api/cs-agent/tools/lookup-customer?phone=96179000000", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["found"] is False
    assert "ما لقينا" in data["message_ar"]


def test_customer_status(app, client):
    """GET /api/cs-agent/tools/customer-status returns balance, plan and expiry."""
    headers = auth_headers(client, "admin_status", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        cust_id, _ = _setup_customer(app, tenant.id, "Michel Aoun", "71111222", balance=35.5)

    res = client.get(f"/api/cs-agent/tools/customer-status?customer_id={cust_id}", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["found"] is True
    assert data["name"] == "Michel Aoun"
    assert data["balance_due"] == 35.5
    assert data["plan_name"] == "50 Mbps Unlimited"
    assert "35.5" in data["summary_ar"] or "35.50" in data["summary_ar"]


def test_network_diagnostic_creation(app, client):
    """POST /api/cs-agent/tools/network-diagnostic enqueues job and returns diagnostic."""
    headers = auth_headers(client, "admin_diag", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        cust_id, _ = _setup_customer(app, tenant.id, "Nour Kassir", "70555666")
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="Core-CCR", host="10.0.0.1",
            username="admin", password="pw", device_type="mikrotik_ccr", api_port=8728
        )
        appmod.db.session.add(device)
        appmod.db.session.commit()

        # Update customer's network device
        cust = appmod.db.session.get(appmod.Customer, cust_id)
        cust.network_device_id = device.id
        appmod.db.session.commit()

    # Call network diagnostic with wait_seconds=1 (will time out gracefully or return job state)
    res = client.post(
        "/api/cs-agent/tools/network-diagnostic",
        json={"customer_id": cust_id, "wait_seconds": 1},
        headers=headers
    )
    assert res.status_code == 200
    data = res.get_json()
    assert "job_id" in data
    # Verify NetworkAgentJob was created in DB
    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, data["job_id"])
        assert job is not None
        assert job.operation == "secret_status"
        assert job.params == {"pppoe_username": "georges_pppoe"}


def test_send_payment_link(app, client):
    """POST /api/cs-agent/tools/send-payment-link returns payment instructions."""
    headers = auth_headers(client, "admin_pay", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        cust_id, _ = _setup_customer(app, tenant.id, "Rami Zein", "76123456", balance=40.0)

    res = client.post(
        "/api/cs-agent/tools/send-payment-link",
        json={"customer_id": cust_id},
        headers=headers
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["balance_due"] == 40.0
    assert "payment_url" in data
    assert "Whish" in data["instructions_ar"]


def test_escalate_to_human(app, client):
    """POST /api/cs-agent/tools/escalate opens a high-priority support ticket."""
    headers = auth_headers(client, "admin_escalate", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        cust_id, _ = _setup_customer(app, tenant.id, "Zeina Trad", "71444555")

    res = client.post(
        "/api/cs-agent/tools/escalate",
        json={
            "customer_id": cust_id,
            "reason": "Repeated internet dropouts",
            "summary": "Customer reported red PON light on ONU for past 2 hours."
        },
        headers=headers
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["escalated"] is True
    ticket_id = data["ticket_id"]

    # Verify SupportTicket was created
    with app.app_context():
        ticket = appmod.db.session.get(appmod.SupportTicket, ticket_id)
        assert ticket is not None
        assert ticket.priority == "high"
        assert ticket.status == "open"
        assert "Repeated internet dropouts" in ticket.title


def test_multi_tenant_isolation(app, client):
    """A caller from Tenant A cannot see or lookup customers from Tenant B."""
    headers_a = auth_headers(client, "tenant_a_admin", "pw123")
    with app.app_context():
        tenant_a = appmod.Tenant.query.filter_by(name="tenant_a_admin").first()
        _setup_customer(app, tenant_a.id, "Customer In Tenant A", "70111111")

    headers_b = auth_headers(client, "tenant_b_admin", "pw123")
    with app.app_context():
        tenant_b = appmod.Tenant.query.filter_by(name="tenant_b_admin").first()
        cust_b_id, _ = _setup_customer(app, tenant_b.id, "Customer In Tenant B", "70222222")

    # Tenant A looking up Tenant B's customer -> Not found
    res_a = client.get("/api/cs-agent/tools/lookup-customer?phone=70222222", headers=headers_a)
    assert res_a.status_code == 200
    assert res_a.get_json()["found"] is False

    # Tenant A querying Tenant B's customer status -> 404
    res_a_stat = client.get(f"/api/cs-agent/tools/customer-status?customer_id={cust_b_id}", headers=headers_a)
    assert res_a_stat.status_code == 404
