"""payment.settled_from_credit / payment.credit_review, balance_log.customer_id

Revision ID: ddb03a2d1e17
Revises: 25895744de64
Create Date: 2026-10-05

- payment.settled_from_credit: bill paid out of credit the customer already
  had (the fixed apply_customer_balance_to_unpaid_payments).
- payment.credit_review: lost-credit review state for bills the OLD
  settlement auto-paid (None | 'restored' | 'dismissed').
- balance_log.customer_id: the customer statement.
See docs/superpowers/specs/2026-10-05-receive-payment-and-statement-design.md.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'ddb03a2d1e17'
down_revision = '25895744de64'
branch_labels = None
depends_on = None


def _columns(table):
    return {c['name'] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade():
    payment_cols = _columns('payment')
    with op.batch_alter_table('payment', schema=None) as batch_op:
        if 'settled_from_credit' not in payment_cols:
            batch_op.add_column(sa.Column('settled_from_credit', sa.Boolean(), nullable=False,
                                          server_default=sa.false()))
        if 'credit_review' not in payment_cols:
            batch_op.add_column(sa.Column('credit_review', sa.String(length=10), nullable=True))

    if 'customer_id' not in _columns('balance_log'):
        with op.batch_alter_table('balance_log', schema=None) as batch_op:
            batch_op.add_column(sa.Column('customer_id', sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f('ix_balance_log_customer_id'), ['customer_id'], unique=False)
            batch_op.create_foreign_key(batch_op.f('fk_balance_log_customer_id_customer'),
                                        'customer', ['customer_id'], ['id'])


def downgrade():
    if 'customer_id' in _columns('balance_log'):
        with op.batch_alter_table('balance_log', schema=None) as batch_op:
            batch_op.drop_constraint(batch_op.f('fk_balance_log_customer_id_customer'), type_='foreignkey')
            batch_op.drop_index(batch_op.f('ix_balance_log_customer_id'))
            batch_op.drop_column('customer_id')
    payment_cols = _columns('payment')
    with op.batch_alter_table('payment', schema=None) as batch_op:
        if 'credit_review' in payment_cols:
            batch_op.drop_column('credit_review')
        if 'settled_from_credit' in payment_cols:
            batch_op.drop_column('settled_from_credit')
