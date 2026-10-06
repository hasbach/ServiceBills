"""day_close table and business_settings.timezone

Revision ID: e51992e09bc3
Revises: e0428098679e
Create Date: 2026-10-06
"""
import time

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError


revision = 'e51992e09bc3'
down_revision = 'e0428098679e'
branch_labels = None
depends_on = None


def _with_lock_retry(bind, ddl, attempts=60):
    """Run `ddl` on Postgres without queueing behind the live app.

    Production deploys run this while the previous instance still serves
    traffic. A plain ALTER waits for its ACCESS EXCLUSIVE lock until the role's
    statement_timeout cancels the whole migration (2026-10-06 incident), and
    while it waits every app query on the table queues behind it. Instead try
    with a short lock_timeout inside a savepoint and retry until a gap opens."""
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
    insp = inspect(bind)
    cols = {c['name'] for c in insp.get_columns('business_settings')}
    if 'timezone' not in cols:
        if bind.dialect.name == 'postgresql':
            _with_lock_retry(bind, lambda: bind.execute(text(
                "ALTER TABLE business_settings ADD COLUMN IF NOT EXISTS "
                "timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Beirut'")))
        else:
            with op.batch_alter_table('business_settings') as batch:
                batch.add_column(sa.Column('timezone', sa.String(length=64), nullable=False,
                                           server_default='Asia/Beirut'))
    if 'day_close' not in insp.get_table_names():
        def create_day_close():
            op.create_table(
                'day_close',
                sa.Column('id', sa.Integer(), primary_key=True),
                sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
                sa.Column('day', sa.Date(), nullable=False),
                sa.Column('expected_cash', sa.Numeric(precision=18, scale=4), nullable=False),
                sa.Column('counted_cash', sa.Numeric(precision=18, scale=4), nullable=False),
                sa.Column('expected_whish', sa.Numeric(precision=18, scale=4), nullable=False),
                sa.Column('counted_whish', sa.Numeric(precision=18, scale=4), nullable=False),
                sa.Column('note', sa.String(length=200), nullable=True),
                sa.Column('closed_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
                sa.Column('closed_at', sa.DateTime(), nullable=False),
                sa.UniqueConstraint('tenant_id', 'day', name='uq_day_close_tenant_day'),
            )
            op.create_index('ix_day_close_tenant_id', 'day_close', ['tenant_id'])

        # The foreign keys briefly lock tenant/user too.
        if bind.dialect.name == 'postgresql':
            _with_lock_retry(bind, create_day_close)
        else:
            create_day_close()


def downgrade():
    insp = inspect(op.get_bind())
    cols = {c['name'] for c in insp.get_columns('business_settings')}
    if 'timezone' in cols:
        with op.batch_alter_table('business_settings') as batch:
            batch.drop_column('timezone')
    if 'day_close' in insp.get_table_names():
        op.drop_index('ix_day_close_tenant_id', table_name='day_close')
        op.drop_table('day_close')
