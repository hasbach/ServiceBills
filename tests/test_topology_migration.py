"""The one test in this suite that actually drives Alembic (every other test
builds schema with db.create_all(), which never executes a migration's
upgrade()/downgrade() at all). This exercises the real migration chain
end-to-end against a throwaway on-disk SQLite file, ending at
e675c91c8685_add_network_topology_columns -- in particular its downgrade(),
which drops a self-referential foreign key via batch_alter_table and is
otherwise completely unexercised. See "Testing" in
docs/superpowers/specs/2026-09-01-network-topology-tree-design.md
("migration up/down").

Why a throwaway Flask app/db pair, not the shared `app`/`db` from app.py:
Flask-SQLAlchemy (3.x) caches one Engine per Flask app instance the first
time it's accessed, keyed by the app object itself, and never re-reads
SQLALCHEMY_DATABASE_URI for that app again -- confirmed empirically while
writing this test (mutating flask_app.config["SQLALCHEMY_DATABASE_URI"]
after the first access left db.engine pointed at the old URL). tests/conftest.py's
`app` fixture only ever points that shared engine at "sqlite:///:memory:", so
by the time this test runs, the shared `app`/`db` singletons already have an
in-memory engine wired up and permanently cached -- pointing them at a temp
file here would silently keep running everything against that stale
in-memory engine instead. A brand-new Flask app + SQLAlchemy() gets its own
never-before-accessed engine slot, so setting its config before first access
actually takes effect, and it can never collide with -- or fall back to --
the real app's engine or instance/database.db.
"""
import os
import shutil
import tempfile

import pytest
import sqlalchemy as sa
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate, upgrade, downgrade, stamp

import app as appmod

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")

# Pinned to this feature's own revision rather than "head" on purpose.
#
# After merging origin/main the chain forks: our line is
# 1282420125d2 -> 1c4fbef90530 -> e675c91c8685, while origin's Whish line runs
# 1282420125d2 -> ... -> bd054e2e7cf9 -> ..., and the two rejoin at the merge
# revision. Upgrading to "head" would therefore traverse bd054e2e7cf9, which
# calls op.create_unique_constraint outside batch mode -- unsupported by
# SQLite ("No support for ALTER of constraints in SQLite dialect"), though
# fine on the Postgres that production actually runs.
#
# That is a pre-existing limitation of origin's migration, not something this
# test should assert about. Targeting e675c91c8685 walks only our ancestry, so
# the test keeps doing its real job -- proving this feature's upgrade() and
# downgrade() genuinely work -- without being held hostage to an unrelated
# migration's SQLite incompatibility.
TOPOLOGY_REVISION = "e675c91c8685"


def _table_columns(engine, table_name):
    return {col["name"] for col in sa.inspect(engine).get_columns(table_name)}


def _table_names(engine):
    return set(sa.inspect(engine).get_table_names())


# The Layer 2 network-agent migration (see
# docs/superpowers/specs/2026-09-04-network-agent-layer-2-design.md), added on
# top of the merge revision 6129b0fb0885 -- the current Alembic head at the
# time this was written.
NETWORK_AGENT_REVISION = "5f65a6fd6e8d"

# Task 5 review fix: tightens network_agent.tenant_id from 5f65a6fd6e8d's
# plain, non-unique index to a unique constraint -- the schema-level backstop
# for "one agent per tenant" (see that migration file for the full story).
TENANT_UNIQUE_REVISION = "b71fe5010a39"


