"""Relaying suspend/unsuspend through the on-prem agent.

The agent deliberately had no write primitive: a compromised cloud could read
the network but never disconnect anyone. These tests cover the machinery that
makes giving it one acceptable -- a version gate so a write is never queued for
an agent that cannot perform it, and an audit trail that outlives job pruning.
See docs/superpowers/specs/2026-09-08-relay-pppoe-writes-design.md.
"""
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from flask_jwt_extended import create_access_token, verify_jwt_in_request

import app as appmod
from tests.test_mikrotik_consolidation import (
    stub_connectors, make_device, _admin, _set_agent_mode, _linked_customer)


def test_both_write_operations_are_relayable():
    assert 'suspend_secret' in appmod.AGENT_OPERATIONS
    assert 'unsuspend_secret' in appmod.AGENT_OPERATIONS


def test_only_a_mikrotik_can_be_asked_to_write():
    """An OLT has no PPPoE secrets. Offering the pairing would queue a job
    nobody can serve."""
    for operation in ('suspend_secret', 'unsuspend_secret'):
        assert operation in appmod.DEVICE_TYPE_OPERATIONS['mikrotik_ccr']
        assert operation not in appmod.DEVICE_TYPE_OPERATIONS['vsol_olt']


def test_the_guard_actually_rejects_a_write_aimed_at_an_olt(app, client):
    """The mapping above is a claim; this is the guard enforcing it. Asserting
    only the dict would pass even if _create_device_job never consulted it."""
    from tests.conftest import make_tenant
    from tests.test_mikrotik_consolidation import make_device
    make_tenant(client, "Guard W", "guard_w_admin")
    device_id = make_device(app, "Guard W", device_type="vsol_olt",
                            api_port=161, username="")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        job, error = appmod._create_device_job(
            device, "suspend_secret", {"pppoe_username": "bach1"})
        assert job is None
        assert "vsol_olt" in error and "suspend_secret" in error


def test_version_parsing_accepts_a_three_part_version():
    assert appmod._parse_agent_version('1.3.0') == (1, 3, 0)
    assert appmod._parse_agent_version(' 1.10.2 ') == (1, 10, 2)


def test_version_parsing_refuses_anything_it_cannot_read():
    """Refusing to parse means refusing to write. An agent we cannot identify
    is treated as too old, never as new enough."""
    for raw in (None, '', '1.3', 'one.three.zero', '1.3.x', 'v1.3.0'):
        assert appmod._parse_agent_version(raw) is None, raw


def test_an_agent_below_the_floor_cannot_write():
    class Stub:
        agent_version = '1.2.0'
    allowed, message = appmod._agent_can_write(Stub())
    assert allowed is False
    assert 'updat' in message.lower()
    assert '1.3.0' in message


def test_an_agent_at_or_above_the_floor_can_write():
    for version in ('1.3.0', '1.4.0', '2.0.0'):
        class Stub:
            agent_version = version
        assert appmod._agent_can_write(Stub())[0] is True, version


def test_an_agent_with_no_version_cannot_write():
    class Stub:
        agent_version = None
    assert appmod._agent_can_write(Stub())[0] is False


def test_a_missing_agent_cannot_write():
    """tenant_query(NetworkAgent).first() returns None when none is registered;
    the caller must not have to special-case that before asking."""
    assert appmod._agent_can_write(None)[0] is False


def test_an_audit_row_records_who_did_what_to_whom(app, client):
    from tests.conftest import make_tenant
    make_tenant(client, "Audit A", "audit_a_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Audit A").first()
        row = appmod.NetworkWriteAudit(
            tenant_id=tenant.id, customer_id=None, network_device_id=None,
            pppoe_username="bach1", action="suspend",
            requested_by_user_id=None, job_id=None, outcome="queued")
        appmod.db.session.add(row)
        appmod.db.session.commit()
        stored = appmod.db.session.get(appmod.NetworkWriteAudit, row.id)
        assert stored.action == "suspend"
        assert stored.outcome == "queued"
        assert stored.created_at is not None


