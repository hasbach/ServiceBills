"""add installed_license.revision (cross-worker license cache invalidation)

Revision ID: e1f4a5b6c7d8
Revises: d9e3f4a5b6c7
Create Date: 2026-10-01 00:00:00.000000

Guarded because the production schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e1f4a5b6c7d8'
down_revision = 'd9e3f4a5b6c7'
branch_labels = None
depends_on = None


def _cols():
    insp = sa.inspect(op.get_bind())
    if 'installed_license' not in insp.get_table_names():
        return None
    return {c['name'] for c in insp.get_columns('installed_license')}


def upgrade():
    cols = _cols()
    if cols is None or 'revision' in cols:
        return
    with op.batch_alter_table('installed_license') as batch_op:
        batch_op.add_column(sa.Column('revision', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    cols = _cols()
    if cols is None or 'revision' not in cols:
        return
    with op.batch_alter_table('installed_license') as batch_op:
        batch_op.drop_column('revision')
