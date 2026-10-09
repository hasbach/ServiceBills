"""tenant.contact_phone and platform_payment table

Revision ID: 8d7d5006a5d8
Revises: e51992e09bc3
Create Date: 2026-10-10
"""
import time

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError


revision = '8d7d5006a5d8'
down_revision = 'e51992e09bc3'
branch_labels = None
depends_on = None


def _with_lock_retry(bind, ddl, attempts=60):
    """Run `ddl` on Postgres without queueing behind the live app (see
    e51992e09bc3): short lock_timeout inside a savepoint, retried until a gap
    opens, instead of waiting until statement_timeout kills the deploy."""
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
    insp = inspect(bind)

    if 'contact_phone' not in {c['name'] for c in insp.get_columns('tenant')}:
        if pg:
            _with_lock_retry(bind, lambda: bind.execute(text(
                "ALTER TABLE tenant ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(30)")))
        else:
            with op.batch_alter_table('tenant') as batch:
                batch.add_column(sa.Column('contact_phone', sa.String(length=30), nullable=True))

    if 'platform_payment' not in insp.get_table_names():
        def create_platform_payment():
            op.create_table(
                'platform_payment',
                sa.Column('id', sa.Integer(), primary_key=True),
                sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
                sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
                sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
                sa.Column('method', sa.String(length=20), nullable=False),
                sa.Column('period', sa.String(length=20), nullable=True),
                sa.Column('note', sa.String(length=200), nullable=True),
                sa.Column('recorded_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
                sa.Column('created_at', sa.DateTime(), nullable=False),
            )
            op.create_index('ix_platform_payment_tenant_id', 'platform_payment', ['tenant_id'])

        # The foreign keys briefly lock tenant/user too.
        if pg:
            _with_lock_retry(bind, create_platform_payment)
        else:
            create_platform_payment()


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if 'platform_payment' in insp.get_table_names():
        op.drop_index('ix_platform_payment_tenant_id', table_name='platform_payment')
        op.drop_table('platform_payment')
    if 'contact_phone' in {c['name'] for c in insp.get_columns('tenant')}:
        with op.batch_alter_table('tenant') as batch:
            batch.drop_column('contact_phone')
