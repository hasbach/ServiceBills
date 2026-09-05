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
    code path -- the job simply arrives already done.

    The fake connector result must be shaped like a real ONU record (a
    'mac_address' key) because Task 4 added customer resolution on read for
    completed olt_status jobs -- see test_refresh_returns_a_job_and_customers_
    are_resolved_on_poll below for that behaviour itself. An ONU with no
    matching customer still gets an empty 'customers' list attached, which
    this test's assertion accounts for.
    """
    hdr = make_tenant(client, "Job D", "job_d_admin")
    set_mode(app, "Job D", "direct")
    device_id = make_device(app, "Job D")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status",
                        lambda d: (True, [{"mac_address": "aa:bb:cc:dd:ee:ff"}]))

    body = client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr).get_json()
    assert body["ok"] is True
    job_id = body["job_id"]
    polled = client.get(f"/api/network-jobs/{job_id}", headers=hdr).get_json()
    assert polled["status"] == "done"
    assert polled["result"] == [{"mac_address": "aa:bb:cc:dd:ee:ff", "customers": []}]


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


ONUS = [
    {"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
     "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
     "model": "V2801D", "distance_m": 531},
]


def test_refresh_returns_a_job_and_customers_are_resolved_on_poll(app, client, monkeypatch):
    hdr = make_tenant(client, "Job K", "job_k_admin")
    set_mode(app, "Job K", "direct")
    device_id = make_device(app, "Job K")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))

    with app.app_context():
        tenant = _tenant("Job K")
        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Basic", price=10, cost=5,
            billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        appmod.db.session.add(appmod.Customer(
            tenant_id=tenant.id, name="Moussa Ghadir", phone="1", address="a",
            subscription_plan_id=plan.id,
            subscription_expiry_date=appmod.datetime.utcnow(),
            onu_mac_address="b4:64:15:3f:c1:94"))
        appmod.db.session.commit()

    body = client.post(f"/api/network-tree/olt/{device_id}/refresh", headers=hdr).get_json()
    assert body["ok"] is True and body["job_id"]
    polled = client.get(f"/api/network-jobs/{body['job_id']}", headers=hdr).get_json()
    assert polled["status"] == "done"
    assert [c["name"] for c in polled["result"][0]["customers"]] == ["Moussa Ghadir"]


def test_refresh_on_a_non_olt_is_rejected(app, client):
    hdr = make_tenant(client, "Job L", "job_l_admin")
    set_mode(app, "Job L", "direct")
    with app.app_context():
        tenant = _tenant("Job L")
        ccr = appmod.NetworkDevice(
            tenant_id=tenant.id, name="CCR", host="192.168.8.1",
            username="admin", password="p", device_type="mikrotik_ccr", api_port=8728)
        appmod.db.session.add(ccr)
        appmod.db.session.commit()
        ccr_id = ccr.id
    assert client.post(f"/api/network-tree/olt/{ccr_id}/refresh",
                       headers=hdr).status_code == 400


def test_label_matches_computes_proposals_from_a_completed_job(app, client, monkeypatch):
    hdr = make_tenant(client, "Job M", "job_m_admin")
    set_mode(app, "Job M", "direct")
    device_id = make_device(app, "Job M")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))

    with app.app_context():
        tenant = _tenant("Job M")
        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Basic", price=10, cost=5,
            billing_cycle="monthly", currency="USD")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        appmod.db.session.add(appmod.Customer(
            tenant_id=tenant.id, name="Moussa Ghadir", phone="1", address="a",
            subscription_plan_id=plan.id,
            subscription_expiry_date=appmod.datetime.utcnow()))
        appmod.db.session.commit()

    started = client.get(f"/api/network-tree/olt/{device_id}/label-matches",
                         headers=hdr).get_json()
    assert started["job_id"]
    done = client.get(
        f"/api/network-tree/olt/{device_id}/label-matches?job_id={started['job_id']}",
        headers=hdr).get_json()
    assert done["ok"] is True
    assert len(done["proposals"]) == 1
    assert done["proposals"][0]["customer"]["name"] == "Moussa Ghadir"
    assert done["proposals"][0]["confidence"] == 1.0


# --- Final review, Important 5: network_agent_job has no retention and (by
# design) no scheduled job to add one -- every check-now writes a permanent
# row, in direct mode too, on a Supabase free tier capped at 500 MB. Fix
# reuses the lazy-cleanup pattern: _create_device_job prunes this tenant's
# own terminal jobs older than NETWORK_AGENT_JOB_RETENTION_DAYS right before
# the insert it already commits. ---------------------------------------------

def _seed_terminal_job(app, tenant_id, device_id, age_days, status="done"):
    with app.app_context():
        j = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation="olt_status",
            status=status,
            created_at=datetime.utcnow() - timedelta(days=age_days),
            finished_at=datetime.utcnow() - timedelta(days=age_days))
        appmod.db.session.add(j)
        appmod.db.session.commit()
        return j.id


def test_a_terminal_job_past_the_retention_window_is_pruned_on_the_next_job(app, client, monkeypatch):
    hdr = make_tenant(client, "Job N", "job_n_admin")
    set_mode(app, "Job N", "direct")
    device_id = make_device(app, "Job N")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, []))
    tenant_id = _tenant("Job N").id
    _seed_terminal_job(
        app, tenant_id, device_id,
        age_days=appmod.NETWORK_AGENT_JOB_RETENTION_DAYS + 1)

    client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)

    with app.app_context():
        # Not asserting on the old row's id directly: SQLite reuses a
        # deleted row's rowid for the very next insert when nothing else
        # occupies it, so the pruned row and the job check-now just created
        # can legitimately end up sharing an id in this test's in-memory DB
        # (Postgres's real sequence never does this). Counting is what
        # actually proves the stale row is gone: if pruning had done
        # nothing, this tenant would have two rows, not one.
        jobs = appmod.NetworkAgentJob.query.filter_by(tenant_id=tenant_id).all()
        assert len(jobs) == 1
        assert jobs[0].created_at > datetime.utcnow() - timedelta(minutes=1)


def test_a_terminal_job_within_the_retention_window_is_kept(app, client, monkeypatch):
    hdr = make_tenant(client, "Job O", "job_o_admin")
    set_mode(app, "Job O", "direct")
    device_id = make_device(app, "Job O")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, []))
    tenant_id = _tenant("Job O").id
    recent_id = _seed_terminal_job(
        app, tenant_id, device_id,
        age_days=appmod.NETWORK_AGENT_JOB_RETENTION_DAYS - 1)

    client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)

    with app.app_context():
        assert appmod.NetworkAgentJob.query.get(recent_id) is not None


def test_pruning_only_touches_this_tenants_own_jobs(app, client, monkeypatch):
    hdr = make_tenant(client, "Job P1", "job_p1_admin")
    set_mode(app, "Job P1", "direct")
    device_one = make_device(app, "Job P1")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, []))
    tenant_one = _tenant("Job P1").id

    make_tenant(client, "Job P2", "job_p2_admin")
    set_mode(app, "Job P2", "direct")
    device_two = make_device(app, "Job P2")
    tenant_two = _tenant("Job P2").id
    other_old_id = _seed_terminal_job(
        app, tenant_two, device_two,
        age_days=appmod.NETWORK_AGENT_JOB_RETENTION_DAYS + 1)

    client.post(f"/api/network-devices/{device_one}/check-now", headers=hdr)

    with app.app_context():
        # Tenant P1's own job creation must never prune tenant P2's rows.
        assert appmod.NetworkAgentJob.query.get(other_old_id) is not None


def test_pending_or_claimed_jobs_are_never_pruned_regardless_of_age(app, client, monkeypatch):
    hdr = make_tenant(client, "Job Q", "job_q_admin")
    set_mode(app, "Job Q", "direct")
    device_id = make_device(app, "Job Q")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, []))
    tenant_id = _tenant("Job Q").id
    stale_pending_id = _seed_terminal_job(
        app, tenant_id, device_id,
        age_days=appmod.NETWORK_AGENT_JOB_RETENTION_DAYS + 30, status="pending")

    client.post(f"/api/network-devices/{device_id}/check-now", headers=hdr)

    with app.app_context():
        # Old, but never claimed/finished -- pruning must not touch live work.
        assert appmod.NetworkAgentJob.query.get(stale_pending_id) is not None