def test_the_audit_row_outlives_the_job_that_created_it(app, client):
    """The whole reason this table exists: _prune_stale_agent_jobs deletes
    terminal jobs, so a job row is not an audit trail."""
    from tests.conftest import make_tenant
    make_tenant(client, "Audit B", "audit_b_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Audit B").first()
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="CCR", host="192.168.100.1", api_port=8728,
            username="admin", password="pw", device_type="mikrotik_ccr")
        appmod.db.session.add(device)
        appmod.db.session.commit()
        job = appmod.NetworkAgentJob(
            tenant_id=tenant.id, device_id=device.id, operation="suspend_secret",
            status="done")
        appmod.db.session.add(job)
        appmod.db.session.commit()
        row = appmod.NetworkWriteAudit(
            tenant_id=tenant.id, pppoe_username="bach1", action="suspend",
            job_id=job.id, outcome="ok", message="disabled")
        appmod.db.session.add(row)
        appmod.db.session.commit()
        row_id, job_id = row.id, job.id

        appmod.db.session.delete(appmod.db.session.get(appmod.NetworkAgentJob, job_id))
        appmod.db.session.commit()

        survivor = appmod.db.session.get(appmod.NetworkWriteAudit, row_id)
        assert survivor is not None, "the audit row must outlive its job"
        assert survivor.pppoe_username == "bach1"
        assert survivor.action == "suspend"


def _sqlite_engine_with_fk_enforcement():
    """A throwaway engine, deliberately not the shared app/db the `app`
    fixture points at. SQLite only enforces foreign keys when
    PRAGMA foreign_keys=ON is set on the connection -- it is off by default,
    resets on every new connection, and Flask-SQLAlchemy caches one Engine
    per Flask app for the life of the process (see
    tests/test_topology_migration.py's module docstring for how that was
    confirmed), so the shared engine cannot be repointed here without
    silently changing FK behaviour for every other test that reuses it.
    A dedicated engine, enabled once via the connect event below, is the only
    way to actually exercise ondelete='SET NULL' instead of merely failing to
    contradict it -- SQLite's default (unenforced) leaves a dangling job_id or
    customer_id in place, which looks identical to a working SET NULL from a
    test that never checks the column's actual value."""
    engine = sa.create_engine("sqlite:///:memory:")

    @sa.event.listens_for(engine, "connect")
    def _enable_fk_enforcement(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    appmod.db.metadata.create_all(bind=engine)
    return engine


def test_deleting_the_job_nulls_the_audit_rows_job_id_under_real_fk_enforcement():
    """The durability test above (test_the_audit_row_outlives_the_job_that_
    created_it) runs under SQLite's default of FK enforcement being off, so it
    cannot tell ondelete='SET NULL' apart from the database default of NO
    ACTION -- both leave the audit row in place there, because SQLite never
    checks the constraint at all. NO ACTION would only reveal itself as a
    ForeignKeyViolation against a real, enforcing database (production's
    Postgres, or SQLite with the pragma below) -- exactly the "invisible
    locally, fails only on production Postgres" pattern this codebase has
    already been bitten by twice. This turns enforcement on for a throwaway
    engine so the constraint is actually exercised, not merely not
    contradicted."""
    engine = _sqlite_engine_with_fk_enforcement()
    session = sessionmaker(bind=engine)()
    try:
        tenant = appmod.Tenant(name="Audit FK Job", slug="audit-fk-job",
                               status="active", plan="free")
        session.add(tenant)
        session.commit()
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="CCR", host="192.168.100.1", api_port=8728,
            username="admin", password="pw", device_type="mikrotik_ccr")
        session.add(device)
        session.commit()
        job = appmod.NetworkAgentJob(
            tenant_id=tenant.id, device_id=device.id, operation="suspend_secret",
            status="done")
        session.add(job)
        session.commit()
        row = appmod.NetworkWriteAudit(
            tenant_id=tenant.id, pppoe_username="bach1", action="suspend",
            job_id=job.id, outcome="ok")
        session.add(row)
        session.commit()
        row_id, job_id = row.id, job.id

        session.delete(session.get(appmod.NetworkAgentJob, job_id))
        session.commit()
        session.close()  # drop the identity map -- the next read must hit the DB

        session = sessionmaker(bind=engine)()
        survivor = session.get(appmod.NetworkWriteAudit, row_id)
        assert survivor is not None, "the audit row must survive the job's deletion"
        assert survivor.job_id is None, "ondelete='SET NULL' must have fired"
        assert survivor.pppoe_username == "bach1"
    finally:
        session.close()
        engine.dispose()


