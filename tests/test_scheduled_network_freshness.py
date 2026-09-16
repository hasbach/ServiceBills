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


def test_refresh_creates_both_jobs_for_an_agent_mode_olt(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(app, client, "Sched D", "sched_d_admin")
    with app.app_context():
        appmod.refresh_agent_mode_network_status_for_tenant(tenant_id)
        jobs = appmod.NetworkAgentJob.query.filter_by(tenant_id=tenant_id, device_id=device_id).all()
        operations = sorted(j.operation for j in jobs)
        assert operations == ['cpe_locations', 'olt_status']
        assert all(j.params == {'_scheduled': True} for j in jobs)


def test_refresh_skips_a_device_operation_with_an_outstanding_job(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(app, client, "Sched E", "sched_e_admin")
    with app.app_context():
        existing = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation='olt_status',
            status='pending')
        appmod.db.session.add(existing)
        appmod.db.session.commit()

        appmod.refresh_agent_mode_network_status_for_tenant(tenant_id)

        olt_status_jobs = appmod.NetworkAgentJob.query.filter_by(
            tenant_id=tenant_id, device_id=device_id, operation='olt_status').all()
        assert len(olt_status_jobs) == 1  # no duplicate created
        cpe_jobs = appmod.NetworkAgentJob.query.filter_by(
            tenant_id=tenant_id, device_id=device_id, operation='cpe_locations').all()
        assert len(cpe_jobs) == 1  # this one still gets created


def test_refresh_is_a_noop_for_a_direct_mode_tenant(app, client):
    make_tenant(client, "Sched F", "sched_f_admin")  # network_access_mode defaults to 'direct'
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Sched F").first()
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="EPON OLT", host="192.168.8.101",
            username="", password="unused", device_type="vsol_olt", api_port=161)
        appmod.db.session.add(device)
        appmod.db.session.commit()

        appmod.refresh_agent_mode_network_status_for_tenant(tenant.id)

        assert appmod.NetworkAgentJob.query.filter_by(tenant_id=tenant.id).count() == 0


def test_refresh_with_context_isolates_one_tenants_failure(app, client, monkeypatch):
    tenant_a_id, _ = _make_agent_mode_tenant_with_olt(app, client, "Sched G", "sched_g_admin")
    tenant_b_id, device_b_id = _make_agent_mode_tenant_with_olt(app, client, "Sched H", "sched_h_admin")

    original = appmod.refresh_agent_mode_network_status_for_tenant

    def boom(tenant_id):
        if tenant_id == tenant_a_id:
            raise RuntimeError("simulated failure")
        return original(tenant_id)

    monkeypatch.setattr(appmod, "refresh_agent_mode_network_status_for_tenant", boom)

    with app.app_context():
        appmod.refresh_agent_mode_network_status_with_context()
        # Tenant B's job still got created despite tenant A's failure.
        jobs = appmod.NetworkAgentJob.query.filter_by(tenant_id=tenant_b_id, device_id=device_b_id).all()
        assert len(jobs) == 2
