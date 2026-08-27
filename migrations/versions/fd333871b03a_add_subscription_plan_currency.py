"""add subscription_plan.currency

Revision ID: fd333871b03a
Revises: b7e6063473b4
Create Date: 2026-08-27

Multi-currency accounting (see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
Additive-only: one new column defaulting every existing plan to 'USD',
which is a genuine no-op for a single-currency tenant. Defensive existence
check per this repo's established migration discipline.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'fd333871b03a'
down_revision = 'b7e6063473b4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('subscription_plan')}

    with op.batch_alter_table('subscription_plan', schema=None) as batch_op:
        if 'currency' not in columns:
            batch_op.add_column(sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'))
        else:
            print("NOTE: subscription_plan.currency already exists -- skipping add.")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('subscription_plan')}
    with op.batch_alter_table('subscription_plan', schema=None) as batch_op:
        if 'currency' in columns:
            batch_op.drop_column('currency')
