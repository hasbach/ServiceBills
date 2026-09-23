"""add whatsapp inbox tables and push_subscription.topics

Revision ID: a7c3e9d1b2f4
Revises: f1a2b3c4d5e6
Create Date: 2026-09-23 00:00:00.000000

New tables only, plus one guarded ADD COLUMN on push_subscription -- production's
real schema is known to drift from migration history, so check before adding.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c3e9d1b2f4'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'whatsapp_conversation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('wa_phone', sa.String(length=20), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('contact_name', sa.String(length=120), nullable=True),
        sa.Column('needs_attention', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('attention_reason', sa.String(length=30), nullable=True),
        sa.Column('attention_since', sa.DateTime(), nullable=True),
        sa.Column('ai_paused', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('ai_paused_at', sa.DateTime(), nullable=True),
        sa.Column('last_admin_reply_at', sa.DateTime(), nullable=True),
        sa.Column('last_inbound_at', sa.DateTime(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(), nullable=False),
        sa.Column('last_message_preview', sa.String(length=200), nullable=True),
        sa.Column('unread_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_push_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_whatsapp_conversation_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_whatsapp_conversation_customer_id_customer'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_whatsapp_conversation')),
        sa.UniqueConstraint('tenant_id', 'wa_phone', name='uq_whatsapp_conversation_tenant_phone'),
    )
    with op.batch_alter_table('whatsapp_conversation', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_whatsapp_conversation_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_whatsapp_conversation_needs_attention'), ['needs_attention'], unique=False)
        batch_op.create_index(batch_op.f('ix_whatsapp_conversation_last_message_at'), ['last_message_at'], unique=False)

    op.create_table(
        'whatsapp_message',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('direction', sa.String(length=3), nullable=False),
        sa.Column('sender', sa.String(length=10), nullable=False),
        sa.Column('sent_by_user_id', sa.Integer(), nullable=True),
        sa.Column('msg_type', sa.String(length=20), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('wa_message_id', sa.String(length=128), nullable=True),
        sa.Column('reply_to_wa_message_id', sa.String(length=128), nullable=True),
        sa.Column('reaction_emoji', sa.String(length=16), nullable=True),
        sa.Column('reaction_target_wa_id', sa.String(length=128), nullable=True),
        sa.Column('wa_media_id', sa.String(length=128), nullable=True),
        sa.Column('media_key', sa.String(length=300), nullable=True),
        sa.Column('media_playback_key', sa.String(length=300), nullable=True),
        sa.Column('media_mime', sa.String(length=80), nullable=True),
        sa.Column('media_status', sa.String(length=10), nullable=False, server_default='none'),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='received'),
        sa.Column('error_code', sa.String(length=20), nullable=True),
        sa.Column('error_message', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_whatsapp_message_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['conversation_id'], ['whatsapp_conversation.id'], name=op.f('fk_whatsapp_message_conversation_id_whatsapp_conversation')),
        sa.ForeignKeyConstraint(['sent_by_user_id'], ['user.id'], name=op.f('fk_whatsapp_message_sent_by_user_id_user'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_whatsapp_message')),
        sa.UniqueConstraint('tenant_id', 'wa_message_id', name='uq_whatsapp_message_tenant_wamid'),
    )
    with op.batch_alter_table('whatsapp_message', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_whatsapp_message_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_whatsapp_message_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_whatsapp_message_created_at'), ['created_at'], unique=False)

    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('push_subscription')}
    if 'topics' not in cols:
        with op.batch_alter_table('push_subscription', schema=None) as batch_op:
            batch_op.add_column(sa.Column('topics', sa.Text(), nullable=True))


def downgrade():
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('push_subscription')}
    if 'topics' in cols:
        with op.batch_alter_table('push_subscription', schema=None) as batch_op:
            batch_op.drop_column('topics')
    with op.batch_alter_table('whatsapp_message', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_whatsapp_message_created_at'))
        batch_op.drop_index(batch_op.f('ix_whatsapp_message_conversation_id'))
        batch_op.drop_index(batch_op.f('ix_whatsapp_message_tenant_id'))
    op.drop_table('whatsapp_message')
    with op.batch_alter_table('whatsapp_conversation', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_whatsapp_conversation_last_message_at'))
        batch_op.drop_index(batch_op.f('ix_whatsapp_conversation_needs_attention'))
        batch_op.drop_index(batch_op.f('ix_whatsapp_conversation_tenant_id'))
    op.drop_table('whatsapp_conversation')
