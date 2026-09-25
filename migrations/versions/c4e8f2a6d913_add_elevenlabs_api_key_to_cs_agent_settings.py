"""add cs_agent_settings.elevenlabs_api_key

Revision ID: c4e8f2a6d913
Revises: a7c3e9d1b2f4
Create Date: 2026-09-25 00:00:00.000000

Commit 03d46c0 added CSAgentSettings.elevenlabs_api_key to the model with no
migration, so every query on cs_agent_settings failed with UndefinedColumn in
production. Text, like gemini_api_key -- EncryptedString stores Fernet
ciphertext. Guarded because production's real schema drifts from history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4e8f2a6d913'
down_revision = 'a7c3e9d1b2f4'
branch_labels = None
depends_on = None


def upgrade():
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('cs_agent_settings')}
    if 'elevenlabs_api_key' not in cols:
        with op.batch_alter_table('cs_agent_settings', schema=None) as batch_op:
            batch_op.add_column(sa.Column('elevenlabs_api_key', sa.Text(), nullable=True))


def downgrade():
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('cs_agent_settings')}
    if 'elevenlabs_api_key' in cols:
        with op.batch_alter_table('cs_agent_settings', schema=None) as batch_op:
            batch_op.drop_column('elevenlabs_api_key')
