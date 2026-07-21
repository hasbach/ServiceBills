"""add customer id to reseller payment

Revision ID: 142c9647d56c
Revises: 425728acb5e8
Create Date: 2026-07-21 05:00:49.697489

Reseller-linked customers never get a Payment row (their charges go to the
reseller's balance instead), so the "already billed this cycle?" cursor used
by has_pending_payment() was always a no-op for them: it fell back to
subscription_start_date forever and re-billed every historical cycle on every
scheduler run. This column lets ResellerPayment charges be scoped to a
specific customer, the same way Payment already is, so a matching
has_pending_reseller_charge() cursor can work correctly. Nullable: existing
rows (and reseller-level entries like add_credit/apply_discount/collect_payment
that aren't tied to one customer) are left as NULL.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '142c9647d56c'
down_revision = '425728acb5e8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('reseller_payment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('customer_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_reseller_payment_customer_id'), ['customer_id'])
        batch_op.create_foreign_key(batch_op.f('fk_reseller_payment_customer_id_customer'), 'customer', ['customer_id'], ['id'])


def downgrade():
    with op.batch_alter_table('reseller_payment', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_reseller_payment_customer_id_customer'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_reseller_payment_customer_id'))
        batch_op.drop_column('customer_id')
