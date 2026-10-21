"""cs agent: per-tenant tool secret

Revision ID: 18c24f5699dc
Revises: 6bf7151ee83f
Create Date: 2026-10-21

The ElevenLabs tool webhooks used one platform-wide CS_AGENT_SECRET and then
trusted the tenant_id in the request. Each tenant now gets its own secret;
only its SHA-256 is stored, and the tenant is looked up by that hash.
Additive only; every existing row starts with no secret (legacy fallback).
cs_agent_settings is read on every tool call, so ALTERs use the
lock_timeout + savepoint retry.
"""
import time

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError


revision = '18c24f5699dc'
down_revision = '6bf7151ee83f'
branch_labels = None
depends_on = None

# Must equal CSAgentSettings.__table__.name (pinned by a test).
TABLE = 'cs_agent_settings'
INDEX = 'ix_cs_agent_settings_tool_secret_hash'

# (name, postgres DDL type, sqlalchemy type)
_COLUMNS = (
    ('tool_secret_hash', 'VARCHAR(64)', sa.String(length=64)),
    ('tool_secret_created_at', 'TIMESTAMP WITHOUT TIME ZONE', sa.DateTime()),
)


def _with_lock_retry(bind, ddl, attempts=60):
    """Run `ddl` on Postgres without queueing behind the live app: short
    lock_timeout inside a savepoint, retried until a gap opens."""
    for attempt in range(attempts):
        savepoint = bind.begin_nested()
        try:
            bind.execute(text("SET LOCAL lock_timeout = '2s'"))
            ddl()
            bind.execute(text("SET LOCAL lock_timeout = 0"))
            savepoint.commit()
            return
        except OperationalError as exc:
            savepoint.rollback()
            if 'lock timeout' not in str(exc).lower() or attempt == attempts - 1:
                raise
        time.sleep(2)


def upgrade():
    bind = op.get_bind()
    pg = bind.dialect.name == 'postgresql'

    existing = {c['name'] for c in inspect(bind).get_columns(TABLE)}
    for name, pg_type, sa_type in _COLUMNS:
        if name in existing:
            continue
        if pg:
            _with_lock_retry(bind, lambda: bind.execute(text(
                'ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}'.format(TABLE, name, pg_type))))
        else:
            with op.batch_alter_table(TABLE) as batch:
                batch.add_column(sa.Column(name, sa_type, nullable=True))

    if INDEX not in {ix['name'] for ix in inspect(bind).get_indexes(TABLE)}:
        # Unique: one hash -> one tenant. All rows are NULL today, so it can't fail.
        if pg:
            _with_lock_retry(bind, lambda: bind.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (tool_secret_hash)'.format(INDEX, TABLE))))
        else:
            op.create_index(INDEX, TABLE, ['tool_secret_hash'], unique=True)


def downgrade():
    bind = op.get_bind()
    if INDEX in {ix['name'] for ix in inspect(bind).get_indexes(TABLE)}:
        op.drop_index(INDEX, table_name=TABLE)
    existing = {c['name'] for c in inspect(bind).get_columns(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        for name, _, _ in _COLUMNS:
            if name in existing:
                batch.drop_column(name)