def test_deleting_the_customer_nulls_the_audit_rows_customer_id_under_real_fk_enforcement():
    """Same reasoning as the job test above, for the customer_id FK: a
    business deletes exactly the customers who have churned, and those are
    exactly the ones most likely to have suspend history. Under the database
    default (NO ACTION) that history would make deleting them raise
    ForeignKeyViolation on production Postgres, invisibly passing on SQLite's
    default of not enforcing FKs at all."""
    engine = _sqlite_engine_with_fk_enforcement()
    session = sessionmaker(bind=engine)()
    try:
        tenant = appmod.Tenant(name="Audit FK Cust", slug="audit-fk-cust",
                               status="active", plan="free")
        session.add(tenant)
        session.add(appmod.Currency(code="USD", name="US Dollar", decimal_places=2))
        session.commit()
        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Basic", price=10, cost=5,
            billing_cycle="monthly", currency="USD")
        session.add(plan)
        session.commit()
        customer = appmod.Customer(
            tenant_id=tenant.id, name="Bach", phone="1", address="a",
            subscription_plan_id=plan.id,
            subscription_expiry_date=datetime.utcnow(),
            pppoe_username="bach1")
        session.add(customer)
        session.commit()
        row = appmod.NetworkWriteAudit(
            tenant_id=tenant.id, customer_id=customer.id, pppoe_username="bach1",
            action="suspend", outcome="ok")
        session.add(row)
        session.commit()
        row_id, customer_id = row.id, customer.id

        session.delete(session.get(appmod.Customer, customer_id))
        session.commit()
        session.close()  # drop the identity map -- the next read must hit the DB

        session = sessionmaker(bind=engine)()
        survivor = session.get(appmod.NetworkWriteAudit, row_id)
        assert survivor is not None, "the audit row must survive the customer's deletion"
        assert survivor.customer_id is None, "ondelete='SET NULL' must have fired"
        assert survivor.pppoe_username == "bach1", (
            "pppoe_username is recorded as sent, not looked up later -- it "
            "must survive independently of the customer row")
    finally:
        session.close()
        engine.dispose()


def test_deleting_the_user_nulls_the_id_but_the_username_survives_under_real_fk_enforcement():
    """The finding this closes: DELETE /api/users/<int:user_id> hard-deletes a
    User row during routine staff offboarding, and ondelete='SET NULL' on
    requested_by_user_id degrades that FK exactly the way it degrades
    customer_id/network_device_id/job_id above. But requested_by_user_id
    carries "who" semantics the other three don't -- NULL there is defined by
    the spec to mean the automatic restore, nobody clicked it. Without a
    separate snapshot, a deleted user's old audit rows would read exactly like
    that automatic restore, erasing the "who" from the trail. This proves the
    snapshot actually survives the deletion that erases the FK: it must still
    read the deleted user's username after requested_by_user_id has gone
    NULL."""
    engine = _sqlite_engine_with_fk_enforcement()
    session = sessionmaker(bind=engine)()
    try:
        tenant = appmod.Tenant(name="Audit FK User", slug="audit-fk-user",
                               status="active", plan="free")
        session.add(tenant)
        session.commit()
        user = appmod.User(username="audit_fk_user_admin", role="admin",
                           tenant_id=tenant.id)
        user.set_password("pw")
        session.add(user)
        session.commit()
        row = appmod.NetworkWriteAudit(
            tenant_id=tenant.id, pppoe_username="bach1", action="suspend",
            requested_by_user_id=user.id,
            requested_by_username=user.username, outcome="ok")
        session.add(row)
        session.commit()
        row_id, user_id = row.id, user.id

        session.delete(session.get(appmod.User, user_id))
        session.commit()
        session.close()  # drop the identity map -- the next read must hit the DB

        session = sessionmaker(bind=engine)()
        survivor = session.get(appmod.NetworkWriteAudit, row_id)
        assert survivor is not None, "the audit row must survive the user's deletion"
        assert survivor.requested_by_user_id is None, "ondelete='SET NULL' must have fired"
        assert survivor.requested_by_username == "audit_fk_user_admin", (
            "requested_by_username is recorded as sent, not looked up later -- "
            "it must survive independently of the user row. That is the whole "
            "point: it is what still distinguishes 'a human did this and "
            "their account is gone' from 'no human was involved'")
    finally:
        session.close()
        engine.dispose()


def test_the_audit_model_is_tenant_owned_and_deleted_first(app):
    """It has FKs to tenant, customer, network_device, user and
    network_agent_job, so it must be deleted before every one of them."""
    assert appmod.NetworkWriteAudit in appmod.TENANT_OWNED_MODELS
    order = appmod._TENANT_DELETE_ORDER
    assert appmod.NetworkWriteAudit in order
    audit_at = order.index(appmod.NetworkWriteAudit)
    for parent in (appmod.Customer, appmod.NetworkDevice,
                   appmod.NetworkAgentJob):
        assert audit_at < order.index(parent), (
            "NetworkWriteAudit must be deleted before {}".format(parent.__name__))


