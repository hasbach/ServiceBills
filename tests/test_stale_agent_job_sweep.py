"""Stale agent jobs are swept on paths that always run, not only when a
browser reads the job back.

_expire_job_if_stale used to run only from reads (GET /api/network-jobs/<id>
and a few endpoints). A job nobody reads -- the automatic unsuspend queued by
_maybe_restore_mikrotik_access after a payment, or any job whose tab was
closed -- stayed 'pending' or 'claimed' forever, and its NetworkWriteAudit row
stayed 'queued' forever with it. Worse, a 'pending' job had no age check at
claim time, so an agent coming back online hours later would still perform a
write the cloud had long stopped expecting.

The sweep now also runs on the agent's own poll (before a job is handed out)
and whenever a new job is created.
"""
from datetime import datetime, timedelta

from flask_jwt_extended import create_access_token, verify_jwt_in_request

import app as appmod
from tests.conftest import make_tenant
from tests.test_network_agent_api import make_agent_and_device, auth
from tests.test_mikrotik_consolidation import (
    stub_connectors, make_device, _admin, _set_agent_mode, _linked_customer)
from tests.test_relay_writes import _set_agent_version


LONG_AGO = timedelta(hours=3)


def _job(app, tenant_id, device_id, operation="olt_status", status="pending",
         age=LONG_AGO, params=None):
    with app.app_context():
        then = datetime.utcnow() - age
        job = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation=operation,
            status=status, params=params or {})
        job.created_at = then
        if status == "claimed":
            job.claimed_at = then
        appmod.db.session.add(job)
        appmod.db.session.commit()
        return job.id


def _queued_audit(app, tenant_id, job_id, action="unsuspend"):
    with app.app_context():
        row = appmod.NetworkWriteAudit(
            tenant_id=tenant_id, pppoe_username="bach1", action=action,
            job_id=job_id, outcome="queued")
        appmod.db.session.add(row)
        appmod.db.session.commit()
        return row.id


def _get(app, model, row_id):
    with app.app_context():
        return appmod.db.session.get(model, row_id)


def test_poll_expires_a_stale_pending_job_instead_of_handing_it_out(app, client):
    make_tenant(client, "Sweep A", "sweep_a_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Sweep A")
    job_id = _job(app, tenant_id, device_id)

    assert client.get("/api/agent/jobs", headers=auth(token)).status_code == 204

    job = _get(app, appmod.NetworkAgentJob, job_id)
    assert job.status == "expired"
    assert job.finished_at is not None


def test_poll_still_hands_out_a_fresh_job_queued_behind_a_stale_one(app, client):
    make_tenant(client, "Sweep B", "sweep_b_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Sweep B")
    stale_id = _job(app, tenant_id, device_id)
    fresh_id = _job(app, tenant_id, device_id, age=timedelta(seconds=1))

    body = client.get("/api/agent/jobs", headers=auth(token)).get_json()

    assert body["job_id"] == fresh_id
    assert _get(app, appmod.NetworkAgentJob, stale_id).status == "expired"


def test_poll_fails_a_job_that_was_claimed_and_never_reported(app, client):
    """The agent claimed it, then died mid-job. Its next poll after restart
    is the first request guaranteed to happen -- close the job there."""
    make_tenant(client, "Sweep C", "sweep_c_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Sweep C")
    job_id = _job(app, tenant_id, device_id, status="claimed")

    client.get("/api/agent/jobs", headers=auth(token))

    assert _get(app, appmod.NetworkAgentJob, job_id).status == "failed"


def test_sweeping_a_stale_write_job_closes_its_audit_row(app, client):
    make_tenant(client, "Sweep D", "sweep_d_admin")
    token, _, tenant_id = make_agent_and_device(app, "Sweep D")
    ccr_id = make_device(app, "Sweep D")
    job_id = _job(app, tenant_id, ccr_id, operation="unsuspend_secret",
                  params={"pppoe_username": "bach1"})
    audit_id = _queued_audit(app, tenant_id, job_id)

    assert client.get("/api/agent/jobs", headers=auth(token)).status_code == 204

    audit = _get(app, appmod.NetworkWriteAudit, audit_id)
    assert audit.outcome == "failed"
    assert audit.message


def test_poll_leaves_other_tenants_stale_jobs_alone(app, client):
    """The poll is authenticated as one tenant's agent; it must not touch
    another tenant's rows, even to expire them."""
    make_tenant(client, "Sweep E1", "sweep_e1_admin")
    make_tenant(client, "Sweep E2", "sweep_e2_admin")
    token, _, _ = make_agent_and_device(app, "Sweep E1")
    _, other_device, other_tenant = make_agent_and_device(app, "Sweep E2")
    other_job = _job(app, other_tenant, other_device)

    client.get("/api/agent/jobs", headers=auth(token))

    assert _get(app, appmod.NetworkAgentJob, other_job).status == "pending"


def test_poll_leaves_a_recently_claimed_job_alone(app, client):
    make_tenant(client, "Sweep F", "sweep_f_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Sweep F")
    job_id = _job(app, tenant_id, device_id, status="claimed",
                  age=timedelta(seconds=5))

    client.get("/api/agent/jobs", headers=auth(token))

    assert _get(app, appmod.NetworkAgentJob, job_id).status == "claimed"


def test_the_payment_restore_sweeps_a_stuck_write_before_queueing(app, client, monkeypatch):
    """The path the gap was found on: a restore whose earlier job got stuck.
    Creating the next job closes the old one and its audit row, in the same
    commit as the new job and audit."""
    stub_connectors(monkeypatch)
    _admin(client, "Sweep G", "sweep_g_admin")
    device_id = make_device(app, "Sweep G")
    customer_id = _linked_customer(app, "Sweep G", device_id)
    _set_agent_mode(app, "Sweep G")
    _set_agent_version(app, "Sweep G", "1.3.0")
    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(name="Sweep G").first().id
    stuck_id = _job(app, tenant_id, device_id, operation="unsuspend_secret",
                    status="claimed", params={"pppoe_username": "bach1"})
    stuck_audit_id = _queued_audit(app, tenant_id, stuck_id)

    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        token = create_access_token(identity="sweep_g_admin",
                                    additional_claims={"tenant_id": tenant_id})
        with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
            verify_jwt_in_request()
            result = appmod._maybe_restore_mikrotik_access(customer)
        assert result is not None and result.get("queued") is True

    assert _get(app, appmod.NetworkAgentJob, stuck_id).status == "failed"
    assert _get(app, appmod.NetworkWriteAudit, stuck_audit_id).outcome == "failed"
    with app.app_context():
        assert appmod.NetworkAgentJob.query.filter_by(
            tenant_id=tenant_id, status="pending").count() == 1


def test_a_scheduled_job_creation_sweeps_too(app, client):
    make_tenant(client, "Sweep H", "sweep_h_admin")
    _, device_id, tenant_id = make_agent_and_device(app, "Sweep H")
    stuck_id = _job(app, tenant_id, device_id, status="claimed")
    with app.app_context():
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant_id).first()
        agent.last_seen_at = datetime.utcnow()
        appmod.db.session.commit()
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        job, error = appmod._create_scheduled_device_job(device, "olt_status")
        assert error is None and job is not None

    assert _get(app, appmod.NetworkAgentJob, stuck_id).status == "failed"
