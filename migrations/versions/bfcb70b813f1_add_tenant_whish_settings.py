"""add tenant_whish_settings table

Revision ID: bfcb70b813f1
Revises: 1282420125d2
Create Date: 2026-08-27

Per-tenant Whish merchant credentials for the tenant-facing customer-payments
feature (see docs/superpowers/specs/2026-08-27-tenant-whish-customer-payments-design.md).
Additive-only: one new table, fully inert for every existing tenant until they
paste their own credentials in. Follows this repo's defensive-migration
pattern (see c57bc44a51d0's docstring): existence-checked, skip-with-NOTE
rather than crash if already present, given this project's documented history
of migrations disagreeing with the real production schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'bfcb70b813f1'
down_revision = '1282420125d2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'tenant_whish_settings' in set(inspector.get_table_names()):
        print("NOTE: tenant_whish_settings already exists -- skipping create (nothing to do).")
        return
    op.create_table(
        'tenant_whish_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('whish_channel', sa.Text(), nullable=True),
        sa.Column('whish_secret', sa.Text(), nullable=True),
        sa.Column('display_name_override', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_tenant_whish_settings_tenant_id', 'tenant_whish_settings', ['tenant_id'])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'tenant_whish_settings' in set(inspector.get_table_names()):
        op.drop_table('tenant_whish_settings')
