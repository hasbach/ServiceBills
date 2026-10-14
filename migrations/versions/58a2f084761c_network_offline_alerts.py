"""network offline alerts: device/agent alert state + network_alert_event

Revision ID: 58a2f084761c
Revises: 8d7d5006a5d8
Create Date: 2026-10-14

See docs/superpowers/specs/2026-10-14-network-offline-alerts-design.md.
Additive only. network_agent is written on every agent poll (every few
seconds), so its ALTER goes through the lock_timeout + savepoint retry from
e51992e09bc3 instead of queueing behind the live app.
"""
import time

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError


revision = '58a2f084761c'
down_revision = '8d7d5006a5d8'
branch_labels = None
depends_on = None


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


_DEVICE_COLUMNS = (
    ('alert_status', 'VARCHAR(10)', sa.String(length=10)),
    ('failing_since', 'TIMESTAMP WITHOUT TIME ZONE', sa.DateTime()),
    ('onu_alert_state', 'JSON', sa.JSON()),
)


def upgrade():
    bind = op.get_bind()
    pg = bind.dialect.name == 'postgresql'
    insp = inspect(bind)

    device_cols = {c['name'] for c in insp.get_columns('network_device')}
    for name, pg_type, sa_type in _DEVICE_COLUMNS:
        if name in device_cols:
            continue
        if pg:
            _with_lock_retry(bind, lambda: bind.execute(text(
                'ALTER TABLE network_device ADD COLUMN IF NOT EXISTS {} {}'.format(name, pg_type))))
        else:
            with op.batch_alter_table('network_device') as batch:
                batch.add_column(sa.Column(name, sa_type, nullable=True))

    if 'offline_alerted_at' not in {c['name'] for c in insp.get_columns('network_agent')}:
        if pg:
            _with_lock_retry(bind, lambda: bind.execute(text(
                'ALTER TABLE network_agent ADD COLUMN IF NOT EXISTS offline_alerted_at '
                'TIMESTAMP WITHOUT TIME ZONE')))
        else:
            with op.batch_alter_table('network_agent') as batch:
                batch.add_column(sa.Column('offline_alerted_at', sa.DateTime(), nullable=True))

    if 'network_alert_event' not in insp.get_table_names():
        def create_event_table():
            op.create_table(
                'network_alert_event',
                sa.Column('id', sa.Integer(), primary_key=True),
                sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
                sa.Column('device_id', sa.Integer(),
                          sa.ForeignKey('network_device.id', ondelete='SET NULL'), nullable=True),
                sa.Column('kind', sa.String(length=20), nullable=False),
                sa.Column('message', sa.String(length=500), nullable=False),
                sa.Column('pushed', sa.Boolean(), nullable=False, server_default=sa.true()),
                sa.Column('created_at', sa.DateTime(), nullable=False),
            )
            op.create_index('ix_network_alert_event_tenant_id', 'network_alert_event', ['tenant_id'])

        # The foreign keys briefly lock tenant/network_device too.
        if pg:
            _with_lock_retry(bind, create_event_table)
        else:
            create_event_table()


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if 'network_alert_event' in insp.get_table_names():
        op.drop_index('ix_network_alert_event_tenant_id', table_name='network_alert_event')
        op.drop_table('network_alert_event')
    if 'offline_alerted_at' in {c['name'] for c in insp.get_columns('network_agent')}:
        with op.batch_alter_table('network_agent') as batch:
            batch.drop_column('offline_alerted_at')
    device_cols = {c['name'] for c in insp.get_columns('network_device')}
    with op.batch_alter_table('network_device') as batch:
        for name, _, _ in _DEVICE_COLUMNS:
            if name in device_cols:
                batch.drop_column(name)
