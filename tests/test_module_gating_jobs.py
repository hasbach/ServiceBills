import app as appmod
from tests.conftest import make_tenant
from tests.test_module_gating_api import _set_overrides
from tests.test_whatsapp_keepalive import _configure_api_mode, FakeResponse
from tests.test_phase3_network_automation import _setup_bridged_customer, _enable_automation
from tests.test_scheduled_network_freshness import _make_agent_mode_tenant_with_olt


def _tid(app, slug):
    with app.app_context():
        return appmod.Tenant.query.filter_by(slug=slug).first().id


def test_keepalive_skips_without_whatsapp(app, client, monkeypatch):
    hdr = make_tenant(client, "Job Wa", "jobwa_admin")
    _configure_api_mode(app, client, hdr, "job-wa")
    _set_overrides(app, "job-wa", {"whatsapp": False})
    calls = []
    monkeypatch.setattr(appmod.requests, "post",
                        lambda url, json, headers, timeout: (calls.append(url), FakeResponse())[1])
    with app.app_context():
        appmod.send_daily_whatsapp_keepalive(_tid(app, "job-wa"))
    assert calls == []


def test_upstream_sync_skips_without_module(app, client, monkeypatch):
    hdr = make_tenant(client, "Job Up", "jobup_admin")
    customer_id = _setup_bridged_customer(client, hdr)
    tid = _tid(app, "job-up")
    _enable_automation(app, tid)
    _set_overrides(app, "job-up", {"upstream_sync": False})
    calls = []
    monkeypatch.setattr(appmod.upstream_portal, "get_subscriber_status",
                        lambda p, u: (calls.append(u), (True, {"status": "online", "expiry": None}))[1])
    with app.app_context():
        appmod.auto_sync_upstream_status_for_tenant(tid)
    assert calls == []
    with app.app_context():
        assert appmod.db.session.get(appmod.Customer, customer_id).upstream_last_synced_at is None


def test_network_refresh_skips_without_module(app, client, monkeypatch):
    tid, _device_id = _make_agent_mode_tenant_with_olt(app, client, "Job Nw", "jobnw_admin")
    _set_overrides(app, "job-nw", {"network": False})
    calls = []
    monkeypatch.setattr(appmod, "_create_scheduled_device_job",
                        lambda *a, **k: (calls.append(a), (None, "x"))[1])
    with app.app_context():
        appmod.refresh_agent_mode_network_status_for_tenant(tid)
    assert calls == []