def _set_agent_version(app, tenant_name, version):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant.id).first()
        if agent is None:
            agent = appmod.NetworkAgent(tenant_id=tenant.id, name="Box", token_hash="x")
            appmod.db.session.add(agent)
        agent.agent_version = version
        agent.last_seen_at = appmod.datetime.utcnow()
        appmod.db.session.commit()


def test_suspend_still_writes_inline_in_direct_mode(app, client, monkeypatch):
    calls = []
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda d, u, enabled: calls.append((u, enabled)) or (True, "disabled"))
    hdr = _admin(client, "Wr A", "wr_a_admin")
    device_id = make_device(app, "Wr A")
    customer_id = _linked_customer(app, "Wr A", device_id)

    r = client.post("/api/customers/{}/network-suspend".format(customer_id), headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert calls == [("bach1", False)]

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(customer_id=customer_id).one()
        assert (row.action, row.outcome, row.job_id) == ("suspend", "ok", None)


def test_suspend_queues_a_job_in_agent_mode(app, client, monkeypatch):
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr B", "wr_b_admin")
    device_id = make_device(app, "Wr B")
    customer_id = _linked_customer(app, "Wr B", device_id)
    _set_agent_mode(app, "Wr B")
    _set_agent_version(app, "Wr B", "1.3.0")

    r = client.post("/api/customers/{}/network-suspend".format(customer_id), headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["job_id"] is not None

    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, body["job_id"])
        assert job.operation == "suspend_secret"
        assert job.params == {"pppoe_username": "bach1"}
        row = appmod.NetworkWriteAudit.query.filter_by(customer_id=customer_id).one()
        assert (row.action, row.outcome, row.job_id) == ("suspend", "queued", job.id)


def test_an_old_agent_is_refused_before_a_job_is_created(app, client, monkeypatch):
    """Otherwise the user clicks Suspend, waits, and gets a refusal from the
    box with no idea why."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr C", "wr_c_admin")
    device_id = make_device(app, "Wr C")
    customer_id = _linked_customer(app, "Wr C", device_id)
    _set_agent_mode(app, "Wr C")
    _set_agent_version(app, "Wr C", "1.2.0")

    r = client.post("/api/customers/{}/network-suspend".format(customer_id), headers=hdr)
    assert r.get_json()["ok"] is False
    assert "1.3.0" in r.get_json()["message"]
    with app.app_context():
        assert appmod.NetworkAgentJob.query.count() == 0
        assert appmod.NetworkWriteAudit.query.count() == 0


def test_unsuspend_queues_the_unsuspend_operation(app, client, monkeypatch):
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr D", "wr_d_admin")
    device_id = make_device(app, "Wr D")
    customer_id = _linked_customer(app, "Wr D", device_id)
    _set_agent_mode(app, "Wr D")
    _set_agent_version(app, "Wr D", "1.3.0")

    r = client.post("/api/customers/{}/network-unsuspend".format(customer_id), headers=hdr)
    job_id = r.get_json()["job_id"]
    with app.app_context():
        assert appmod.db.session.get(appmod.NetworkAgentJob, job_id).operation == "unsuspend_secret"
        assert appmod.NetworkWriteAudit.query.one().action == "unsuspend"


def test_the_audit_row_is_completed_when_the_agent_reports_back(app, client, monkeypatch):
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr E", "wr_e_admin")
    device_id = make_device(app, "Wr E")
    customer_id = _linked_customer(app, "Wr E", device_id)
    _set_agent_mode(app, "Wr E")
    _set_agent_version(app, "Wr E", "1.3.0")

    job_id = client.post("/api/customers/{}/network-suspend".format(customer_id),
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Wr E").first()
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant.id).first()
        token = appmod._issue_agent_token(agent)
        job = appmod.db.session.get(appmod.NetworkAgentJob, job_id)
        job.status = "claimed"
        appmod.db.session.commit()

    client.post("/api/agent/jobs/{}/result".format(job_id),
                headers={"Authorization": "Bearer " + token},
                json={"ok": True, "result": "disabled"})

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(job_id=job_id).one()
        assert row.outcome == "ok"


def test_the_audit_row_is_completed_as_failed_when_the_agent_reports_a_failure(
        app, client, monkeypatch):
    """Sibling of the test above, for the other half of agent_post_result's
    ok/failure branch. Also the first test in this file to assert on
    audit.message, not just outcome."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr E2", "wr_e2_admin")
    device_id = make_device(app, "Wr E2")
    customer_id = _linked_customer(app, "Wr E2", device_id)
    _set_agent_mode(app, "Wr E2")
    _set_agent_version(app, "Wr E2", "1.3.0")

    job_id = client.post("/api/customers/{}/network-suspend".format(customer_id),
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Wr E2").first()
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant.id).first()
        token = appmod._issue_agent_token(agent)
        job = appmod.db.session.get(appmod.NetworkAgentJob, job_id)
        job.status = "claimed"
        appmod.db.session.commit()

    client.post("/api/agent/jobs/{}/result".format(job_id),
                headers={"Authorization": "Bearer " + token},
                json={"ok": False, "error": "no such secret"})

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(job_id=job_id).one()
        assert row.outcome == "failed"
        assert row.message == "no such secret"


