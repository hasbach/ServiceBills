"""add business_settings.public_url (on-prem public URL)

Revision ID: d9e3f4a5b6c7
Revises: c8d2e3f4a5b6
Create Date: 2026-09-30 00:00:00.000000

Guarded because the production schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'd9e3f4a5b6c7'
down_revision = 'c8d2e3f4a5b6'
branch_labels = None
depends_on = None


def _cols():
    insp = sa.inspect(op.get_bind())
    if 'business_settings' not in insp.get_table_names():
        return None
    return {c['name'] for c in insp.get_columns('business_settings')}


def upgrade():
    cols = _cols()
    if cols is None or 'public_url' in cols:
        return
    with op.batch_alter_table('business_settings') as batch_op:
        batch_op.add_column(sa.Column('public_url', sa.String(length=300), nullable=True))


def downgrade():
    cols = _cols()
    if cols is None or 'public_url' not in cols:
        return
    with op.batch_alter_table('business_settings') as batch_op:
        batch_op.drop_column('public_url')
