"""paid_via on money-out/in ledgers, business_settings.whish_opening_amount

Revision ID: 3fe48c293a2d
Revises: ddb03a2d1e17
Create Date: 2026-10-05

paid_via (NULL = cash, 'whish') on expense, supplier_payment,
upstream_provider_payment and reseller_payment, so the Daily Cash flow can
report Whish money next to cash. whish_opening_amount is the Whish account
balance at the start of cash_opening_date. All nullable: existing rows stay
cash, existing tenants see no change.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '3fe48c293a2d'
down_revision = 'ddb03a2d1e17'
branch_labels = None
depends_on = None

_PAID_VIA_TABLES = ('expense', 'supplier_payment', 'upstream_provider_payment', 'reseller_payment')


def _columns(table):
    return {c['name'] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade():
    for table in _PAID_VIA_TABLES:
        if 'paid_via' in _columns(table):
            print(f"NOTE: {table}.paid_via already exists -- skipping.")
            continue
        op.add_column(table, sa.Column('paid_via', sa.String(length=10), nullable=True))
    if 'whish_opening_amount' not in _columns('business_settings'):
        op.add_column('business_settings', sa.Column('whish_opening_amount', sa.Numeric(precision=18, scale=4),
                                                     nullable=True))


def downgrade():
    if 'whish_opening_amount' in _columns('business_settings'):
        op.drop_column('business_settings', 'whish_opening_amount')
    for table in _PAID_VIA_TABLES:
        if 'paid_via' in _columns(table):
            op.drop_column(table, 'paid_via')
