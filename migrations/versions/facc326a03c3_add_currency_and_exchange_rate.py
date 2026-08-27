"""add currency and exchange_rate tables

Revision ID: facc326a03c3
Revises: 95dfe810650a
Create Date: 2026-08-27

Multi-currency accounting for tenant customer billing (see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
Additive-only: two new tables, one seeded with USD/LBP. Follows this repo's
defensive-migration discipline (existence checks, skip-with-NOTE rather than
crash) per c57bc44a51d0's documented rationale.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'facc326a03c3'
down_revision = '95dfe810650a'
branch_labels = None
depends_on = None

_SEED_CURRENCIES = [
    ('USD', 'US Dollar', 2),
    ('LBP', 'Lebanese Pound', 0),
]


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if 'currency' not in existing_tables:
        op.create_table(
            'currency',
            sa.Column('code', sa.String(length=3), primary_key=True),
            sa.Column('name', sa.String(length=50), nullable=False),
            sa.Column('decimal_places', sa.Integer(), nullable=False, server_default='2'),
            sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    else:
        print("NOTE: currency table already exists -- skipping create (nothing to do).")

    currency_table = sa.table(
        'currency', sa.column('code', sa.String), sa.column('name', sa.String),
        sa.column('decimal_places', sa.Integer), sa.column('active', sa.Boolean),
    )
    existing_codes = set()
    if 'currency' in set(inspect(bind).get_table_names()):
        existing_codes = {row[0] for row in bind.execute(sa.text("SELECT code FROM currency"))}
    for code, name, decimals in _SEED_CURRENCIES:
        if code in existing_codes:
            print(f"NOTE: currency '{code}' already seeded -- skipping insert.")
            continue
        op.bulk_insert(currency_table, [{'code': code, 'name': name, 'decimal_places': decimals, 'active': True}])

    if 'exchange_rate' not in existing_tables:
        op.create_table(
            'exchange_rate',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('from_currency', sa.String(length=3), sa.ForeignKey('currency.code'), nullable=False),
            sa.Column('to_currency', sa.String(length=3), sa.ForeignKey('currency.code'), nullable=False),
            sa.Column('rate', sa.Numeric(18, 8), nullable=False),
            sa.Column('effective_at', sa.DateTime(), nullable=False),
            sa.Column('source', sa.String(length=20), nullable=False, server_default='manual'),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_exchange_rate_tenant_id', 'exchange_rate', ['tenant_id'])
        op.create_index(
            'ix_exchange_rate_tenant_pair_effective', 'exchange_rate',
            ['tenant_id', 'from_currency', 'to_currency', 'effective_at'])
    else:
        print("NOTE: exchange_rate table already exists -- skipping create (nothing to do).")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if 'exchange_rate' in existing_tables:
        op.drop_table('exchange_rate')
    if 'currency' in existing_tables:
        op.drop_table('currency')
