"""Tests for the scheduled ONU/CPE-location freshness job. See
docs/superpowers/specs/2026-09-17-network-tree-scheduled-freshness-design.md."""
import app as appmod
from tests.conftest import make_tenant


def _make_agent_mode_tenant_with_olt(app, client, business_name, username, agent_online=True):
    """Returns (tenant_id, device_id). Sets network_access_mode='agent' and
    creates one online (or offline) NetworkAgent plus one vsol_olt device."""
    make_tenant(client, business_name, username)
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=business_name).first()
        settings = appmod.BusinessSettings(
            tenant_id=tenant.id, business_name=business_name, address="123 Main St",
            mobile="+1234567890", network_access_mode='agent')
        appmod.db.session.add(settings)
        agent = appmod.NetworkAgent(
            tenant_id=tenant.id, name="Box", token_hash="x",
            last_seen_at=appmod.datetime.utcnow() if agent_online else None)
        appmod.db.session.add(agent)
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="EPON OLT", host="192.168.8.100",
            username="", password="unused", device_type="vsol_olt", api_port=161)
        appmod.db.session.add(device)
        appmod.db.session.commit()
        return tenant.id, device.id


def test_create_scheduled_device_job_stamps_scheduled_marker_and_no_user(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(app, client, "Sched A", "sched_a_admin")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        # No request context anywhere in this test -- this must not touch
        # tenant_query()/current_tenant_id() at all.
        job, error = appmod._create_scheduled_device_job(device, 'olt_status')
        assert error is None
        assert job.requested_by_user_id is None
        assert job.params == {'_scheduled': True}
        assert job.tenant_id == tenant_id
        assert job.status == 'pending'


def test_create_scheduled_device_job_reports_agent_offline(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(
        app, client, "Sched B", "sched_b_admin", agent_online=False)
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        job, error = appmod._create_scheduled_device_job(device, 'olt_status')
        assert job is None
        assert 'Agent offline' in error


def test_create_scheduled_device_job_rejects_unsupported_operation_for_device_type(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(app, client, "Sched C", "sched_c_admin")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        # olt devices cannot perform device_health (that's a mikrotik_ccr operation)
        job, error = appmod._create_scheduled_device_job(device, 'device_health')
        assert job is None
        assert 'cannot perform' in error
