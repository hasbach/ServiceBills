"""add business_settings multi-currency fields

Revision ID: b7e6063473b4
Revises: facc326a03c3
Create Date: 2026-08-27

Multi-currency accounting opt-in + reporting currency (see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
Additive-only: two new nullable-with-default columns, a genuine no-op for
every existing tenant until they explicitly opt in. Defensive existence
checks per this repo's established migration discipline.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'b7e6063473b4'
down_revision = 'facc326a03c3'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('business_settings')}

    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        if 'multi_currency_enabled' not in columns:
            batch_op.add_column(sa.Column(
                'multi_currency_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        else:
            print("NOTE: business_settings.multi_currency_enabled already exists -- skipping add.")
        if 'reporting_currency' not in columns:
            batch_op.add_column(sa.Column(
                'reporting_currency', sa.String(length=3), nullable=False, server_default='USD'))
        else:
            print("NOTE: business_settings.reporting_currency already exists -- skipping add.")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('business_settings')}
    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        if 'reporting_currency' in columns:
            batch_op.drop_column('reporting_currency')
        if 'multi_currency_enabled' in columns:
            batch_op.drop_column('multi_currency_enabled')