def test_migration_chain_adds_and_removes_topology_columns():
    """Upgrade the full chain to head against a fresh temp-file SQLite DB,
    assert the three new columns exist, downgrade one revision (this
    migration's own downgrade()), assert they are gone again, then
    upgrade back to head and assert they return -- a genuine round trip,
    not just a call that doesn't raise."""
    tmpdir = tempfile.mkdtemp(prefix="topology_migration_test_")
    db_path = os.path.join(tmpdir, "topology_migration.db")
    # A dedicated Flask app + SQLAlchemy instance, used only to give
    # Flask-Migrate's env.py (which reads
    # current_app.extensions['migrate'].db.engine) something to bind to --
    # see the module docstring for why the shared app/db can't be reused
    # here.
    mig_app = Flask("test_topology_migration")
    mig_app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + db_path.replace("\\", "/"))
    mig_db = SQLAlchemy(mig_app)
    Migrate(mig_app, mig_db, directory=MIGRATIONS_DIR, render_as_batch=True)

    try:
        with mig_app.app_context():
            upgrade(directory=MIGRATIONS_DIR, revision=TOPOLOGY_REVISION)
            engine = mig_db.engine

            device_cols = _table_columns(engine, "network_device")
            customer_cols = _table_columns(engine, "customer")
            assert "device_type" in device_cols
            assert "parent_device_id" in device_cols
            assert "onu_mac_address" in customer_cols

            downgrade(directory=MIGRATIONS_DIR, revision="-1")
            device_cols = _table_columns(engine, "network_device")
            customer_cols = _table_columns(engine, "customer")
            assert "device_type" not in device_cols
            assert "parent_device_id" not in device_cols
            assert "onu_mac_address" not in customer_cols

            upgrade(directory=MIGRATIONS_DIR, revision=TOPOLOGY_REVISION)
            device_cols = _table_columns(engine, "network_device")
            customer_cols = _table_columns(engine, "customer")
            assert "device_type" in device_cols
            assert "parent_device_id" in device_cols
            assert "onu_mac_address" in customer_cols

            engine.dispose()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_network_agent_migration_adds_and_removes_its_tables_and_column():
    """Proves NETWORK_AGENT_REVISION's upgrade()/downgrade() genuinely work,
    the same way test_migration_chain_adds_and_removes_topology_columns()
    does for the topology migration above -- but bootstrapped differently.

    NETWORK_AGENT_REVISION's down_revision is 6129b0fb0885, the merge of the
    topology and WhatsApp-template lines. Reaching that merge means walking
    the WhatsApp/Whish branch too, and that branch contains bd054e2e7cf9,
    which calls op.create_unique_constraint outside batch mode -- exactly
    the SQLite incompatibility TOPOLOGY_REVISION above was pinned to avoid
    (see its comment). Unlike that test, we can't dodge it by picking an
    earlier revision: our migration's parent IS the merge revision, so any
    real walk from base to NETWORK_AGENT_REVISION crosses it regardless
    (confirmed empirically -- `upgrade(revision=NETWORK_AGENT_REVISION)`
    from an empty db raises the same "No support for ALTER of constraints
    in SQLite dialect" error).

    So instead of walking history, this builds the pre-migration schema
    directly from the current ORM models (db.metadata.create_all(), minus
    the two tables and the one column this migration itself adds -- i.e.
    exactly the schema shape at 6129b0fb0885), stamps the db at that
    revision, and upgrades/downgrades/upgrades from there. Alembic still
    executes this migration's real upgrade()/downgrade() through the real
    SQLite engine; only the unrelated, pre-existing, Postgres-only ancestor
    migration is skipped, matching the same "not this test's job" reasoning
    as TOPOLOGY_REVISION's comment.
    """
    tmpdir = tempfile.mkdtemp(prefix="network_agent_migration_test_")
    db_path = os.path.join(tmpdir, "network_agent_migration.db")
    mig_app = Flask("test_network_agent_migration")
    mig_app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + db_path.replace("\\", "/"))
    mig_db = SQLAlchemy(mig_app)
    Migrate(mig_app, mig_db, directory=MIGRATIONS_DIR, render_as_batch=True)

    try:
        with mig_app.app_context():
            engine = mig_db.engine

            # Build the schema as it stood at 6129b0fb0885: every current
            # model's table except the two this migration adds, and without
            # the one column it adds to business_settings.
            pre_tables = [t for name, t in appmod.db.metadata.tables.items()
                         if name not in ("network_agent", "network_agent_job")]
            appmod.db.metadata.create_all(bind=engine, tables=pre_tables)
            with engine.begin() as conn:
                conn.execute(sa.text(
                    "ALTER TABLE business_settings DROP COLUMN network_access_mode"))
            stamp(directory=MIGRATIONS_DIR, revision="6129b0fb0885")

            upgrade(directory=MIGRATIONS_DIR, revision=NETWORK_AGENT_REVISION)
            assert "network_agent" in _table_names(engine)
            assert "network_agent_job" in _table_names(engine)
            assert "network_access_mode" in _table_columns(engine, "business_settings")

            downgrade(directory=MIGRATIONS_DIR, revision="-1")
            assert "network_agent" not in _table_names(engine)
            assert "network_agent_job" not in _table_names(engine)
            assert "network_access_mode" not in _table_columns(engine, "business_settings")

            upgrade(directory=MIGRATIONS_DIR, revision=NETWORK_AGENT_REVISION)
            assert "network_agent" in _table_names(engine)
            assert "network_agent_job" in _table_names(engine)
            assert "network_access_mode" in _table_columns(engine, "business_settings")

            engine.dispose()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _unique_constraint_columns(engine, table_name):
    return [tuple(uc["column_names"])
            for uc in sa.inspect(engine).get_unique_constraints(table_name)]


