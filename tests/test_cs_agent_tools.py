"""Automated tests for Customer Service AI Agent tool endpoints.

Tests phone normalization, customer lookup, account status,
network diagnostics relaying, payment links, human escalation,
and multi-tenant boundary isolation.
"""
from datetime import datetime, timedelta
import pytest
import app as appmod
from tests.conftest import auth_headers


def _setup_customer(app, tenant_id, name="Georges Khoury", phone="70123456", balance=-25.0):
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
    """GET /api/cs-agent/config returns agent settings with env fallback, and POST saves per-tenant ID."""
    app.config["ELEVENLABS_AGENT_ID"] = "agent_test_123"
    
    # 1. Without tenant settings, falls back to env var
    res = client.get("/api/cs-agent/config")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["elevenlabs_agent_id"] == "agent_test_123"
    assert "agent_test_123" in data["ws_url"]

    # 2. Authenticated tenant saves custom agent ID
    headers = auth_headers(client, "admin_agent_config", "pw123")
    save_res = client.post(
        "/api/cs-agent/config",
        json={"elevenlabs_agent_id": "tenant_custom_agent_999"},
        headers=headers
    )
    assert save_res.status_code == 200
    save_data = save_res.get_json()
    assert save_data["status"] == "ok"
    assert save_data["settings"]["elevenlabs_agent_id"] == "tenant_custom_agent_999"

    # 3. GET now returns tenant-specific ID instead of env fallback
    get_res = client.get("/api/cs-agent/config", headers=headers)
    assert get_res.status_code == 200
    get_data = get_res.get_json()
    assert get_data["elevenlabs_agent_id"] == "tenant_custom_agent_999"
    assert "tenant_custom_agent_999" in get_data["ws_url"]


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
    assert good_res.get_json()["found"] is True

    # 3. With 'phone_number' query param instead of 'phone' -> 200
    alias_res = client.get(
        f"/api/cs-agent/tools/lookup-customer?phone_number=03112233&tenant_id={tenant.id}",
        headers={"X-CS-Agent-Secret": "secret_agent_token_xyz"}
    )
    assert alias_res.status_code == 200
    data = alias_res.get_json()
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
        cust_id, _ = _setup_customer(app, tenant.id, "Michel Aoun", "71111222", balance=-35.5)

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
        cust_id, _ = _setup_customer(app, tenant.id, "Rami Zein", "76123456", balance=-40.0)

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


def test_network_diagnostic_voice_safety_expired(app, client):
    """Network diagnostic on expired subscription returns success=True immediately with clear Arabic reason."""
    import cs_agent_tools
    headers = auth_headers(client, "admin_test_expired", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="admin_test_expired").first()
        cust_id, _ = _setup_customer(app, tenant.id, "Expired Customer", "70333333")
        cust = appmod.db.session.get(appmod.Customer, cust_id)
        cust.subscription_expiry_date = datetime.utcnow() - timedelta(days=2)
        cust.is_subscription_active = False
        appmod.db.session.commit()

        diag = cs_agent_tools.network_diagnostic(appmod, tenant.id, cust_id, wait_seconds=1)
        assert diag["success"] is True
        assert diag["status"] == "subscription_expired"
        assert "منتهي الصلاحية" in diag["diagnosis_ar"]


def test_process_customer_message_ai_promise_to_pay(app, client):
    """Verifies AI recognizes Lebanese promise to pay voice/text message and acknowledges gracefully."""
    import cs_agent_tools
    headers = auth_headers(client, "admin_test_ptp", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="admin_test_ptp").first()
        cust_id, _ = _setup_customer(app, tenant.id, "Ahmad Salloum", "71315744", balance=-25.0)
        cust = appmod.db.session.get(appmod.Customer, cust_id)

        user_msg = "اهلا حبيب , اي بس طول بالك عليي هلق كم يوم لأن الوضع منو منيح بس يصير معي انا بحكيك"
        res = cs_agent_tools.process_customer_message_ai(appmod, tenant.id, cust, user_msg)
        assert res["intent"] == "promise_to_pay"
        assert "طول بالك" in user_msg
        assert "تكرم عينك" in res["reply_text"]
        assert "وعد بالدفع" in res["ticket_tag"]


def test_process_customer_message_ai_balance_inquiry(app, client):
    """Verifies AI informs customer of their positive due balance and provides Whish payment option."""
    import cs_agent_tools
    headers = auth_headers(client, "admin_test_bal", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="admin_test_bal").first()
        cust_id, _ = _setup_customer(app, tenant.id, "Kareem Haddad", "71888999", balance=-30.0)
        cust = appmod.db.session.get(appmod.Customer, cust_id)

        user_msg = "مرحبا بدي اعرف قديش في عليي رصيد للفاتورة"
        res = cs_agent_tools.process_customer_message_ai(appmod, tenant.id, cust, user_msg)
        assert res["intent"] == "balance_inquiry"
        assert "30.00" in res["reply_text"]
        assert "Whish" in res["reply_text"]


