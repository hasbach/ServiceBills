"""day_close table and business_settings.timezone

Revision ID: e51992e09bc3
Revises: e0428098679e
Create Date: 2026-10-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'e51992e09bc3'
down_revision = 'e0428098679e'
branch_labels = None
depends_on = None


def upgrade():
    insp = inspect(op.get_bind())
    if 'day_close' not in insp.get_table_names():
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
    cols = {c['name'] for c in insp.get_columns('business_settings')}
    if 'timezone' not in cols:
        with op.batch_alter_table('business_settings') as batch:
            batch.add_column(sa.Column('timezone', sa.String(length=64), nullable=False,
                                       server_default='Asia/Beirut'))


def downgrade():
    insp = inspect(op.get_bind())
    cols = {c['name'] for c in insp.get_columns('business_settings')}
    if 'timezone' in cols:
        with op.batch_alter_table('business_settings') as batch:
            batch.drop_column('timezone')
    if 'day_close' in insp.get_table_names():
        op.drop_index('ix_day_close_tenant_id', table_name='day_close')
        op.drop_table('day_close')
