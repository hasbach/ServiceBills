"""add tenant.module_overrides

Revision ID: a1b2c3d4e5f6
Revises: f6c8a3d1b2e4
Create Date: 2026-09-30 00:00:00.000000

Guarded because production's real schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'f6c8a3d1b2e4'
branch_labels = None
depends_on = None


def _cols(table):
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if 'module_overrides' not in _cols('tenant'):
        with op.batch_alter_table('tenant', schema=None) as batch_op:
            batch_op.add_column(sa.Column('module_overrides', sa.JSON(), nullable=True))


def downgrade():
    if 'module_overrides' in _cols('tenant'):
        with op.batch_alter_table('tenant', schema=None) as batch_op:
            batch_op.drop_column('module_overrides')
