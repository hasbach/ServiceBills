"""add network agent

Revision ID: 5f65a6fd6e8d
Revises: 6129b0fb0885
Create Date: 2026-09-04 13:30:00.000000

Layer 2 schema foundation: NetworkAgent (one relay per tenant, polling for
work outbound so nothing connects inbound to the tenant's LAN),
NetworkAgentJob (one relayed device call), and BusinessSettings.
network_access_mode (the per-tenant switch between calling devices directly
and relaying through the agent). See
docs/superpowers/specs/2026-09-04-network-agent-layer-2-design.md.

Hand-written rather than `flask db revision --autogenerate`: this repo's
local SQLite dev database can't reach the real head (6129b0fb0885) at all --
upgrading through bd054e2e7cf9 (op.create_unique_constraint outside batch
mode) raises "No support for ALTER of constraints in SQLite dialect" (see
tests/test_topology_migration.py's TOPOLOGY_REVISION comment for the same
limitation) -- so autogenerate has nothing valid to diff against locally.
The operations below are exactly what autogenerate would emit from the
NetworkAgent/NetworkAgentJob models and the BusinessSettings.
network_access_mode column in app.py: two create_table calls with their
indexes, and one add_column.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5f65a6fd6e8d'
down_revision = '6129b0fb0885'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('network_agent',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('token_hash', sa.String(length=255), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=True),
    sa.Column('agent_version', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_network_agent_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_network_agent'))
    )
    with op.batch_alter_table('network_agent', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_network_agent_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('network_agent_job',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.Integer(), nullable=False),
    sa.Column('operation', sa.String(length=30), nullable=False),
    sa.Column('params', sa.JSON(), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('requested_by_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('claimed_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['device_id'], ['network_device.id'], name=op.f('fk_network_agent_job_device_id_network_device')),
    sa.ForeignKeyConstraint(['requested_by_user_id'], ['user.id'], name=op.f('fk_network_agent_job_requested_by_user_id_user')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_network_agent_job_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_network_agent_job'))
    )
    with op.batch_alter_table('network_agent_job', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_network_agent_job_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_network_agent_job_created_at'), ['created_at'], unique=False)
        batch_op.create_index('ix_network_agent_job_poll', ['tenant_id', 'status', 'created_at'], unique=False)

    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('network_access_mode', sa.String(length=10),
                                      nullable=False, server_default='direct'))


def downgrade():
    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        batch_op.drop_column('network_access_mode')

    with op.batch_alter_table('network_agent_job', schema=None) as batch_op:
        batch_op.drop_index('ix_network_agent_job_poll')
        batch_op.drop_index(batch_op.f('ix_network_agent_job_created_at'))
        batch_op.drop_index(batch_op.f('ix_network_agent_job_tenant_id'))
    op.drop_table('network_agent_job')

    with op.batch_alter_table('network_agent', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_network_agent_tenant_id'))
    op.drop_table('network_agent')
