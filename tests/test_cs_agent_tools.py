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
    """GET /api/cs-agent/config requires a real JWT (no more query/env-based
    tenant resolution -- see the memory-endpoint auth fix), and once
    authenticated returns agent settings with env fallback; POST saves
    per-tenant ID."""
    app.config["ELEVENLABS_AGENT_ID"] = "agent_test_123"

    # 1. Unauthenticated GET is rejected -- no tenant_id, no config leak.
    res = client.get("/api/cs-agent/config")
    assert res.status_code == 401

    # 2. Authenticated tenant saves custom agent ID
    headers = auth_headers(client, "admin_agent_config", "pw123")

    # Without tenant settings yet, does NOT fall back to global config/env var (strictly per-tenant)
    res = client.get("/api/cs-agent/config", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["elevenlabs_agent_id"] == ""
    assert data["has_agent_id"] is False
    assert data["has_elevenlabs_key"] is False
    assert data["ws_url"] is None

    save_res = client.post(
        "/api/cs-agent/config",
        json={
            "elevenlabs_agent_id": "tenant_custom_agent_999",
            "elevenlabs_api_key": "el_key_1234567890abcdef"
        },
        headers=headers
    )
    assert save_res.status_code == 200
    save_data = save_res.get_json()
    assert save_data["status"] == "ok"
    assert save_data["settings"]["elevenlabs_agent_id"] == "tenant_custom_agent_999"
    assert "..." in save_data["settings"]["elevenlabs_api_key"]

    # 3. GET now returns tenant-specific ID and masked key
    get_res = client.get("/api/cs-agent/config", headers=headers)
    assert get_res.status_code == 200
    get_data = get_res.get_json()
    assert get_data["elevenlabs_agent_id"] == "tenant_custom_agent_999"
    assert get_data["has_agent_id"] is True
    assert get_data["has_elevenlabs_key"] is True
    assert "..." in get_data["elevenlabs_api_key"]
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
    cs_agent_tools._CACHED_AGENT_CONFIG_TIME = {}

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
        res_fb = cs_agent_tools.process_customer_message_ai(appmod, tenant.id, cust, "عندي استفسار عن موضوع تاني خالص")
        assert res_fb["intent"] == "general"
        assert "يارا" in res_fb["reply_text"]
        assert "•" not in res_fb["reply_text"]  # Concise friendly message

        # 3. Ongoing session greeting does not repeat initial first_message
        res_ongoing = cs_agent_tools.process_customer_message_ai(
            appmod, tenant.id, cust, "مرحبا", is_new_session=False
        )
        assert res_ongoing["intent"] == "greeting"
        assert "كفي مساعدتك" in res_ongoing["reply_text"] or "تفضل" in res_ongoing["reply_text"]


def test_whatsapp_ai_reply_no_unwanted_tickets(app, client, monkeypatch):
    """Verifies that routine AI conversation does NOT create SupportTickets, but escalation does."""
    import cs_agent_tools
    from unittest.mock import MagicMock

    class MockResponse:
        ok = True
        status_code = 200
        text = '{"messages": [{"id": "wamid.mock"}]}'
        def json(self):
            return {"messages": [{"id": "wamid.mock"}]}

    monkeypatch.setattr(cs_agent_tools.requests, "post", lambda *a, **kw: MockResponse())

    headers = auth_headers(client, "admin_test_tickets", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="admin_test_tickets").first()
        cust_id, _ = _setup_customer(app, tenant.id, "Tarek Ziad", "70666555", balance=-20.0)
        cust = appmod.db.session.get(appmod.Customer, cust_id)

        settings = MagicMock()
        settings.access_token = "mock_token"
        settings.phone_number_id = "mock_phone_id"
        settings.api_version = "v19.0"

        # 1. Routine greeting: should NOT create any SupportTicket
        initial_ticket_count = appmod.SupportTicket.query.filter_by(tenant_id=tenant.id).count()
        cs_agent_tools.handle_whatsapp_cs_ai_reply(
            appmod, tenant.id, "96170666555", cust, "مرحبا صباح الخير", is_voice=False, settings=settings
        )
        after_greeting_count = appmod.SupportTicket.query.filter_by(tenant_id=tenant.id).count()
        assert after_greeting_count == initial_ticket_count

        # 2. Routine balance inquiry: should NOT create any SupportTicket
        cs_agent_tools.handle_whatsapp_cs_ai_reply(
            appmod, tenant.id, "96170666555", cust, "قديش في عليي رصيد", is_voice=False, settings=settings
        )
        after_bal_count = appmod.SupportTicket.query.filter_by(tenant_id=tenant.id).count()
        assert after_bal_count == initial_ticket_count

        # 3. Explicit escalation: SHOULD create a SupportTicket via escalate_to_human
        esc_res = cs_agent_tools.handle_whatsapp_cs_ai_reply(
            appmod, tenant.id, "96170666555", cust, "بدي احكي مع موظف الدعم ضروري", is_voice=False, settings=settings
        )
        assert esc_res["intent"] == "escalate"
        after_esc_count = appmod.SupportTicket.query.filter_by(tenant_id=tenant.id).count()
        assert after_esc_count == initial_ticket_count + 1
        
        # Verify ticket details
        created_ticket = appmod.SupportTicket.query.filter_by(tenant_id=tenant.id).order_by(appmod.SupportTicket.id.desc()).first()
        assert created_ticket.priority == "high"
        assert "مساعد الذكاء الاصطناعي" in created_ticket.title


def test_query_elevenlabs_conversational_ai_success(monkeypatch):
    """query_elevenlabs_conversational_ai performs WebSocket handshake and returns cleaned reply."""
    import cs_agent_tools
    import json

    class FakeWebSocket:
        def __init__(self):
            self.sent_messages = []
            self._messages_to_recv = [
                json.dumps({"type": "conversation_initiation_metadata"}),
                json.dumps({"type": "agent_response", "agent_response_event": {"agent_response": "[warmly] مرحبا انا يارا"}}),
                json.dumps({"type": "ping", "ping_event": {"event_id": 42}}),
                json.dumps({"type": "agent_response", "agent_response_event": {"agent_response": "[ودود] تكرم عينك، اشتراكك شغال وباقي 7 ايام."}})
            ]
            self.closed = False

        def send(self, msg):
            self.sent_messages.append(json.loads(msg))

        def recv(self):
            if self._messages_to_recv:
                return self._messages_to_recv.pop(0)
            raise TimeoutError("No more messages")

        def settimeout(self, timeout):
            pass

        def close(self):
            self.closed = True

    fake_ws = FakeWebSocket()
    import websocket
    monkeypatch.setattr(websocket, "create_connection", lambda url, timeout=10: fake_ws)

    class FakeCustomer:
        id = 99
        name = "Hasan Salloum"

    res = cs_agent_tools.query_elevenlabs_conversational_ai(
        agent_id="agent_mock_test",
        incoming_text="بدي اعرف كم يوم باقي لاشتراكي",
        sender_phone="96171315744",
        customer=FakeCustomer(),
        recent_history=[{"direction": "in", "transcript": "مرحبا"}, {"direction": "out", "transcript": "اهلين وسهلين"}]
    )

    assert res is not None
    assert res["intent"] == "elevenlabs_convai"
    assert res["reply_text"] == "تكرم عينك، اشتراكك شغال وباقي 7 ايام."
    assert fake_ws.closed is True

    # Verify messages sent over WebSocket
    assert len(fake_ws.sent_messages) >= 3
    # 1. initiation client data
    assert fake_ws.sent_messages[0]["type"] == "conversation_initiation_client_data"
    dyn_vars = fake_ws.sent_messages[0]["conversation_initiation_client_data_event"]["dynamic_variables"]
    assert dyn_vars["customer_name"] == "Hasan Salloum"
    # customer_id is deliberately NOT sent (see the NOTE in
    # query_elevenlabs_conversational_ai): including it made ElevenLabs
    # auto-fire customer-status/network-diagnostic tools on every message.
    assert "customer_id" not in dyn_vars
    # 2. pong
    pong_msgs = [m for m in fake_ws.sent_messages if m.get("type") == "pong"]
    assert len(pong_msgs) == 1
    assert pong_msgs[0]["event_id"] == 42
    # 3. user_message with history and caller info
    user_msgs = [m for m in fake_ws.sent_messages if m.get("type") == "user_message"]
    assert len(user_msgs) == 1
    assert "Hasan Salloum" in user_msgs[0]["text"]
    assert "اهلين وسهلين" in user_msgs[0]["text"]


def test_query_elevenlabs_conversational_ai_timeout_fallback(monkeypatch):
    """query_elevenlabs_conversational_ai returns None on connection or socket error."""
    import cs_agent_tools
    import websocket

    def _raise_error(*a, **kw):
        raise ConnectionRefusedError("ElevenLabs ConvAI endpoint down")

    monkeypatch.setattr(websocket, "create_connection", _raise_error)

    res = cs_agent_tools.query_elevenlabs_conversational_ai(
        agent_id="agent_mock_test",
        incoming_text="مرحبا",
        sender_phone="96171315744"
    )
    assert res is None


def test_handle_whatsapp_cs_ai_reply_with_gemini(app, client, monkeypatch):
    """handle_whatsapp_cs_ai_reply uses the Gemini brain reply when available
    (tenant has a gemini_api_key configured) and falls back gracefully when
    Gemini returns None. Supersedes the old ElevenLabs-ConvAI-in-WhatsApp-path
    test now that Task 6 has replaced that wiring with query_gemini_agent."""
    import cs_agent_tools
    from unittest.mock import MagicMock

    sent_payloads = []
    class MockResponse:
        ok = True
        status_code = 200
        text = '{"messages": [{"id": "wamid.mock"}]}'
        def json(self):
            return {"messages": [{"id": "wamid.mock"}]}

    def mock_post(url, *args, **kwargs):
        json_data = kwargs.get("json", {})
        sent_payloads.append((url, json_data))
        return MockResponse()

    monkeypatch.setattr(cs_agent_tools.requests, "post", mock_post)

    headers = auth_headers(client, "admin_test_convai", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="admin_test_convai").first()
        cust_id, _ = _setup_customer(app, tenant.id, "Nour Al-Hassan", "71112233", balance=0.0)
        cust = appmod.db.session.get(appmod.Customer, cust_id)

        cs_settings = appmod.CSAgentSettings(tenant_id=tenant.id, gemini_api_key="fake-gemini-key")
        appmod.db.session.add(cs_settings)
        appmod.db.session.commit()

        settings = MagicMock()
        settings.access_token = "mock_token"
        settings.phone_number_id = "mock_phone_id"
        settings.elevenlabs_agent_id = "agent_convai_123"
        settings.api_version = "v19.0"

        # Case 1: Gemini brain succeeds
        monkeypatch.setattr(
            cs_agent_tools,
            "query_gemini_agent",
            lambda *a, **kw: {"intent": "gemini_agent", "reply_text": "اهلاً يا نور، كيف فيني ساعدك اليوم؟", "ticket_tag": "محادثة ذكاء اصطناعي", "escalate": False}
        )

        res = cs_agent_tools.handle_whatsapp_cs_ai_reply(
            appmod, tenant.id, "96171112233", cust, "مرحبا يارا", is_voice=False, settings=settings
        )
        assert res["intent"] == "gemini_agent"
        assert res["reply_text"] == "اهلاً يا نور، كيف فيني ساعدك اليوم؟"

        # Verify sent WhatsApp payload contains Yara's response
        text_payloads = [p for u, p in sent_payloads if p.get("type") == "text"]
        assert any("اهلاً يا نور" in p["text"]["body"] for p in text_payloads)

        # Case 2: Gemini brain returns None -> graceful fallback to rule-based processor
        monkeypatch.setattr(cs_agent_tools, "query_gemini_agent", lambda *a, **kw: None)

        res_fallback = cs_agent_tools.handle_whatsapp_cs_ai_reply(
            appmod, tenant.id, "96171112233", cust, "قديش في عليي رصيد", is_voice=False, settings=settings
        )
        assert res_fallback is not None
        assert res_fallback["intent"] == "balance_inquiry"
        assert "حسابك خالص" in res_fallback["reply_text"]


def test_escalate_to_human_sends_customer_reply_alert_template(app, client, monkeypatch):
    """escalate_to_human creates a ticket and sends customer_reply_alert template to forwarding_mobile."""
    import cs_agent_tools

    sent_templates = []
    class MockResponse:
        ok = True
        status_code = 200
        text = '{"messages": [{"id": "wamid.mock.escalate"}]}'
        def json(self):
            return {"messages": [{"id": "wamid.mock.escalate"}]}

    def mock_post(url, *args, **kwargs):
        json_data = kwargs.get("json", {})
        sent_templates.append((url, json_data))
        return MockResponse()

    monkeypatch.setattr(cs_agent_tools.requests, "post", mock_post)

    headers = auth_headers(client, "admin_test_esc_tmpl", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="admin_test_esc_tmpl").first()
        cust_id, _ = _setup_customer(app, tenant.id, "Hasan Salloum", "71315744", balance=0.0)

        # Configure WhatsAppSettings with forwarding_mobile
        ws_settings = appmod.WhatsAppSettings.query.filter_by(tenant_id=tenant.id).first()
        if not ws_settings:
            ws_settings = appmod.WhatsAppSettings(tenant_id=tenant.id)
            appmod.db.session.add(ws_settings)
        ws_settings.access_token = "test_meta_token"
        ws_settings.phone_number_id = "1176220222243847"
        ws_settings.forwarding_mobile = "+9613261036"
        ws_settings.template_forward_alert = "customer_reply_alert"
        ws_settings.template_language = "ar"
        appmod.db.session.commit()

        res = cs_agent_tools.escalate_to_human(
            appmod=appmod,
            tenant_id=tenant.id,
            customer_id=cust_id,
            reason="عطل في الراوتر",
            summary="الضو الاحمر شغال والنت فاصل",
            phone="71315744"
        )

        assert res["success"] is True
        assert res["escalated"] is True
        assert res["alert_sent"] is True
        assert res["forwarded_to"] == "9613261036"

        # Verify template payload was dispatched to Meta API
        assert len(sent_templates) >= 1
        url, payload = sent_templates[0]
        assert "1176220222243847/messages" in url
        assert payload["messaging_product"] == "whatsapp"
        assert payload["to"] == "9613261036"
        assert payload["type"] == "template"
        assert payload["template"]["name"] == "customer_reply_alert"
        assert payload["template"]["language"]["code"] == "ar"

        # Check template components
        body_params = payload["template"]["components"][0]["parameters"]
        param_texts = [p["text"] for p in body_params]
        assert any("Hasan Salloum" in t for t in param_texts)
        assert any("عطل في الراوتر" in t for t in param_texts)


def test_cs_agent_settings_gemini_fields_round_trip(app, client):
    """CSAgentSettings persists and returns gemini_api_key / gemini_model."""
    auth_headers(client, "admin_gemini_fields", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        settings = appmod.CSAgentSettings(tenant_id=tenant.id)
        settings.gemini_api_key = 'AIzaTestKey123'
        settings.gemini_model = 'gemini-2.5-flash-lite'
        appmod.db.session.add(settings)
        appmod.db.session.commit()

        reloaded = appmod.CSAgentSettings.query.filter_by(tenant_id=tenant.id).first()
        assert reloaded.gemini_api_key == 'AIzaTestKey123'
        assert reloaded.to_dict()['gemini_model'] == 'gemini-2.5-flash-lite'


def test_cs_agent_config_saves_gemini_key(app, client):
    """POST /api/cs-agent/config saves a tenant's Gemini API key and model.
    Neither the POST response nor a subsequent GET ever echoes the raw key
    back -- only a masked form plus has_gemini_key, even though the real
    value is what's persisted to the database."""
    headers = auth_headers(client, "admin_gemini_config", "pw123")
    raw_key = "AIzaSyTestKeyForTenant"

    save_res = client.post(
        "/api/cs-agent/config",
        json={
            "elevenlabs_agent_id": "agent_keep_existing",
            "gemini_api_key": raw_key,
            "gemini_model": "gemini-2.5-flash-lite"
        },
        headers=headers
    )
    assert save_res.status_code == 200
    saved_masked = save_res.get_json()["settings"]["gemini_api_key"]
    assert saved_masked != raw_key
    assert raw_key not in save_res.get_data(as_text=True)
    assert saved_masked.startswith("AIzaSy") and "..." in saved_masked

    get_res = client.get("/api/cs-agent/config", headers=headers)
    assert get_res.status_code == 200
    data = get_res.get_json()
    assert data["gemini_api_key"] != raw_key
    assert raw_key not in get_res.get_data(as_text=True)
    assert data["gemini_api_key"].startswith("AIzaSy") and "..." in data["gemini_api_key"]
    assert data["gemini_model"] == "gemini-2.5-flash-lite"
    assert data["has_gemini_key"] is True

    # The real key is still what's actually persisted and usable server-side.
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        settings = appmod.CSAgentSettings.query.filter_by(tenant_id=tenant.id).first()
        assert settings.gemini_api_key == raw_key


def test_cs_agent_config_no_gemini_key_by_default(app, client):
    """A tenant that never set a Gemini key gets has_gemini_key: False, never another tenant's key."""
    headers = auth_headers(client, "admin_no_gemini", "pw123")
    get_res = client.get("/api/cs-agent/config", headers=headers)
    data = get_res.get_json()
    assert data["gemini_api_key"] == ""
    assert data["has_gemini_key"] is False


def test_cs_agent_knowledge_entry_tenant_scoped(app, client):
    """A CSAgentKnowledgeEntry belongs to exactly one tenant and round-trips."""
    auth_headers(client, "admin_knowledge_scoped", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        entry = appmod.CSAgentKnowledgeEntry(
            tenant_id=tenant.id,
            question_text='شو بواقي اشتراكي؟',
            answer_text='بواقيك 25 دولار، تنتهي بعد 20 يوم.',
            source='manual'
        )
        appmod.db.session.add(entry)
        appmod.db.session.commit()

        reloaded = appmod.CSAgentKnowledgeEntry.query.filter_by(tenant_id=tenant.id).first()
        assert reloaded.question_text == 'شو بواقي اشتراكي؟'
        assert reloaded.is_active is True
        assert reloaded.to_dict()['source'] == 'manual'


def test_search_knowledge_entries_keyword_match_and_tenant_isolation(app, client):
    """Keyword search finds a relevant entry for the right tenant, and never for another tenant."""
    import cs_agent_tools

    auth_headers(client, "admin_knowledge_t1", "pw123")
    with app.app_context():
        t1_id = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first().id

    auth_headers(client, "admin_knowledge_t2", "pw123")
    with app.app_context():
        t2_id = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first().id
        assert t2_id != t1_id

        appmod.db.session.add(appmod.CSAgentKnowledgeEntry(
            tenant_id=t1_id, question_text="كيف بدي جدد اشتراكي؟", answer_text="ابعتلك رابط دفع فوراً."
        ))
        appmod.db.session.add(appmod.CSAgentKnowledgeEntry(
            tenant_id=t2_id, question_text="كيف بدي جدد اشتراكي؟", answer_text="جواب تينانت تاني ما لازم يظهر."
        ))
        appmod.db.session.commit()

        results = cs_agent_tools.search_knowledge_entries(appmod, t1_id, "بدي جدد اشتراكي شو بعمل", limit=5)
        assert len(results) == 1
        assert results[0].answer_text == "ابعتلك رابط دفع فوراً."


def test_search_knowledge_entries_no_match_returns_empty(app, client):
    """An unrelated question, or a tenant with zero entries, returns an empty list -- not an error."""
    import cs_agent_tools
    auth_headers(client, "admin_knowledge_empty", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        results = cs_agent_tools.search_knowledge_entries(appmod, tenant.id, "شي غير موجود إطلاقاً", limit=5)
        assert results == []


def test_add_list_and_deactivate_knowledge_entry(app, client):
    """Manual add, list, and deactivate round-trip correctly."""
    import cs_agent_tools
    auth_headers(client, "admin_knowledge_crud", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        entry = cs_agent_tools.add_knowledge_entry(
            appmod, tenant.id, "شو أوقات الدعم الفني؟", "من 9 الصبح لـ 9 الليل كل يوم."
        )
        assert entry.id is not None

        entries = cs_agent_tools.list_knowledge_entries(appmod, tenant.id)
        assert any(e.id == entry.id for e in entries)

        ok = cs_agent_tools.set_knowledge_entry_active(appmod, tenant.id, entry.id, False)
        assert ok is True
        reloaded = appmod.CSAgentKnowledgeEntry.query.get(entry.id)
        assert reloaded.is_active is False

        # Deactivated entries never come back from search
        results = cs_agent_tools.search_knowledge_entries(appmod, tenant.id, "شو أوقات الدعم الفني", limit=5)
        assert results == []


def test_memory_endpoints_crud(app, client):
    """POST creates, GET lists, PUT deactivates, DELETE removes -- all tenant-scoped."""
    headers = auth_headers(client, "admin_memory_crud", "pw123")

    create_res = client.post(
        "/api/cs-agent/memory",
        json={"question_text": "شو بدل التركيب؟", "answer_text": "50 دولار تركيب لمرة وحدة."},
        headers=headers
    )
    assert create_res.status_code == 200
    entry_id = create_res.get_json()["entry"]["id"]
    assert create_res.get_json()["entry"]["source"] == "manual"

    list_res = client.get("/api/cs-agent/memory", headers=headers)
    assert list_res.status_code == 200
    assert any(e["id"] == entry_id for e in list_res.get_json()["entries"])

    deactivate_res = client.put(
        f"/api/cs-agent/memory/{entry_id}", json={"is_active": False}, headers=headers
    )
    assert deactivate_res.status_code == 200

    delete_res = client.delete(f"/api/cs-agent/memory/{entry_id}", headers=headers)
    assert delete_res.status_code == 200

    list_after = client.get("/api/cs-agent/memory", headers=headers)
    assert not any(e["id"] == entry_id for e in list_after.get_json()["entries"])


def test_memory_endpoints_are_tenant_isolated(app, client):
    """A tenant can't see, edit, or delete another tenant's memory entries."""
    headers_a = auth_headers(client, "admin_memory_a", "pw123")
    create_res = client.post(
        "/api/cs-agent/memory",
        json={"question_text": "سؤال تينانت A", "answer_text": "جواب تينانت A"},
        headers=headers_a
    )
    entry_id = create_res.get_json()["entry"]["id"]

    headers_b = auth_headers(client, "admin_memory_b", "pw123")
    list_res_b = client.get("/api/cs-agent/memory", headers=headers_b)
    assert not any(e["id"] == entry_id for e in list_res_b.get_json()["entries"])

    # Tenant B can't deactivate or delete tenant A's entry
    put_res = client.put(f"/api/cs-agent/memory/{entry_id}", json={"is_active": False}, headers=headers_b)
    assert put_res.status_code == 404
    delete_res = client.delete(f"/api/cs-agent/memory/{entry_id}", headers=headers_b)
    assert delete_res.status_code == 404


def test_memory_recent_logs_endpoint(app, client):
    """GET /api/cs-agent/memory/recent-logs returns this tenant's recent CS agent message logs."""
    headers = auth_headers(client, "admin_recent_logs", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        appmod.db.session.add(appmod.CSAgentMessageLog(
            tenant_id=tenant.id, direction='in', transcript='شو رصيدي؟'
        ))
        appmod.db.session.add(appmod.CSAgentMessageLog(
            tenant_id=tenant.id, direction='out', transcript='رصيدك 25 دولار.'
        ))
        appmod.db.session.commit()

    res = client.get("/api/cs-agent/memory/recent-logs", headers=headers)
    assert res.status_code == 200
    logs = res.get_json()["logs"]
    assert len(logs) >= 2
    assert any(l["transcript"] == 'شو رصيدي؟' for l in logs)


def test_memory_endpoint_creates_entry_with_authenticated_user_id(app, client):
    """POST /api/cs-agent/memory with JWT auth sets created_by_id to the authenticated user's ID."""
    username = "admin_memory_user_id"
    password = "pw123"
    headers = auth_headers(client, username, password)

    with app.app_context():
        # Get the authenticated user's ID
        user = appmod.User.query.filter_by(username=username).first()
        assert user is not None
        expected_user_id = user.id

    # POST to create a memory entry with JWT authentication
    create_res = client.post(
        "/api/cs-agent/memory",
        json={"question_text": "شو بدل التركيب؟", "answer_text": "50 دولار تركيب لمرة وحدة."},
        headers=headers
    )
    assert create_res.status_code == 200
    entry_data = create_res.get_json()["entry"]
    entry_id = entry_data["id"]

    # Verify created_by_id is set to the authenticated user's ID
    assert entry_data["created_by_id"] == expected_user_id

    # Verify in database that created_by_id is persisted
    with app.app_context():
        entry = appmod.CSAgentKnowledgeEntry.query.get(entry_id)
        assert entry is not None
        assert entry.created_by_id == expected_user_id


def test_memory_endpoints_reject_cs_agent_secret_without_jwt(app, client):
    """Unlike the tool-webhook endpoints, the memory CRUD endpoints are
    admin-UI-only: they must reject the CS_AGENT_SECRET / body-tenant_id path
    resolve_tenant_id() otherwise allows, and require a real JWT. A request
    that only presents the shared secret (no bearer token) must get 401, not
    silently resolve to whatever tenant_id it named in the body -- otherwise
    an unauthenticated caller who knows (or guesses) CS_AGENT_SECRET, or any
    caller at all when it's unset (its production default), could read or
    plant "known answers" in any tenant's Gemini system prompt."""
    app.config["CS_AGENT_SECRET"] = "test_secret_key_xyz"

    auth_headers(client, "admin_memory_no_jwt", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="admin_memory_no_jwt").first()
        assert tenant is not None
        tenant_id = tenant.id

    # POST with only the agent secret (no JWT) must be rejected.
    create_res = client.post(
        "/api/cs-agent/memory",
        json={
            "question_text": "سؤال بدون تحقق",
            "answer_text": "جواب بدون تحقق",
            "tenant_id": tenant_id
        },
        headers={"X-CS-Agent-Secret": "test_secret_key_xyz"}
    )
    assert create_res.status_code == 401

    # Same for GET (list), PUT, and DELETE -- no JWT, no access.
    assert client.get("/api/cs-agent/memory", headers={"X-CS-Agent-Secret": "test_secret_key_xyz"}).status_code == 401
    assert client.get("/api/cs-agent/memory/recent-logs", headers={"X-CS-Agent-Secret": "test_secret_key_xyz"}).status_code == 401
    assert client.put(
        "/api/cs-agent/memory/1", json={"is_active": False},
        headers={"X-CS-Agent-Secret": "test_secret_key_xyz"}
    ).status_code == 401
    assert client.delete(
        "/api/cs-agent/memory/1", headers={"X-CS-Agent-Secret": "test_secret_key_xyz"}
    ).status_code == 401


def test_memory_endpoints_reject_no_auth_at_all(app, client):
    """With CS_AGENT_SECRET unset (its production default -- see config.py;
    forced empty here too, since app.config is process-global and another
    test in this file may have set it), resolve_tenant_id() would otherwise
    trust a bare tenant_id from the request with no authentication
    whatsoever. The memory endpoints must still require a JWT."""
    app.config["CS_AGENT_SECRET"] = ""
    assert client.get("/api/cs-agent/memory").status_code == 401
    assert client.post(
        "/api/cs-agent/memory",
        json={"question_text": "q", "answer_text": "a", "tenant_id": 1}
    ).status_code == 401


def test_whatsapp_reply_uses_gemini_when_tenant_key_configured(app, client):
    """handle_whatsapp_cs_ai_reply calls query_gemini_agent (not ElevenLabs
    ConvAI) when the tenant has a gemini_api_key configured."""
    import cs_agent_tools
    from unittest.mock import patch, MagicMock

    auth_headers(client, "admin_wa_gemini", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()
        settings = appmod.CSAgentSettings(tenant_id=tenant.id, gemini_api_key="fake-key")
        appmod.db.session.add(settings)

        wa_settings = MagicMock()
        wa_settings.access_token = "wa-token"
        wa_settings.phone_number_id = "123"
        wa_settings.api_version = "v19.0"
        appmod.db.session.commit()

        with patch("cs_agent_tools.query_gemini_agent") as mock_gemini, \
             patch("cs_agent_tools.query_elevenlabs_conversational_ai") as mock_convai, \
             patch("requests.post"):
            mock_gemini.return_value = {
                "intent": "gemini_agent", "reply_text": "جواب من جيميناي",
                "ticket_tag": "محادثة ذكاء اصطناعي", "escalate": False
            }
            cs_agent_tools.handle_whatsapp_cs_ai_reply(
                appmod, tenant.id, "70123456", None, "شو رصيدي؟",
                is_voice=False, settings=wa_settings
            )

        mock_gemini.assert_called_once()
        mock_convai.assert_not_called()


def test_whatsapp_reply_falls_back_to_rule_based_without_gemini_key(app, client):
    """With no gemini_api_key configured, query_gemini_agent is never called
    and the rule-based processor answers instead."""
    import cs_agent_tools
    from unittest.mock import patch, MagicMock

    auth_headers(client, "admin_wa_no_gemini", "pw123")
    with app.app_context():
        tenant = appmod.Tenant.query.order_by(appmod.Tenant.id.desc()).first()

        wa_settings = MagicMock()
        wa_settings.access_token = "wa-token"
        wa_settings.phone_number_id = "123"
        wa_settings.api_version = "v19.0"

        with patch("cs_agent_tools.query_gemini_agent") as mock_gemini, \
             patch("requests.post"):
            cs_agent_tools.handle_whatsapp_cs_ai_reply(
                appmod, tenant.id, "70123456", None, "مرحبا",
                is_voice=False, settings=wa_settings
            )

        mock_gemini.assert_not_called()



# ---------------------------------------------------------------------------
# Handoff honesty guard in handle_whatsapp_cs_ai_reply
# ---------------------------------------------------------------------------

def _handoff_guard_env(app, client, monkeypatch, tenant_name, reply_text, escalate=False):
    """Tenant + customer + a stubbed Gemini brain that returns `reply_text`,
    with every outgoing Meta call captured instead of sent."""
    import cs_agent_tools
    from unittest.mock import MagicMock

    sent = []

    class _Ok:
        ok = True
        status_code = 200
        text = '{}'
        def json(self):
            return {}

    monkeypatch.setattr(cs_agent_tools.requests, "post", lambda url, *a, **kw: sent.append(kw.get("json", {})) or _Ok())
    monkeypatch.setattr(
        cs_agent_tools, "query_gemini_agent",
        lambda *a, **kw: {"intent": "gemini_agent", "reply_text": reply_text,
                          "ticket_tag": "محادثة ذكاء اصطناعي", "escalate": escalate}
    )

    auth_headers(client, tenant_name, "pw123")
    tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
    cust_id, _ = _setup_customer(app, tenant.id, "Mohammad Test", "71555666", balance=-50.0)
    appmod.db.session.add(appmod.CSAgentSettings(tenant_id=tenant.id, gemini_api_key="fake-gemini-key"))
    appmod.db.session.commit()

    settings = MagicMock()
    settings.access_token = "mock_token"
    settings.phone_number_id = "mock_phone_id"
    settings.elevenlabs_agent_id = None
    settings.api_version = "v19.0"
    return tenant, appmod.db.session.get(appmod.Customer, cust_id), settings, sent


def _ai_tickets(tenant_id):
    return appmod.SupportTicket.query.filter(
        appmod.SupportTicket.tenant_id == tenant_id,
        appmod.SupportTicket.title.like("[مساعد الذكاء الاصطناعي]%"),
    ).all()


def test_unbacked_handoff_claim_triggers_real_escalation(app, client, monkeypatch):
    """Reproduces production session 13 (2026-09-20): Gemini told the customer
    "حولت ملفك للقسم المالي" but never called escalate_to_human, so no ticket
    existed and nobody followed up. The claim must now be made true: a ticket
    is opened, the customer is given its number, and the result says so."""
    import cs_agent_tools
    claim = "اعتذر منك كتير، حولت ملفك للقسم المالي ليتحققوا من الوصل ويعدلوا الرصيد."
    with app.app_context():
        tenant, cust, settings, sent = _handoff_guard_env(app, client, monkeypatch, "admin_test_handoff_claim", claim)

        res = cs_agent_tools.handle_whatsapp_cs_ai_reply(
            appmod, tenant.id, "96171555666", cust, "أنا دافع 25 بس، ليش 50؟", settings=settings
        )

        tickets = _ai_tickets(tenant.id)
        assert len(tickets) == 1
        assert tickets[0].customer_id == cust.id
        assert tickets[0].priority == "high"
        assert res["escalate"] is True
        assert res["ticket_id"] == tickets[0].id
        body = [p for p in sent if p.get("type") == "text"][0]["text"]["body"]
        assert claim in body
        assert str(tickets[0].id) in body


def test_ordinary_reply_does_not_open_a_ticket(app, client, monkeypatch):
    import cs_agent_tools
    with app.app_context():
        tenant, cust, settings, _ = _handoff_guard_env(
            app, client, monkeypatch, "admin_test_handoff_plain", "أهلاً! رصيدك 50 دولار."
        )
        res = cs_agent_tools.handle_whatsapp_cs_ai_reply(
            appmod, tenant.id, "96171555666", cust, "قديش رصيدي؟", settings=settings
        )
        assert _ai_tickets(tenant.id) == []
        assert res["escalate"] is False


def test_repeated_handoff_claims_reuse_the_open_ticket(app, client, monkeypatch):
    """Session 13 repeated the claim three turns in a row ("حولت…", "سجّلت
    طلب…", "عم يتابعوه…"). One open AI ticket per customer is enough -- later
    claims must point at it instead of opening a new one each turn."""
    import cs_agent_tools
    with app.app_context():
        tenant, cust, settings, sent = _handoff_guard_env(
            app, client, monkeypatch, "admin_test_handoff_dedupe", "حولت ملفك للقسم المالي."
        )
        cs_agent_tools.handle_whatsapp_cs_ai_reply(appmod, tenant.id, "96171555666", cust, "ليش 50؟", settings=settings)

        monkeypatch.setattr(
            cs_agent_tools, "query_gemini_agent",
            lambda *a, **kw: {"intent": "gemini_agent", "reply_text": "ما تعتل هم، عم يتابعوه الماليّة.",
                              "ticket_tag": "محادثة ذكاء اصطناعي", "escalate": False}
        )
        res = cs_agent_tools.handle_whatsapp_cs_ai_reply(appmod, tenant.id, "96171555666", cust, "ماشي، سلام", settings=settings)

        tickets = _ai_tickets(tenant.id)
        assert len(tickets) == 1
        assert res["escalate"] is True
        assert res["ticket_id"] == tickets[0].id


def test_real_escalation_is_not_duplicated_by_the_guard(app, client, monkeypatch):
    """When the model genuinely called escalate_to_human (escalate=True), the
    guard must leave it alone -- no second ticket."""
    import cs_agent_tools
    with app.app_context():
        tenant, cust, settings, _ = _handoff_guard_env(
            app, client, monkeypatch, "admin_test_handoff_real", "حولت طلبك لفريق الدعم.", escalate=True
        )
        cs_agent_tools.handle_whatsapp_cs_ai_reply(appmod, tenant.id, "96171555666", cust, "بدي موظف", settings=settings)
        assert _ai_tickets(tenant.id) == []