def test_the_audit_row_is_completed_when_the_agent_posts_a_malformed_body(
        app, client, monkeypatch):
    """One of the Critical fix's five previously-stuck-'queued' routes: a
    non-object JSON body from the agent 400s and marks the job 'done' with an
    error, via the branch in agent_post_result well before the happy-path
    completion block -- that must complete the audit row too, not just the
    job."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr R", "wr_r_admin")
    device_id = make_device(app, "Wr R")
    customer_id = _linked_customer(app, "Wr R", device_id)
    _set_agent_mode(app, "Wr R")
    _set_agent_version(app, "Wr R", "1.3.0")

    job_id = client.post("/api/customers/{}/network-suspend".format(customer_id),
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Wr R").first()
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant.id).first()
        token = appmod._issue_agent_token(agent)
        job = appmod.db.session.get(appmod.NetworkAgentJob, job_id)
        job.status = "claimed"
        appmod.db.session.commit()

    r = client.post("/api/agent/jobs/{}/result".format(job_id),
                    headers={"Authorization": "Bearer " + token},
                    json=["not", "an", "object"])
    assert r.status_code == 400

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(job_id=job_id).one()
        assert row.outcome == "failed"
        assert "expected a JSON object" in row.message


def test_expiring_a_never_claimed_write_job_completes_its_audit_row(
        app, client, monkeypatch):
    """Another of the Critical fix's five: a job no agent ever claimed within
    JOB_CLAIM_TIMEOUT_SECONDS goes 'expired' via _expire_job_if_stale's lazy
    pending-timeout branch -- that must complete the paired audit row too."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr S", "wr_s_admin")
    device_id = make_device(app, "Wr S")
    customer_id = _linked_customer(app, "Wr S", device_id)
    _set_agent_mode(app, "Wr S")
    _set_agent_version(app, "Wr S", "1.3.0")

    job_id = client.post("/api/customers/{}/network-suspend".format(customer_id),
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, job_id)
        job.created_at = appmod.datetime.utcnow() - appmod.timedelta(
            seconds=appmod.JOB_CLAIM_TIMEOUT_SECONDS + 1)
        appmod.db.session.commit()

        appmod._expire_job_if_stale(job)

        assert job.status == "expired"
        row = appmod.NetworkWriteAudit.query.filter_by(job_id=job_id).one()
        assert row.outcome == "failed"
        assert row.message == job.error


def test_expiring_a_claimed_write_job_completes_its_audit_row(app, client, monkeypatch):
    """Another of the Critical fix's five: a job claimed but never resolved
    within JOB_RESULT_TIMEOUT_SECONDS goes 'failed' via _expire_job_if_stale's
    lazy claimed-timeout branch -- the exact setup for the 409 race below,
    checked here on its own first."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr T", "wr_t_admin")
    device_id = make_device(app, "Wr T")
    customer_id = _linked_customer(app, "Wr T", device_id)
    _set_agent_mode(app, "Wr T")
    _set_agent_version(app, "Wr T", "1.3.0")

    job_id = client.post("/api/customers/{}/network-suspend".format(customer_id),
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, job_id)
        job.status = "claimed"
        job.claimed_at = appmod.datetime.utcnow() - appmod.timedelta(
            seconds=appmod.JOB_RESULT_TIMEOUT_SECONDS + 1)
        appmod.db.session.commit()

        appmod._expire_job_if_stale(job)

        assert job.status == "failed"
        row = appmod.NetworkWriteAudit.query.filter_by(job_id=job_id).one()
        assert row.outcome == "failed"
        assert row.message == job.error


def test_polling_for_a_job_whose_device_was_deleted_completes_its_audit_row(
        app, client, monkeypatch):
    """The last of the Critical fix's five: agent_poll_job's claim endpoint
    fails a job outright when its device vanished between creation and the
    agent's next poll -- that must complete the paired audit row too, not
    just the job. No existing test exercised this branch at all before this
    one, for any operation."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr U", "wr_u_admin")
    device_id = make_device(app, "Wr U")
    customer_id = _linked_customer(app, "Wr U", device_id)
    _set_agent_mode(app, "Wr U")
    _set_agent_version(app, "Wr U", "1.3.0")

    job_id = client.post("/api/customers/{}/network-suspend".format(customer_id),
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Wr U").first()
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant.id).first()
        token = appmod._issue_agent_token(agent)
        appmod.db.session.delete(appmod.db.session.get(appmod.NetworkDevice, device_id))
        appmod.db.session.commit()

    r = client.get("/api/agent/jobs", headers={"Authorization": "Bearer " + token,
                                               "X-Agent-Version": "1.3.0"})
    assert r.status_code == 204

    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, job_id)
        assert job.status == "failed"
        row = appmod.NetworkWriteAudit.query.filter_by(job_id=job_id).one()
        assert row.outcome == "failed"
        assert row.message == "Device no longer exists"


