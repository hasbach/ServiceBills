"""mcp connector: OAuth clients, grants, rotated refresh hashes, tool-call audit

Revision ID: 9af8c7a2648e
Revises: 18c24f5699dc
Create Date: 2026-10-21

New tables only -- nothing existing is altered, so no lock_timeout retry is
needed. Each create is guarded with inspect() because production's real schema
has drifted from the migration history before. See
docs/superpowers/specs/2026-10-21-mcp-connector-design.md.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '9af8c7a2648e'
down_revision = '18c24f5699dc'
branch_labels = None
depends_on = None


def upgrade():
    existing = set(inspect(op.get_bind()).get_table_names())
    if 'mcp_client' not in existing:
        op.create_table(
            'mcp_client',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('client_id', sa.String(64), nullable=False),
            sa.Column('client_name', sa.String(200), nullable=False),
            sa.Column('redirect_uris', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_mcp_client_client_id', 'mcp_client', ['client_id'], unique=True)
    if 'mcp_grant' not in existing:
        op.create_table(
            'mcp_grant',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('client_id', sa.String(64), nullable=False),
            sa.Column('scope', sa.String(100), nullable=False),
            sa.Column('resource', sa.String(300), nullable=True),
            sa.Column('code_hash', sa.String(64), nullable=True),
            sa.Column('code_challenge', sa.String(128), nullable=True),
            sa.Column('redirect_uri', sa.String(500), nullable=True),
            sa.Column('code_expires_at', sa.DateTime(), nullable=True),
            sa.Column('code_used_at', sa.DateTime(), nullable=True),
            sa.Column('access_token_hash', sa.String(64), nullable=True),
            sa.Column('access_expires_at', sa.DateTime(), nullable=True),
            sa.Column('refresh_token_hash', sa.String(64), nullable=True),
            sa.Column('refresh_expires_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('last_used_at', sa.DateTime(), nullable=True),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_mcp_grant_tenant_id', 'mcp_grant', ['tenant_id'])
        op.create_index('ix_mcp_grant_user_id', 'mcp_grant', ['user_id'])
        op.create_index('ix_mcp_grant_client_id', 'mcp_grant', ['client_id'])
        op.create_index('ix_mcp_grant_code_hash', 'mcp_grant', ['code_hash'], unique=True)
        op.create_index('ix_mcp_grant_access_token_hash', 'mcp_grant', ['access_token_hash'], unique=True)
        op.create_index('ix_mcp_grant_refresh_token_hash', 'mcp_grant', ['refresh_token_hash'], unique=True)
    if 'mcp_rotated_refresh' not in existing:
        op.create_table(
            'mcp_rotated_refresh',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('grant_id', sa.Integer(), sa.ForeignKey('mcp_grant.id'), nullable=False),
            sa.Column('token_hash', sa.String(64), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_mcp_rotated_refresh_tenant_id', 'mcp_rotated_refresh', ['tenant_id'])
        op.create_index('ix_mcp_rotated_refresh_grant_id', 'mcp_rotated_refresh', ['grant_id'])
        op.create_index('ix_mcp_rotated_refresh_token_hash', 'mcp_rotated_refresh', ['token_hash'], unique=True)
    if 'mcp_tool_call' not in existing:
        op.create_table(
            'mcp_tool_call',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('grant_id', sa.Integer(), sa.ForeignKey('mcp_grant.id'), nullable=False),
            sa.Column('tool', sa.String(64), nullable=False),
            sa.Column('args_summary', sa.String(500), nullable=True),
            sa.Column('ok', sa.Boolean(), nullable=False),
            sa.Column('error', sa.String(300), nullable=True),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_mcp_tool_call_tenant_id', 'mcp_tool_call', ['tenant_id'])
        op.create_index('ix_mcp_tool_call_grant_id', 'mcp_tool_call', ['grant_id'])
        op.create_index('ix_mcp_tool_call_created_at', 'mcp_tool_call', ['created_at'])


def downgrade():
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in ('mcp_rotated_refresh', 'mcp_tool_call', 'mcp_grant', 'mcp_client'):
        if table in existing:
            op.drop_table(table)
