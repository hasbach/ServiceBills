"""add installed_license table (on-prem)

Revision ID: c8d2e3f4a5b6
Revises: b7c1e2d3f4a5
Create Date: 2026-09-30 00:00:00.000000

Guarded because the production schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c8d2e3f4a5b6'
down_revision = 'b7c1e2d3f4a5'
branch_labels = None
depends_on = None


def upgrade():
    if 'installed_license' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'installed_license',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('license_text', sa.Text(), nullable=True),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('last_refresh_at', sa.DateTime(), nullable=True),
        sa.Column('last_refresh_error', sa.String(500), nullable=True),
    )


def downgrade():
    if 'installed_license' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table('installed_license')
