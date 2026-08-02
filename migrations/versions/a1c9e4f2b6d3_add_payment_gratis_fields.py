"""add payment gratis fields

Revision ID: a1c9e4f2b6d3
Revises: d4f8b2a91c6e
Create Date: 2026-08-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c9e4f2b6d3'
down_revision = 'd4f8b2a91c6e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_gratis', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('gratis_note', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.drop_column('gratis_note')
        batch_op.drop_column('is_gratis')
