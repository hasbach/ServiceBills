"""add payment currency and fx_rate_to_reporting

Revision ID: f4d3dff69984
Revises: fd333871b03a
Create Date: 2026-08-27

Historical FX-rate locking on Payment (see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
Additive-only: currency defaults every existing payment to 'USD',
fx_rate_to_reporting defaults to 1 -- both a genuine no-op for a
single-currency tenant. Defensive existence checks per this repo's
established migration discipline.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'f4d3dff69984'
down_revision = 'fd333871b03a'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('payment')}

    with op.batch_alter_table('payment', schema=None) as batch_op:
        if 'currency' not in columns:
            batch_op.add_column(sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'))
        else:
            print("NOTE: payment.currency already exists -- skipping add.")
        if 'fx_rate_to_reporting' not in columns:
            batch_op.add_column(sa.Column(
                'fx_rate_to_reporting', sa.Numeric(18, 8), nullable=False, server_default='1'))
        else:
            print("NOTE: payment.fx_rate_to_reporting already exists -- skipping add.")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('payment')}
    with op.batch_alter_table('payment', schema=None) as batch_op:
        if 'fx_rate_to_reporting' in columns:
            batch_op.drop_column('fx_rate_to_reporting')
        if 'currency' in columns:
            batch_op.drop_column('currency')
