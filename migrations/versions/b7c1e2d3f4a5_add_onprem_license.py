"""add onprem_license table

Revision ID: b7c1e2d3f4a5
Revises: a1b2c3d4e5f6
Create Date: 2026-09-30 00:00:00.000000

Guarded because the production schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c1e2d3f4a5'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    if 'onprem_license' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'onprem_license',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('license_key', sa.String(24), nullable=False),
        sa.Column('business_name', sa.String(200), nullable=False),
        sa.Column('owner_phone', sa.String(40), nullable=True),
        sa.Column('machine_id', sa.String(128), nullable=True),
        sa.Column('trial', sa.Boolean(), nullable=False),
        sa.Column('base_term', sa.String(16), nullable=False),
        sa.Column('base_expires_at', sa.String(10), nullable=False),
        sa.Column('modules', sa.JSON(), nullable=True),
        sa.Column('revoked', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('activated_at', sa.DateTime(), nullable=True),
        sa.Column('last_refresh_at', sa.DateTime(), nullable=True),
        sa.Column('last_app_version', sa.String(40), nullable=True),
    )
    op.create_index('ix_onprem_license_license_key', 'onprem_license', ['license_key'], unique=True)
    op.create_index('ix_onprem_license_machine_id', 'onprem_license', ['machine_id'])


def downgrade():
    if 'onprem_license' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table('onprem_license')
