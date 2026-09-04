"""Model-level tests for the Layer 2 agent relay. See
docs/superpowers/specs/2026-09-04-network-agent-layer-2-design.md."""
from datetime import datetime, timedelta

import app as appmod
from tests.conftest import make_tenant


def _tenant(name):
    return appmod.Tenant.query.filter_by(name=name).first()


def _device(tenant_id, name="EPON OLT"):
    device = appmod.NetworkDevice(
        tenant_id=tenant_id, name=name, host="192.168.8.100",
        username="", password="unused-in-agent-mode",
        device_type="vsol_olt", api_port=161,
    )
    appmod.db.session.add(device)
    appmod.db.session.commit()
    return device


def test_agent_to_dict_never_leaks_the_token_hash(app, client):
    make_tenant(client, "Agent A", "agent_a_admin")
    with app.app_context():
        agent = appmod.NetworkAgent(
            tenant_id=_tenant("Agent A").id, name="DeltaNet Box",
            token_hash="not-a-real-hash",
        )
        appmod.db.session.add(agent)
        appmod.db.session.commit()

        data = agent.to_dict()
        assert data["name"] == "DeltaNet Box"
        assert "token_hash" not in data
        assert "token" not in data


def test_agent_is_online_only_within_the_window(app, client):
    # One tenant per agent -- network_agent.tenant_id is unique (Task 5
    # review fix: "one agent per tenant" is a schema constraint, not just
    # application-level convention), so three agents under test each need
    # their own tenant rather than sharing "Agent B".
    make_tenant(client, "Agent B Never", "agent_b_never_admin")
    make_tenant(client, "Agent B Fresh", "agent_b_fresh_admin")
    make_tenant(client, "Agent B Stale", "agent_b_stale_admin")
    with app.app_context():
        never = appmod.NetworkAgent(
            tenant_id=_tenant("Agent B Never").id, name="Never", token_hash="h")
        fresh = appmod.NetworkAgent(
            tenant_id=_tenant("Agent B Fresh").id, name="Fresh", token_hash="h",
            last_seen_at=datetime.utcnow())
        stale = appmod.NetworkAgent(
            tenant_id=_tenant("Agent B Stale").id, name="Stale", token_hash="h",
            last_seen_at=datetime.utcnow() - timedelta(
                seconds=appmod.AGENT_ONLINE_WINDOW_SECONDS + 5))
        appmod.db.session.add_all([never, fresh, stale])
        appmod.db.session.commit()

        assert never.to_dict()["is_online"] is False
        assert fresh.to_dict()["is_online"] is True
        assert stale.to_dict()["is_online"] is False


def test_job_defaults_to_pending_and_serializes(app, client):
    make_tenant(client, "Agent C", "agent_c_admin")
    with app.app_context():
        tenant = _tenant("Agent C")
        device = _device(tenant.id)
        job = appmod.NetworkAgentJob(
            tenant_id=tenant.id, device_id=device.id, operation="olt_status")
        appmod.db.session.add(job)
        appmod.db.session.commit()

        data = job.to_dict()
        assert data["status"] == "pending"
        assert data["operation"] == "olt_status"
        assert data["device_id"] == device.id
        assert data["result"] is None
        assert data["error"] is None


def test_the_five_relayed_operations_are_read_only(app, client):
    """set_secret_enabled writes to the CCR and must never be relayable."""
    assert appmod.AGENT_OPERATIONS == (
        "test_connection", "device_health", "secret_status",
        "active_session", "olt_status",
    )
    assert "set_secret_enabled" not in appmod.AGENT_OPERATIONS


def test_network_access_mode_defaults_to_direct(app, client):
    hdr = make_tenant(client, "Agent D", "agent_d_admin")
    # /api/business-settings nests the payload under "settings" -- see
    # test_business_settings_default_single_currency (test_multi_currency.py)
    # and test_business_settings_persists_automation_flag
    # (test_phase3_network_automation.py) for the same access pattern against
    # this same endpoint. The brief's verbatim test asserted a flat
    # body["network_access_mode"], which the real endpoint has never returned.
    body = client.get("/api/business-settings", headers=hdr).get_json()
    assert body["settings"]["network_access_mode"] == "direct"
