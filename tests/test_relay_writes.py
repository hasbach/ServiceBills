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

import app as appmod


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


from tests.conftest import make_tenant


def test_an_audit_row_records_who_did_what_to_whom(app, client):
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
