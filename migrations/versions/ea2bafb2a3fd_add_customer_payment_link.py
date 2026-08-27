"""add customer_payment_link table

Revision ID: ea2bafb2a3fd
Revises: bfcb70b813f1
Create Date: 2026-08-27

Per-customer, per-Payment Whish payment link -- see
docs/superpowers/specs/2026-08-27-tenant-whish-customer-payments-design.md.
Additive-only, defensive existence-check pattern per c57bc44a51d0's docstring.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'ea2bafb2a3fd'
down_revision = 'bfcb70b813f1'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'customer_payment_link' in set(inspector.get_table_names()):
        print("NOTE: customer_payment_link already exists -- skipping create (nothing to do).")
        return
    op.create_table(
        'customer_payment_link',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customer.id'), nullable=False),
        sa.Column('payment_id', sa.Integer(), sa.ForeignKey('payment.id'), nullable=False),
        sa.Column('amount', sa.Numeric(18, 4), nullable=False),
        sa.Column('currency', sa.String(length=3), sa.ForeignKey('currency.code'), nullable=False),
        sa.Column('view_token', sa.String(length=64), nullable=False, unique=True),
        sa.Column('callback_token', sa.String(length=64), nullable=False),
        sa.Column('whish_external_id', sa.String(length=64), nullable=True, unique=True),
        sa.Column('whish_transaction_number', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_customer_payment_link_tenant_id', 'customer_payment_link', ['tenant_id'])
    op.create_index('ix_customer_payment_link_customer_id', 'customer_payment_link', ['customer_id'])
    op.create_index('ix_customer_payment_link_payment_id', 'customer_payment_link', ['payment_id'])
    op.create_index('ix_customer_payment_link_status', 'customer_payment_link', ['status'])
    op.create_index('ix_customer_payment_link_view_token', 'customer_payment_link', ['view_token'], unique=True)
    op.create_index('ix_customer_payment_link_whish_external_id', 'customer_payment_link', ['whish_external_id'], unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'customer_payment_link' in set(inspector.get_table_names()):
        op.drop_table('customer_payment_link')
