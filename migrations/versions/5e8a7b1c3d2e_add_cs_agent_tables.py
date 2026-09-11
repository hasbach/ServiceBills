"""add cs_agent tables (settings, session, message_log)

Revision ID: 5e8a7b1c3d2e
Revises: 3d372f36071f
Create Date: 2026-09-12 02:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5e8a7b1c3d2e'
down_revision = '3d372f36071f'
branch_labels = None
depends_on = None


def upgrade():
    # 1. cs_agent_settings
    op.create_table(
        'cs_agent_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('elevenlabs_agent_id', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_cs_agent_settings_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cs_agent_settings')),
        sa.UniqueConstraint('tenant_id', name=op.f('uq_cs_agent_settings_tenant_id'))
    )
    with op.batch_alter_table('cs_agent_settings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cs_agent_settings_tenant_id'), ['tenant_id'], unique=True)

    # 2. cs_agent_session
    op.create_table(
        'cs_agent_session',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False, server_default='phone'),
        sa.Column('caller_identifier', sa.String(length=50), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_active_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_cs_agent_session_customer_id_customer')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_cs_agent_session_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cs_agent_session'))
    )
    with op.batch_alter_table('cs_agent_session', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cs_agent_session_caller_identifier'), ['caller_identifier'], unique=False)
        batch_op.create_index(batch_op.f('ix_cs_agent_session_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_cs_agent_session_tenant_id'), ['tenant_id'], unique=False)

    # 3. cs_agent_message_log
    op.create_table(
        'cs_agent_message_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('direction', sa.String(length=5), nullable=False, server_default='in'),
        sa.Column('tool_name', sa.String(length=50), nullable=True),
        sa.Column('tool_input', sa.JSON(), nullable=True),
        sa.Column('tool_output', sa.JSON(), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['cs_agent_session.id'], name=op.f('fk_cs_agent_message_log_session_id_cs_agent_session')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_cs_agent_message_log_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cs_agent_message_log'))
    )
    with op.batch_alter_table('cs_agent_message_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cs_agent_message_log_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_cs_agent_message_log_tenant_id'), ['tenant_id'], unique=False)


def downgrade():
    with op.batch_alter_table('cs_agent_message_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cs_agent_message_log_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_cs_agent_message_log_session_id'))
    op.drop_table('cs_agent_message_log')

    with op.batch_alter_table('cs_agent_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cs_agent_session_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_cs_agent_session_customer_id'))
        batch_op.drop_index(batch_op.f('ix_cs_agent_session_caller_identifier'))
    op.drop_table('cs_agent_session')

    with op.batch_alter_table('cs_agent_settings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cs_agent_settings_tenant_id'))
    op.drop_table('cs_agent_settings')