def test_a_late_agent_report_after_lazy_expiry_notes_the_discrepancy_but_does_not_overwrite_the_outcome(
        app, client, monkeypatch):
    """The Critical fix's worst case, straight from the review: the agent
    claims the job, performs the disconnect on the router, takes longer than
    JOB_RESULT_TIMEOUT_SECONDS, a browser's poll runs lazy expiry and closes
    the job (and now, its audit row) as 'failed' first, and only then does
    the agent's real result POST land -- rejected 409 because the job is no
    longer 'claimed'.

    The audit row must not be silently flipped to 'ok' on the agent's word
    alone (a process outside this application's trust boundary, reporting
    after the fact, with no way here to know which side is right) -- but the
    record must show a human reading it later that the agent DID report
    back, and what it said, rather than quietly dropping that information on
    the floor."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr V", "wr_v_admin")
    device_id = make_device(app, "Wr V")
    customer_id = _linked_customer(app, "Wr V", device_id)
    _set_agent_mode(app, "Wr V")
    _set_agent_version(app, "Wr V", "1.3.0")

    job_id = client.post("/api/customers/{}/network-suspend".format(customer_id),
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Wr V").first()
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant.id).first()
        token = appmod._issue_agent_token(agent)
        job = appmod.db.session.get(appmod.NetworkAgentJob, job_id)
        job.status = "claimed"
        job.claimed_at = appmod.datetime.utcnow() - appmod.timedelta(
            seconds=appmod.JOB_RESULT_TIMEOUT_SECONDS + 1)
        appmod.db.session.commit()

        # A browser's poll of GET /api/network-jobs/<id> would trigger this
        # same lazy expiry; called directly here for a test that doesn't
        # otherwise need network_view_required's role wiring in scope.
        appmod._expire_job_if_stale(job)
        assert job.status == "failed"

    r = client.post("/api/agent/jobs/{}/result".format(job_id),
                    headers={"Authorization": "Bearer " + token},
                    json={"ok": True, "result": "disabled"})
    assert r.status_code == 409

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(job_id=job_id).one()
        assert row.outcome == "failed", (
            "must not be overwritten to 'ok' on the late-arriving agent's "
            "word alone")
        assert "never reported back" in row.message, (
            "the original lazy-expiry message must survive, not be replaced")
        assert "Agent reported back (ok)" in row.message, (
            "the discrepancy must be recorded for a human to find later")


def test_the_payment_restore_queues_without_blocking_in_agent_mode(app, client, monkeypatch):
    """Runs after every settling payment on a single synchronous worker, so it
    must queue and return, never wait."""
    stub_connectors(monkeypatch)
    never = []
    monkeypatch.setattr(appmod.mikrotik, "get_secret_status",
                        lambda d, u: never.append(u) or (True, "disabled"))
    _admin(client, "Wr F", "wr_f_admin")
    device_id = make_device(app, "Wr F")
    customer_id = _linked_customer(app, "Wr F", device_id)
    _set_agent_mode(app, "Wr F")
    _set_agent_version(app, "Wr F", "1.3.0")

    with app.app_context():
        # _maybe_restore_mikrotik_access calls _tenant_access_mode(), which --
        # like every tenant_query call -- needs a verified JWT in scope
        # (tenancy.current_tenant_id() reads get_jwt()). Same requirement,
        # same fix, as test_payment_restore_skips_in_agent_mode in
        # tests/test_mikrotik_consolidation.py: manufacture a verified-JWT
        # request context rather than calling the bare function.
        tenant = appmod.Tenant.query.filter_by(name="Wr F").first()
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        token = create_access_token(identity="wr_f_admin",
                                    additional_claims={"tenant_id": tenant.id})
        with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
            verify_jwt_in_request()
            result = appmod._maybe_restore_mikrotik_access(customer)
        assert result is not None and result.get("queued") is True
        job = appmod.NetworkAgentJob.query.filter_by(operation="unsuspend_secret").one()
        assert job.params == {"pppoe_username": "bach1"}
        row = appmod.NetworkWriteAudit.query.one()
        assert row.requested_by_user_id is None, "nobody clicked it"
    assert never == [], "agent mode must not spend a round trip reading status first"


def test_the_payment_restore_still_checks_status_first_in_direct_mode(app, client, monkeypatch):
    """The check avoids a pointless write, and in direct mode it costs nothing."""
    monkeypatch.setattr(appmod.mikrotik, "get_secret_status", lambda d, u: (True, "enabled"))
    wrote = []
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda d, u, enabled: wrote.append(u) or (True, "ok"))
    _admin(client, "Wr G", "wr_g_admin")
    device_id = make_device(app, "Wr G")
    customer_id = _linked_customer(app, "Wr G", device_id)

    with app.app_context():
        # Same JWT-context requirement as the agent-mode test above -- direct
        # mode's _tenant_access_mode() call needs it just as much.
        tenant = appmod.Tenant.query.filter_by(name="Wr G").first()
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        token = create_access_token(identity="wr_g_admin",
                                    additional_claims={"tenant_id": tenant.id})
        with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
            verify_jwt_in_request()
            result = appmod._maybe_restore_mikrotik_access(customer)
        assert result is None
    assert wrote == [], "an already-enabled secret needs no write in direct mode"


def test_suspend_snapshots_the_requesting_username(app, client, monkeypatch):
    """requested_by_username is a column Task 3 added after this plan was
    written -- the brief's _record_write_audit predates it and does not set
    it. It must come from the same resolved user as requested_by_user_id: a
    snapshot that stays NULL forever would look like accountability while
    providing none. See NetworkWriteAudit.requested_by_username and
    test_deleting_the_user_nulls_the_id_but_the_username_survives_under_real_fk_enforcement
    above for why the snapshot exists; this proves the write path actually
    fills it in, not just that the column exists."""
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda d, u, enabled: (True, "disabled"))
    hdr = _admin(client, "Wr H", "wr_h_admin")
    device_id = make_device(app, "Wr H")
    customer_id = _linked_customer(app, "Wr H", device_id)

    r = client.post("/api/customers/{}/network-suspend".format(customer_id), headers=hdr)
    assert r.status_code == 200

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(customer_id=customer_id).one()
        assert row.requested_by_user_id is not None
        assert row.requested_by_username == "wr_h_admin"


def test_suspend_snapshots_the_requesting_username_in_agent_mode(app, client, monkeypatch):
    """Mirrors test_suspend_snapshots_the_requesting_username for the queued
    (agent-mode) branch of _perform_customer_write -- a separate
    _record_write_audit call site that must not be missed."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr I", "wr_i_admin")
    device_id = make_device(app, "Wr I")
    customer_id = _linked_customer(app, "Wr I", device_id)
    _set_agent_mode(app, "Wr I")
    _set_agent_version(app, "Wr I", "1.3.0")

    r = client.post("/api/customers/{}/network-suspend".format(customer_id), headers=hdr)
    assert r.status_code == 200

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(customer_id=customer_id).one()
        assert row.requested_by_user_id is not None
        assert row.requested_by_username == "wr_i_admin"


