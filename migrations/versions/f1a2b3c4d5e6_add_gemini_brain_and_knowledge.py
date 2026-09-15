"""add gemini brain columns and cs_agent_knowledge_entry table

Revision ID: f1a2b3c4d5e6
Revises: 7d71301113fd
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f1a2b3c4d5e6'
down_revision = '7d71301113fd'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cs_agent_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gemini_api_key', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('gemini_model', sa.String(length=50), nullable=True))

    op.create_table(
        'cs_agent_knowledge_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('question_text', sa.Text(), nullable=False),
        sa.Column('answer_text', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False, server_default='manual'),
        sa.Column('source_log_id', sa.Integer(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_cs_agent_knowledge_entry_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['source_log_id'], ['cs_agent_message_log.id'], name=op.f('fk_cs_agent_knowledge_entry_source_log_id_cs_agent_message_log')),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], name=op.f('fk_cs_agent_knowledge_entry_created_by_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cs_agent_knowledge_entry'))
    )
    with op.batch_alter_table('cs_agent_knowledge_entry', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cs_agent_knowledge_entry_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index('ix_cs_agent_knowledge_entry_tenant_active', ['tenant_id', 'is_active'], unique=False)


def downgrade():
    with op.batch_alter_table('cs_agent_knowledge_entry', schema=None) as batch_op:
        batch_op.drop_index('ix_cs_agent_knowledge_entry_tenant_active')
        batch_op.drop_index(batch_op.f('ix_cs_agent_knowledge_entry_tenant_id'))
    op.drop_table('cs_agent_knowledge_entry')

    with op.batch_alter_table('cs_agent_settings', schema=None) as batch_op:
        batch_op.drop_column('gemini_model')
        batch_op.drop_column('gemini_api_key')