def test_handle_whatsapp_cs_ai_reply_logging(app, client, monkeypatch):
    """handle_whatsapp_cs_ai_reply sends message via Meta API and logs session in DB."""
    import cs_agent_tools
    from unittest.mock import MagicMock

    class MockResponse:
        ok = True
        status_code = 200
        text = '{"messages": [{"id": "wamid.123"}]}'
        def json(self):
            return {"messages": [{"id": "wamid.123"}]}

    monkeypatch.setattr(cs_agent_tools.requests, "post", lambda *a, **kw: MockResponse())

    headers = auth_headers(client, "admin_test_log", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="admin_test_log").first()
        cust_id, _ = _setup_customer(app, tenant.id, "Salim Nassar", "70777888", balance=-15.0)
        cust = appmod.db.session.get(appmod.Customer, cust_id)

        settings = MagicMock()
        settings.access_token = "mock_token"
        settings.phone_number_id = "mock_phone_id"
        settings.api_version = "v19.0"

        ai_res = cs_agent_tools.handle_whatsapp_cs_ai_reply(
            appmod,
            tenant.id,
            "96170777888",
            cust,
            "بدي اعرف شو عليي رصيد",
            is_voice=False,
            settings=settings
        )

        assert ai_res is not None
        assert ai_res["intent"] == "balance_inquiry"

        # Check CSAgentSession created
        session = appmod.CSAgentSession.query.filter_by(
            tenant_id=tenant.id,
            channel="whatsapp",
            caller_identifier="96170777888"
        ).first()
        assert session is not None

        # Check CSAgentMessageLog created
        logs = appmod.CSAgentMessageLog.query.filter_by(session_id=session.id).all()
        assert len(logs) >= 2  # in and out logs


def test_elevenlabs_agent_config_sync(app, monkeypatch):
    """Verifies get_elevenlabs_agent_config reads voice_id and first_message directly from ElevenLabs."""
    import cs_agent_tools
    from unittest.mock import MagicMock

    # Reset cache
    cs_agent_tools._CACHED_AGENT_CONFIG = {}
    cs_agent_tools._CACHED_AGENT_CONFIG_TIME = 0

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "agent_id": "agent_test_yara",
        "name": "yara",
        "conversation_config": {
            "agent": {
                "first_message": "مرحبا! معك يارا من دلتانت كيف بقدر ساعدك؟"
            },
            "tts": {
                "voice_id": "albaa6OioIhKtKdCEkQw",
                "model_id": "eleven_turbo_v2_5"
            }
        }
    }

    monkeypatch.setattr(cs_agent_tools.requests, "get", lambda *a, **kw: mock_resp)

    cfg = cs_agent_tools.get_elevenlabs_agent_config(api_key="mock_key", agent_id="agent_test_yara")
    assert cfg["name"] == "yara"
    assert cfg["voice_id"] == "albaa6OioIhKtKdCEkQw"
    assert cfg["model_id"] == "eleven_turbo_v2_5"
    assert "يارا" in cfg["first_message"]

    # Effective voice id uses agent's voice
    effective_voice = cs_agent_tools.get_effective_elevenlabs_voice_id(api_key="mock_key", agent_id="agent_test_yara")
    assert effective_voice == "albaa6OioIhKtKdCEkQw"


def test_process_customer_message_ai_greeting_uses_yara(app, client):
    """Greetings return concise Yara persona instead of long rigid menus."""
    import cs_agent_tools
    # Reset cache to test fallback greeting
    cs_agent_tools._CACHED_AGENT_CONFIG = {}
    cs_agent_tools._CACHED_AGENT_CONFIG_TIME = 0

    headers = auth_headers(client, "admin_test_yara", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="admin_test_yara").first()
        cust_id, _ = _setup_customer(app, tenant.id, "Nour Kassir", "71444555")
        cust = appmod.db.session.get(appmod.Customer, cust_id)

        # 1. Greeting message (مرحبا)
        res = cs_agent_tools.process_customer_message_ai(appmod, tenant.id, cust, "مرحبا يعطيكم العافية")
        assert res["intent"] in ["greeting", "courtesy"]
        assert "يارا" in res["reply_text"]
        assert "•" not in res["reply_text"]  # No long bulleted menu

        # 2. General unknown query fallback
        res_fb = cs_agent_tools.process_customer_message_ai(appmod, tenant.id, cust, "شو الاخبار اليوم")
        assert res_fb["intent"] == "general"
        assert "يارا" in res_fb["reply_text"]
        assert "•" not in res_fb["reply_text"]  # Concise friendly message


