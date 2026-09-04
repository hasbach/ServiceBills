"""Agent-facing endpoint tests. The agent authenticates with its own token,
never a user JWT, and must never see another tenant's jobs."""
import app as appmod
from tests.conftest import make_tenant


def _tenant(name):
    return appmod.Tenant.query.filter_by(name=name).first()


def make_agent_and_device(app, tenant_name):
    """Returns (token, device_id, tenant_id)."""
    with app.app_context():
        tenant = _tenant(tenant_name)
        agent = appmod.NetworkAgent(tenant_id=tenant.id, name="Box", token_hash="x")
        appmod.db.session.add(agent)
        appmod.db.session.commit()
        token = appmod._issue_agent_token(agent)
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="EPON OLT", host="192.168.8.100",
            username="", password="unused", device_type="vsol_olt", api_port=161)
        appmod.db.session.add(device)
        appmod.db.session.commit()
        return token, device.id, tenant.id


def make_job(app, tenant_id, device_id, operation="olt_status"):
    with app.app_context():
        job = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation=operation)
        appmod.db.session.add(job)
        appmod.db.session.commit()
        return job.id


def auth(token):
    return {"Authorization": "Bearer " + token, "X-Agent-Version": "1.0.0"}


def test_poll_without_a_token_is_rejected(app, client):
    assert client.get("/api/agent/jobs").status_code == 401


def test_poll_with_a_garbage_token_is_rejected(app, client):
    assert client.get("/api/agent/jobs", headers=auth("nonsense")).status_code == 401
    assert client.get("/api/agent/jobs", headers=auth("9.abc")).status_code == 401


def test_poll_returns_204_when_no_work(app, client):
    make_tenant(client, "Api A", "api_a_admin")
    token, _, _ = make_agent_and_device(app, "Api A")
    assert client.get("/api/agent/jobs", headers=auth(token)).status_code == 204


def test_poll_claims_a_job_and_returns_host_but_no_credential(app, client):
    make_tenant(client, "Api B", "api_b_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api B")
    job_id = make_job(app, tenant_id, device_id)

    body = client.get("/api/agent/jobs", headers=auth(token)).get_json()
    assert body["job_id"] == job_id
    assert body["operation"] == "olt_status"
    assert body["host"] == "192.168.8.100"
    assert body["api_port"] == 161
    # The whole point of the design: no secret crosses the wire.
    assert "password" not in body
    assert "community" not in body
    assert "unused" not in str(body)

    with app.app_context():
        assert appmod.NetworkAgentJob.query.get(job_id).status == "claimed"


def test_poll_stamps_last_seen_and_version(app, client):
    make_tenant(client, "Api C", "api_c_admin")
    token, _, tenant_id = make_agent_and_device(app, "Api C")
    client.get("/api/agent/jobs", headers=auth(token))
    with app.app_context():
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first()
        assert agent.last_seen_at is not None
        assert agent.agent_version == "1.0.0"
        assert agent.is_online() is True


def test_a_claimed_job_is_not_handed_out_twice(app, client):
    make_tenant(client, "Api D", "api_d_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api D")
    make_job(app, tenant_id, device_id)
    assert client.get("/api/agent/jobs", headers=auth(token)).status_code == 200
    assert client.get("/api/agent/jobs", headers=auth(token)).status_code == 204


def test_agent_never_sees_another_tenants_job(app, client):
    make_tenant(client, "Api E1", "api_e1_admin")
    _, device_one, tenant_one = make_agent_and_device(app, "Api E1")
    make_job(app, tenant_one, device_one)

    make_tenant(client, "Api E2", "api_e2_admin")
    token_two, _, _ = make_agent_and_device(app, "Api E2")

    # Tenant two's agent must see nothing, even though a job exists globally.
    assert client.get("/api/agent/jobs", headers=auth(token_two)).status_code == 204


def test_agent_cannot_post_a_result_to_another_tenants_job(app, client):
    make_tenant(client, "Api F1", "api_f1_admin")
    _, device_one, tenant_one = make_agent_and_device(app, "Api F1")
    job_id = make_job(app, tenant_one, device_one)

    make_tenant(client, "Api F2", "api_f2_admin")
    token_two, _, _ = make_agent_and_device(app, "Api F2")

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token_two),
                    json={"ok": True, "result": [], "error": None})
    assert r.status_code == 404
    with app.app_context():
        assert appmod.NetworkAgentJob.query.get(job_id).status == "pending"


def test_posting_a_success_result_completes_the_job(app, client):
    make_tenant(client, "Api G", "api_g_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api G")
    job_id = make_job(app, tenant_id, device_id)
    client.get("/api/agent/jobs", headers=auth(token))

    onus = [{"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
             "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
             "model": "V2801D", "distance_m": 531}]
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": onus, "error": None})
    assert r.status_code == 200
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert job.error is None
        assert job.result[0]["description"] == "MoussaGhadir"
        assert job.finished_at is not None


def test_posting_a_failure_result_records_the_message(app, client):
    make_tenant(client, "Api H", "api_h_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api H")
    job_id = make_job(app, tenant_id, device_id)
    client.get("/api/agent/jobs", headers=auth(token))

    client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                json={"ok": False, "result": None,
                      "error": "No SNMP response received before timeout"})
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        assert job.status == "done"
        assert "timeout" in job.error


def test_result_is_refused_for_a_job_that_was_never_claimed(app, client):
    make_tenant(client, "Api I", "api_i_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api I")
    job_id = make_job(app, tenant_id, device_id)
    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token),
                    json={"ok": True, "result": [], "error": None})
    assert r.status_code == 409


def test_a_regenerated_token_invalidates_the_old_one(app, client):
    make_tenant(client, "Api J", "api_j_admin")
    old_token, _, tenant_id = make_agent_and_device(app, "Api J")
    with app.app_context():
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first()
        new_token = appmod._issue_agent_token(agent)
        appmod.db.session.commit()
    assert client.get("/api/agent/jobs", headers=auth(old_token)).status_code == 401
    assert client.get("/api/agent/jobs", headers=auth(new_token)).status_code == 204
