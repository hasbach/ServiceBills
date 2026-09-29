"""add customer.notes

Revision ID: f6c8a3d1b2e4
Revises: e5b7d2c9a1f3
Create Date: 2026-09-29 00:00:00.000000

Free-text staff notes per subscription, shown on the Subscriptions card.
Guarded because production's real schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'f6c8a3d1b2e4'
down_revision = 'e5b7d2c9a1f3'
branch_labels = None
depends_on = None


def _cols():
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('customer')}


def upgrade():
    if 'notes' not in _cols():
        with op.batch_alter_table('customer', schema=None) as batch_op:
            batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))


def downgrade():
    if 'notes' in _cols():
        with op.batch_alter_table('customer', schema=None) as batch_op:
            batch_op.drop_column('notes')
