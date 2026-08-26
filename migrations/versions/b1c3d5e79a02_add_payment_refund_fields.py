"""add payment refund fields

Revision ID: b1c3d5e79a02
Revises: e2f8a4c19b70
Create Date: 2026-08-26 00:00:00.000000

Adds an audit trail for the dedicated refund/adjustment endpoint: whether a
payment row represents a refund, who issued it, when, and the mandatory
reason. Distinguishes refunds from ordinary payments in the data model
(and in reports, which exclude is_refund rows from revenue totals).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1c3d5e79a02'
down_revision = 'e2f8a4c19b70'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_refund', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('refund_reason', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('refunded_by_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('refunded_at', sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_payment_refunded_by_id_user'), 'user', ['refunded_by_id'], ['id'])


def downgrade():
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_payment_refunded_by_id_user'), type_='foreignkey')
        batch_op.drop_column('refunded_at')
        batch_op.drop_column('refunded_by_id')
        batch_op.drop_column('refund_reason')
        batch_op.drop_column('is_refund')
