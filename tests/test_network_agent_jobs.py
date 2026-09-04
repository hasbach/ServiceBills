"""Job lifecycle: creation in both modes, lazy expiry, and the browser poll."""
from datetime import datetime, timedelta

import app as appmod
from tests.conftest import make_tenant


def _tenant(name):
    return appmod.Tenant.query.filter_by(name=name).first()


def set_mode(app, tenant_name, mode):
    with app.app_context():
        tenant = _tenant(tenant_name)
        settings = appmod.BusinessSettings.query.filter_by(tenant_id=tenant.id).first()
        if settings is None:
            # address/mobile are NOT NULL with no default; registration doesn't
            # populate BusinessSettings, so a fresh tenant has no row yet -- see
            # test_phase3_network_automation.py's _enable_automation for the
            # same pattern.
            settings = appmod.BusinessSettings(
                tenant_id=tenant.id, business_name=tenant_name, address="a", mobile="1")
            appmod.db.session.add(settings)
        settings.network_access_mode = mode
        appmod.db.session.commit()


def make_device(app, tenant_name):
    with app.app_context():
        tenant = _tenant(tenant_name)
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="EPON OLT", host="192.168.8.100",
            username="", password="community", device_type="vsol_olt", api_port=161)
        appmod.db.session.add(device)
        appmod.db.session.commit()
        return device.id


def make_online_agent(app, tenant_name):
    with app.app_context():
        tenant = _tenant(tenant_name)
        agent = appmod.NetworkAgent(
            tenant_id=tenant.id, name="Box", token_hash="x",
            last_seen_at=datetime.utcnow())
        appmod.db.session.add(agent)
        appmod.db.session.commit()


def test_agent_mode_refuses_to_enqueue_when_no_agent_exists(app, client):
    hdr = make_tenant(client, "Job A", "job_a_admin")
    set_mode(app, "Job A", "agent")
    device_id = make_device(app, "Job A")
    r = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)
    body = r.get_json()
    assert body["ok"] is False
    assert "agent" in body["message"].lower()
    with app.app_context():
        assert appmod.NetworkAgentJob.query.count() == 0


def test_agent_mode_refuses_when_the_agent_is_stale(app, client):
    hdr = make_tenant(client, "Job B", "job_b_admin")
    set_mode(app, "Job B", "agent")
    device_id = make_device(app, "Job B")
    with app.app_context():
        tenant = _tenant("Job B")
        appmod.db.session.add(appmod.NetworkAgent(
            tenant_id=tenant.id, name="Box", token_hash="x",
            last_seen_at=datetime.utcnow() - timedelta(seconds=120)))
        appmod.db.session.commit()
    body = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr).get_json()
    assert body["ok"] is False
    with app.app_context():
        assert appmod.NetworkAgentJob.query.count() == 0


def test_agent_mode_creates_a_pending_job_and_returns_its_id(app, client):
    hdr = make_tenant(client, "Job C", "job_c_admin")
    set_mode(app, "Job C", "agent")
    device_id = make_device(app, "Job C")
    make_online_agent(app, "Job C")

    body = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr).get_json()
    assert body["ok"] is True
    assert body["job_id"] is not None
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(body["job_id"])
        assert job.status == "pending"
        assert job.operation == "olt_status"


def test_direct_mode_returns_an_already_completed_job(app, client, monkeypatch):
    """Direct mode must give the frontend the SAME shape, so the UI has one
    code path -- the job simply arrives already done."""
    hdr = make_tenant(client, "Job D", "job_d_admin")
    set_mode(app, "Job D", "direct")
    device_id = make_device(app, "Job D")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, [{"x": 1}]))

    body = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr).get_json()
    assert body["ok"] is True
    job_id = body["job_id"]
    polled = client.get(f"/api/network-jobs/{job_id}", headers=hdr).get_json()
    assert polled["status"] == "done"
    assert polled["result"] == [{"x": 1}]


def test_direct_mode_records_a_connector_failure_as_error(app, client, monkeypatch):
    hdr = make_tenant(client, "Job E", "job_e_admin")
    set_mode(app, "Job E", "direct")
    device_id = make_device(app, "Job E")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (False, "No SNMP response received before timeout"))
    body = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr).get_json()
    polled = client.get(f"/api/network-jobs/{body['job_id']}", headers=hdr).get_json()
    assert polled["status"] == "done"
    assert "timeout" in polled["error"]


def test_pending_job_expires_lazily_on_read(app, client):
    hdr = make_tenant(client, "Job F", "job_f_admin")
    set_mode(app, "Job F", "agent")
    device_id = make_device(app, "Job F")
    make_online_agent(app, "Job F")
    job_id = client.post(f"/api/network-devices/{device_id}/check-now",
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.created_at = datetime.utcnow() - timedelta(
            seconds=appmod.JOB_CLAIM_TIMEOUT_SECONDS + 10)
        appmod.db.session.commit()

    polled = client.get(f"/api/network-jobs/{job_id}", headers=hdr).get_json()
    assert polled["status"] == "expired"
    assert "did not pick" in polled["error"].lower() or "agent" in polled["error"].lower()


def test_claimed_job_fails_lazily_when_no_result_arrives(app, client):
    hdr = make_tenant(client, "Job G", "job_g_admin")
    set_mode(app, "Job G", "agent")
    device_id = make_device(app, "Job G")
    make_online_agent(app, "Job G")
    job_id = client.post(f"/api/network-devices/{device_id}/check-now",
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.status = "claimed"
        job.claimed_at = datetime.utcnow() - timedelta(
            seconds=appmod.JOB_RESULT_TIMEOUT_SECONDS + 10)
        appmod.db.session.commit()

    polled = client.get(f"/api/network-jobs/{job_id}", headers=hdr).get_json()
    assert polled["status"] == "failed"


def test_a_finished_job_is_never_re_expired(app, client, monkeypatch):
    hdr = make_tenant(client, "Job H", "job_h_admin")
    set_mode(app, "Job H", "direct")
    device_id = make_device(app, "Job H")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, []))
    job_id = client.post(f"/api/network-devices/{device_id}/check-now",
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.created_at = datetime.utcnow() - timedelta(hours=2)
        appmod.db.session.commit()
    polled = client.get(f"/api/network-jobs/{job_id}", headers=hdr).get_json()
    assert polled["status"] == "done"


def test_job_poll_is_tenant_scoped(app, client, monkeypatch):
    hdr_one = make_tenant(client, "Job I1", "job_i1_admin")
    set_mode(app, "Job I1", "direct")
    device_id = make_device(app, "Job I1")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, []))
    job_id = client.post(f"/api/network-devices/{device_id}/check-now",
                         headers=hdr_one).get_json()["job_id"]

    hdr_two = make_tenant(client, "Job I2", "job_i2_admin")
    assert client.get(f"/api/network-jobs/{job_id}", headers=hdr_two).status_code == 404