def test_the_automatic_restore_never_snapshots_a_username(app, client, monkeypatch):
    """The flip side of the two tests above: the automatic, payment-triggered
    restore has no acting user, and requested_by_username must stay NULL
    exactly when requested_by_user_id does -- never populated from something
    else (e.g. the agent, or the last staff member to touch the customer).
    test_the_payment_restore_queues_without_blocking_in_agent_mode above
    already checks requested_by_user_id is None for the agent-mode queue path;
    this is the direct-mode counterpart, and both check the username too."""
    monkeypatch.setattr(appmod.mikrotik, "get_secret_status", lambda d, u: (True, "disabled"))
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda d, u, enabled: (True, "disabled"))
    _admin(client, "Wr J", "wr_j_admin")
    device_id = make_device(app, "Wr J")
    customer_id = _linked_customer(app, "Wr J", device_id)

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Wr J").first()
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        token = create_access_token(identity="wr_j_admin",
                                    additional_claims={"tenant_id": tenant.id})
        with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
            verify_jwt_in_request()
            result = appmod._maybe_restore_mikrotik_access(customer)
        assert result["ok"] is True
        row = appmod.NetworkWriteAudit.query.filter_by(customer_id=customer_id).one()
        assert row.requested_by_user_id is None
        assert row.requested_by_username is None, "nobody clicked it"


