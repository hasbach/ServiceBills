"""add audit_log

Revision ID: 7c41f2e9b6d5
Revises: 0ae865cf80b3
Create Date: 2026-11-17 00:00:00.000000

audit_log: one row per create / edit / delete of a financial record (who,
when, what changed). A new table only -- no existing table is altered, so
there is no lock on a hot table during deploy.

Guarded because production's real schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = '7c41f2e9b6d5'
down_revision = '0ae865cf80b3'
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table('audit_log'):
        return
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('entity', sa.String(length=40), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=10), nullable=False),
        sa.Column('party_type', sa.String(length=20), nullable=True),
        sa.Column('party_id', sa.Integer(), nullable=True),
        sa.Column('amount', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
        sa.Column('changes', sa.Text(), nullable=False),
        sa.Column('changed_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_log_tenant_created', 'audit_log', ['tenant_id', 'created_at'])
    op.create_index('ix_audit_log_tenant_entity', 'audit_log', ['tenant_id', 'entity', 'entity_id'])
    op.create_index('ix_audit_log_tenant_party', 'audit_log', ['tenant_id', 'party_type', 'party_id'])


def downgrade():
    if _has_table('audit_log'):
        op.drop_table('audit_log')
