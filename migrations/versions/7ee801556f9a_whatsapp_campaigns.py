"""whatsapp campaign tables

Revision ID: 7ee801556f9a
Revises: 769620e9ddfd
Create Date: 2026-11-18 00:00:00.000000

Background WhatsApp campaigns from Messaging: whatsapp_campaign and
whatsapp_campaign_recipient. New tables only -- no existing table is altered,
so there is no lock on a hot table during deploy.

Guarded because production's real schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = '7ee801556f9a'
down_revision = '769620e9ddfd'
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _has_table('whatsapp_campaign'):
        op.create_table(
            'whatsapp_campaign',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('kind', sa.String(length=20), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('params', sa.JSON(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('total', sa.Integer(), nullable=False),
            sa.Column('created_by', sa.String(length=80), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('finished_at', sa.DateTime(), nullable=True),
            sa.Column('heartbeat_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_whatsapp_campaign_tenant_id', 'whatsapp_campaign', ['tenant_id'])
        op.create_index('ix_whatsapp_campaign_status', 'whatsapp_campaign', ['status'])

    if not _has_table('whatsapp_campaign_recipient'):
        op.create_table(
            'whatsapp_campaign_recipient',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('campaign_id', sa.Integer(), nullable=False),
            sa.Column('customer_id', sa.Integer(), nullable=True),
            sa.Column('name', sa.String(length=200), nullable=True),
            sa.Column('phone', sa.String(length=40), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('delivery_status', sa.String(length=20), nullable=True),
            sa.Column('wa_message_id', sa.String(length=128), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('sent_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.ForeignKeyConstraint(['campaign_id'], ['whatsapp_campaign.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_whatsapp_campaign_recipient_tenant_id',
                        'whatsapp_campaign_recipient', ['tenant_id'])
        op.create_index('ix_whatsapp_campaign_recipient_campaign_id',
                        'whatsapp_campaign_recipient', ['campaign_id'])
        op.create_index('ix_whatsapp_campaign_recipient_status',
                        'whatsapp_campaign_recipient', ['status'])
        op.create_index('ix_whatsapp_campaign_recipient_wa_message_id',
                        'whatsapp_campaign_recipient', ['wa_message_id'])


def downgrade():
    if _has_table('whatsapp_campaign_recipient'):
        op.drop_table('whatsapp_campaign_recipient')
    if _has_table('whatsapp_campaign'):
        op.drop_table('whatsapp_campaign')