# The exact shape network_agent.tenant_id had right after 5f65a6fd6e8d's own
# upgrade(): a plain, non-unique index, not yet the unique constraint
# TENANT_UNIQUE_REVISION adds. Built by hand (rather than reusing
# appmod.db.metadata, which already reflects the *post*-fix model) so the
# bootstrapped schema below genuinely represents the pre-migration state.
_PRE_UNIQUE_METADATA = sa.MetaData()
# A stub, uncreated 'tenant' table -- present only so the FK below can
# resolve its target within this standalone MetaData at DDL-compile time.
# The real tenant table already exists in the target DB by the time
# _pre_unique_network_agent.create() runs (via appmod.db.metadata.create_all()
# below); this Table object is never itself .create()'d.
sa.Table('tenant', _PRE_UNIQUE_METADATA, sa.Column('id', sa.Integer, primary_key=True))
_pre_unique_network_agent = sa.Table(
    'network_agent', _PRE_UNIQUE_METADATA,
    sa.Column('id', sa.Integer, primary_key=True),
    sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenant.id'), nullable=False),
    sa.Column('name', sa.String(100), nullable=False),
    sa.Column('token_hash', sa.String(255), nullable=False),
    sa.Column('last_seen_at', sa.DateTime()),
    sa.Column('agent_version', sa.String(20)),
    sa.Column('created_at', sa.DateTime()),
    sa.Index('ix_network_agent_tenant_id', 'tenant_id', unique=False),
)


def test_network_agent_tenant_unique_migration_adds_and_removes_the_constraint():
    """Proves TENANT_UNIQUE_REVISION's upgrade()/downgrade() genuinely turn
    network_agent.tenant_id's plain index into a real unique constraint and
    back -- the schema-level backstop for "one agent per tenant" (Task 5
    review finding 2: the create route's pre-insert check alone can't stop
    two concurrent creates from both inserting).

    Bootstrapped the same way test_network_agent_migration_adds_and_removes_its_tables_and_column
    above is: walking the real chain from base hits bd054e2e7cf9's
    op.create_unique_constraint outside batch mode, unsupported by SQLite.
    TENANT_UNIQUE_REVISION's own parent is 5f65a6fd6e8d (not the merge), but
    getting there still means walking through it, so the same dodge applies:
    build the pre-migration schema directly (every current table except
    network_agent, which is rebuilt by hand in its pre-fix shape), stamp at
    5f65a6fd6e8d, then let Alembic run this migration's real upgrade()/
    downgrade() from there.
    """
    tmpdir = tempfile.mkdtemp(prefix="network_agent_unique_migration_test_")
    db_path = os.path.join(tmpdir, "network_agent_unique_migration.db")
    mig_app = Flask("test_network_agent_unique_migration")
    mig_app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + db_path.replace("\\", "/"))
    mig_db = SQLAlchemy(mig_app)
    Migrate(mig_app, mig_db, directory=MIGRATIONS_DIR, render_as_batch=True)

    try:
        with mig_app.app_context():
            engine = mig_db.engine

            pre_tables = [t for name, t in appmod.db.metadata.tables.items()
                         if name != 'network_agent']
            appmod.db.metadata.create_all(bind=engine, tables=pre_tables)
            _pre_unique_network_agent.create(bind=engine)
            stamp(directory=MIGRATIONS_DIR, revision=NETWORK_AGENT_REVISION)

            with engine.begin() as conn:
                conn.execute(sa.text(
                    "INSERT INTO tenant (id, name, slug, status, plan) "
                    "VALUES (1, 'T', 't-slug', 'active', 'free')"))

            def _insert_two_agents_for_the_same_tenant():
                with engine.begin() as conn:
                    conn.execute(sa.text(
                        "INSERT INTO network_agent (tenant_id, name, token_hash) "
                        "VALUES (1, 'a', 'x')"))
                    conn.execute(sa.text(
                        "INSERT INTO network_agent (tenant_id, name, token_hash) "
                        "VALUES (1, 'b', 'y')"))

            def _clear_agents():
                with engine.begin() as conn:
                    conn.execute(sa.text("DELETE FROM network_agent"))

            # Pre-migration: no unique constraint yet, so a second agent for
            # the same tenant inserts cleanly -- exactly the gap the finding
            # describes.
            assert ('tenant_id',) not in _unique_constraint_columns(engine, "network_agent")
            _insert_two_agents_for_the_same_tenant()
            _clear_agents()

            upgrade(directory=MIGRATIONS_DIR, revision=TENANT_UNIQUE_REVISION)
            assert ('tenant_id',) in _unique_constraint_columns(engine, "network_agent")
            with pytest.raises(sa.exc.IntegrityError):
                _insert_two_agents_for_the_same_tenant()
            _clear_agents()

            downgrade(directory=MIGRATIONS_DIR, revision="-1")
            assert ('tenant_id',) not in _unique_constraint_columns(engine, "network_agent")
            _insert_two_agents_for_the_same_tenant()  # constraint gone again
            _clear_agents()

            upgrade(directory=MIGRATIONS_DIR, revision=TENANT_UNIQUE_REVISION)
            assert ('tenant_id',) in _unique_constraint_columns(engine, "network_agent")
            with pytest.raises(sa.exc.IntegrityError):
                _insert_two_agents_for_the_same_tenant()

            engine.dispose()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
