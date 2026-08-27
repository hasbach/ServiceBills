"""add customer_whish_payment_attempt table

Revision ID: 9e7d5ce8b323
Revises: bd054e2e7cf9
Create Date: 2026-08-27

Tracks checkout attempts from the tenant-wide self-service Whish payment
page (2026-08-27 plan amendment) -- see CustomerWhishPaymentAttempt's
docstring in app.py for why this is a separate, simpler model than
CustomerPaymentLink rather than a reuse of it. Additive-only, defensive
existence-check pattern per c57bc44a51d0's docstring.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '9e7d5ce8b323'
down_revision = 'bd054e2e7cf9'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'customer_whish_payment_attempt' in set(inspector.get_table_names()):
        print("NOTE: customer_whish_payment_attempt already exists -- skipping create (nothing to do).")
        return
    op.create_table(
        'customer_whish_payment_attempt',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customer.id'), nullable=False),
        sa.Column('amount', sa.Numeric(18, 4), nullable=False),
        sa.Column('currency', sa.String(length=3), sa.ForeignKey('currency.code'), nullable=False),
        sa.Column('callback_token', sa.String(length=64), nullable=False),
        sa.Column('whish_external_id', sa.String(length=64), nullable=True, unique=True),
        sa.Column('whish_transaction_number', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('applied_to_debt', sa.Numeric(18, 4), nullable=True),
        sa.Column('applied_as_prepayment', sa.Numeric(18, 4), nullable=True),
        sa.Column('prepayment_id', sa.Integer(), sa.ForeignKey('payment.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_customer_whish_payment_attempt_tenant_id', 'customer_whish_payment_attempt', ['tenant_id'])
    op.create_index('ix_customer_whish_payment_attempt_customer_id', 'customer_whish_payment_attempt', ['customer_id'])
    op.create_index('ix_customer_whish_payment_attempt_whish_external_id', 'customer_whish_payment_attempt', ['whish_external_id'], unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'customer_whish_payment_attempt' in set(inspector.get_table_names()):
        op.drop_table('customer_whish_payment_attempt')
