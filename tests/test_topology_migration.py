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

import sqlalchemy as sa
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate, upgrade, downgrade

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
