"""add employee.accrual_start

Revision ID: 0ae865cf80b3
Revises: cc1af4ef875c
Create Date: 2026-11-16 00:00:00.000000

employee.accrual_start: salary is accrued only for cycles ending after this
moment. Set when an employee is added or reactivated, so adding someone hired
years ago (or reactivating someone after months off) no longer creates salary
owed for all the past months. NULL on existing rows = unchanged behaviour:
they keep accruing from their last salary charge.

Guarded because production's real schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = '0ae865cf80b3'
down_revision = 'cc1af4ef875c'
branch_labels = None
depends_on = None


def _cols(table):
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if 'accrual_start' not in _cols('employee'):
        with op.batch_alter_table('employee', schema=None) as batch_op:
            batch_op.add_column(sa.Column('accrual_start', sa.DateTime(), nullable=True))


def downgrade():
    if 'accrual_start' in _cols('employee'):
        with op.batch_alter_table('employee', schema=None) as batch_op:
            batch_op.drop_column('accrual_start')
