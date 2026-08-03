"""add payment revert fields

Revision ID: f3a8b1d02e47
Revises: a1c9e4f2b6d3
Create Date: 2026-08-03 00:00:00.000000

Adds an audit trail for the "revert payment" feature: who reverted it, when,
and why. Nullable -- only ever set once a payment has actually been reverted.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a8b1d02e47'
down_revision = 'a1c9e4f2b6d3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reverted_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('reverted_by_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('revert_reason', sa.Text(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_payment_reverted_by_id_user'), 'user', ['reverted_by_id'], ['id'])


def downgrade():
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_payment_reverted_by_id_user'), type_='foreignkey')
        batch_op.drop_column('revert_reason')
        batch_op.drop_column('reverted_by_id')
        batch_op.drop_column('reverted_at')
