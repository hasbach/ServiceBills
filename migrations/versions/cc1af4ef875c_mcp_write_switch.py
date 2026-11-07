"""mcp: write-tools switch + audit flag

Revision ID: cc1af4ef875c
Revises: 9af8c7a2648e
Create Date: 2026-11-07

business_settings.ai_connector_writes_enabled (off by default) gates the AI
connector's write tools; mcp_tool_call.wrote marks audit rows of write tools.
Additive only. Both tables are read on live requests, so Postgres ALTERs use
the lock_timeout + savepoint retry.
"""
import time

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

revision = 'cc1af4ef875c'
down_revision = '9af8c7a2648e'
branch_labels = None
depends_on = None

_COLUMNS = (('business_settings', 'ai_connector_writes_enabled'), ('mcp_tool_call', 'wrote'))


def _with_lock_retry(bind, ddl, attempts=60):
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
    for table, column in _COLUMNS:
        if column in {c['name'] for c in inspect(bind).get_columns(table)}:
            continue
        if pg:
            _with_lock_retry(bind, lambda t=table, c=column: bind.execute(text(
                'ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} BOOLEAN NOT NULL DEFAULT false'.format(t, c))))
        else:
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    bind = op.get_bind()
    for table, column in _COLUMNS:
        if column in {c['name'] for c in inspect(bind).get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
