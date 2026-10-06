"""cash_entry and account_transfer tables (Finance section)

Revision ID: e0428098679e
Revises: 3fe48c293a2d
Create Date: 2026-10-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'e0428098679e'
down_revision = '3fe48c293a2d'
branch_labels = None
depends_on = None


def _tables():
    return set(inspect(op.get_bind()).get_table_names())


def upgrade():
    existing = _tables()
    if 'cash_entry' not in existing:
        op.create_table(
            'cash_entry',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('account', sa.String(length=10), nullable=False),
            sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
            sa.Column('reason', sa.String(length=200), nullable=False),
            sa.Column('date', sa.DateTime(), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_cash_entry_tenant_id', 'cash_entry', ['tenant_id'])
    if 'account_transfer' not in existing:
        op.create_table(
            'account_transfer',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('from_account', sa.String(length=10), nullable=False),
            sa.Column('to_account', sa.String(length=10), nullable=False),
            sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
            sa.Column('note', sa.String(length=200), nullable=True),
            sa.Column('date', sa.DateTime(), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_account_transfer_tenant_id', 'account_transfer', ['tenant_id'])


def downgrade():
    existing = _tables()
    if 'account_transfer' in existing:
        op.drop_table('account_transfer')
    if 'cash_entry' in existing:
        op.drop_table('cash_entry')