def test_create_device_job_with_commit_false_leaves_the_job_rollback_able(
        app, client, monkeypatch):
    """Mechanism regression for the Important finding on
    _record_write_audit's docstring: _create_device_job used to commit the
    job on its own -- durable, claimable, and actionable -- before
    _record_write_audit (and the caller's own commit) ever ran. A failure in
    between would leave a queued, actionable job with no audit row at all,
    the one outcome this table exists to prevent. commit=False defers to a
    flush instead (job.id is still real and usable), so the job is only ever
    committed together with whatever the caller adds afterward.

    Deliberately does NOT prove this by raising inside an HTTP request and
    checking the job disappears afterward: this test harness's `app` fixture
    keeps one ambient app context -- and therefore one db.session -- open for
    the whole test (see conftest.py), so Flask's own per-request teardown
    (which is what would roll back an uncommitted session after a real
    unhandled exception in production) never actually runs between requests
    inside a test. That would make such a test pass regardless of whether
    _create_device_job honoured commit=False at all -- confirmed empirically
    while writing this test: forcing an exception from a monkeypatched
    _record_write_audit left the job durable and queryable afterward even
    though nothing had explicitly committed it. Calling _create_device_job
    directly and rolling back explicitly, instead, tests the actual mechanism
    (flush vs. commit) without depending on that harness quirk.
    """
    stub_connectors(monkeypatch)
    _admin(client, "Wr W", "wr_w_admin")
    device_id = make_device(app, "Wr W")
    _set_agent_mode(app, "Wr W")
    _set_agent_version(app, "Wr W", "1.3.0")

    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Wr W").first()
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        # _create_device_job calls get_jwt_identity() and, via
        # _tenant_access_mode()/tenant_query, current_tenant_id() -> get_jwt()
        # -- both need a verified JWT in scope, same requirement and same fix
        # as the payment-restore tests above.
        token = create_access_token(identity="wr_w_admin",
                                    additional_claims={"tenant_id": tenant.id})
        with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
            verify_jwt_in_request()
            job, error = appmod._create_device_job(
                device, "suspend_secret", {"pppoe_username": "bach1"}, commit=False)
        assert error is None
        assert job.id is not None, "flush must still populate the primary key"
        assert appmod.db.session.get(appmod.NetworkAgentJob, job.id) is not None, (
            "must be visible within the still-open transaction -- this is "
            "what lets _record_write_audit reference job.id before the "
            "caller's own commit")

        appmod.db.session.rollback()

        assert appmod.db.session.get(appmod.NetworkAgentJob, job.id) is None, (
            "commit=False must leave the job merely flushed, not already "
            "durable on its own -- otherwise a failure before the caller's "
            "own commit would strand a queued, actionable job with no "
            "paired audit row, exactly what this table exists to prevent")


def test_a_deleted_staff_account_still_snapshots_a_username(app, client, monkeypatch):
    """Regression for Important finding 2: requester_name used to fall back
    to None on a User.query lookup miss, which is exactly the encoding
    NetworkWriteAudit reserves for "nobody clicked it" (the automatic
    restore) -- collapsing a human's suspend/unsuspend into indistinguishable-
    from-automatic for up to JWT_ACCESS_TOKEN_EXPIRES (8h, config.py) after
    their account is deleted. Reproduces that window directly: get a valid
    token for an admin, delete that admin's User row (routine staff
    offboarding), and use the still-valid token to suspend --
    get_jwt_identity() still returns the username from the token itself even
    though the User row backing it is gone, so requester_name must fall back
    to that rather than to None."""
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda d, u, enabled: (True, "disabled"))
    hdr = _admin(client, "Wr K", "wr_k_admin")
    device_id = make_device(app, "Wr K")
    customer_id = _linked_customer(app, "Wr K", device_id)

    with app.app_context():
        user = appmod.User.query.filter_by(username="wr_k_admin").first()
        appmod.db.session.delete(user)
        appmod.db.session.commit()

    r = client.post("/api/customers/{}/network-suspend".format(customer_id), headers=hdr)
    assert r.status_code == 200, "a deleted user's still-valid token must not break the write"

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(customer_id=customer_id).one()
        assert row.requested_by_user_id is None, "the User row is gone"
        assert row.requested_by_username == "wr_k_admin", (
            "must fall back to get_jwt_identity() rather than collapsing to "
            "None -- which is reserved for 'nobody clicked it'")
