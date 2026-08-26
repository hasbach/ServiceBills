"""add whish billing fields

Revision ID: 95dfe810650a
Revises: aa91943943d4
Create Date: 2026-08-26

Self-serve Pro plan via Whish (see
docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md).
Additive-only: two new nullable Tenant columns (a genuine no-op for every
existing tenant until they actually check out) and one new table. Follows
this repo's established defensive-migration discipline (see
c57bc44a51d0's docstring for why): checks for existing columns/tables
first, skips with a NOTE rather than crashing if already present, given
the documented history of this project's migrations disagreeing with the
real production schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '95dfe810650a'
down_revision = 'aa91943943d4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    tenant_columns = {c['name'] for c in inspector.get_columns('tenant')}
    with op.batch_alter_table('tenant', schema=None) as batch_op:
        if 'plan_expires_at' not in tenant_columns:
            batch_op.add_column(sa.Column('plan_expires_at', sa.DateTime(), nullable=True))
        else:
            print("NOTE: tenant.plan_expires_at already exists -- skipping add (nothing to do).")
        if 'plan_expiry_reminder_sent_at' not in tenant_columns:
            batch_op.add_column(sa.Column('plan_expiry_reminder_sent_at', sa.DateTime(), nullable=True))
        else:
            print("NOTE: tenant.plan_expiry_reminder_sent_at already exists -- skipping add (nothing to do).")

    existing_tables = set(inspector.get_table_names())
    if 'billing_payment_attempt' in existing_tables:
        print("NOTE: billing_payment_attempt table already exists -- skipping create (nothing to do).")
        return
    op.create_table(
        'billing_payment_attempt',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
        sa.Column('billing_cycle', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
        sa.Column('whish_external_id', sa.String(length=64), nullable=False, unique=True),
        sa.Column('callback_token', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_billing_payment_attempt_tenant_id', 'billing_payment_attempt', ['tenant_id'])
    op.create_index('ix_billing_payment_attempt_whish_external_id', 'billing_payment_attempt', ['whish_external_id'], unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'billing_payment_attempt' in set(inspector.get_table_names()):
        op.drop_table('billing_payment_attempt')
    tenant_columns = {c['name'] for c in inspector.get_columns('tenant')}
    with op.batch_alter_table('tenant', schema=None) as batch_op:
        if 'plan_expiry_reminder_sent_at' in tenant_columns:
            batch_op.drop_column('plan_expiry_reminder_sent_at')
        if 'plan_expires_at' in tenant_columns:
            batch_op.drop_column('plan_expires_at')
