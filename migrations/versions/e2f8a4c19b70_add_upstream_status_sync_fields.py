"""add upstream status sync fields to customer

Revision ID: e2f8a4c19b70
Revises: c57bc44a51d0
Create Date: 2026-08-22 00:00:00.000000

Adds the read-only upstream-portal mirror fields from
docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md: three
nullable columns on Customer, written only by the new
/customers/<id>/upstream-status-sync endpoint, never by billing logic.
Purely additive -- no existing column or table is touched.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2f8a4c19b70'
down_revision = 'c57bc44a51d0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('upstream_actual_expiry', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('upstream_last_status', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('upstream_last_synced_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.drop_column('upstream_last_synced_at')
        batch_op.drop_column('upstream_last_status')
        batch_op.drop_column('upstream_actual_expiry')
