"""add expense.is_advance and monthly_profit_estimate.estimated_payroll

Revision ID: e5b7d2c9a1f3
Revises: c4e8f2a6d913
Create Date: 2026-09-28 00:00:00.000000

- expense.is_advance: a payroll payment recorded as an advance (the flag used
  to only change the description text, so reports couldn't tell advances
  from regular salary payments).
- monthly_profit_estimate.estimated_payroll: the month's salaries, now part
  of the estimated profit. Existing (frozen) months get 0 -- they never
  included payroll.

Guarded because production's real schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5b7d2c9a1f3'
down_revision = 'c4e8f2a6d913'
branch_labels = None
depends_on = None


def _cols(table):
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if 'is_advance' not in _cols('expense'):
        with op.batch_alter_table('expense', schema=None) as batch_op:
            batch_op.add_column(sa.Column('is_advance', sa.Boolean(), nullable=False, server_default=sa.false()))
    if 'estimated_payroll' not in _cols('monthly_profit_estimate'):
        with op.batch_alter_table('monthly_profit_estimate', schema=None) as batch_op:
            batch_op.add_column(sa.Column('estimated_payroll', sa.Numeric(18, 4, asdecimal=False),
                                          nullable=False, server_default='0'))


def downgrade():
    if 'estimated_payroll' in _cols('monthly_profit_estimate'):
        with op.batch_alter_table('monthly_profit_estimate', schema=None) as batch_op:
            batch_op.drop_column('estimated_payroll')
    if 'is_advance' in _cols('expense'):
        with op.batch_alter_table('expense', schema=None) as batch_op:
            batch_op.drop_column('is_advance')
