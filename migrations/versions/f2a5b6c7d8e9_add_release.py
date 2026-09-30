"""add release table (on-prem release metadata)

Revision ID: f2a5b6c7d8e9
Revises: e1f4a5b6c7d8
Create Date: 2026-10-01 01:00:00.000000

Guarded because the production schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2a5b6c7d8e9'
down_revision = 'e1f4a5b6c7d8'
branch_labels = None
depends_on = None


def upgrade():
    if 'release' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'release',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('version', sa.String(20), nullable=False, unique=True),
        sa.Column('release_date', sa.String(10), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('min_upgrade_from', sa.String(20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    if 'release' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table('release')
