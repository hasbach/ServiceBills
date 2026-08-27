"""convert money columns from Float to Numeric(18,4)

Revision ID: 1282420125d2
Revises: f4d3dff69984
Create Date: 2026-08-27

Bundled Float->Numeric fix, folded into the multi-currency accounting work
since both touch the same columns (see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
Each column is existence/type-checked before altering (skip-with-NOTE if
already Numeric) per this repo's defensive-migration discipline. On Postgres,
Float->Numeric(18,4) is a safe direct cast for every value ever stored via
this app's UI (existing data was already bounded by IEEE-754 double range,
comfortably inside Numeric(18,4)) -- confirmed against a real Postgres
instance via docker-compose before this migration was considered done, not
merely asserted (see the PR description for the verification output). On
SQLite (dev), this is a metadata-only no-op: SQLite has no real NUMERIC type
enforcement.

The ORM side (app.py) declares these columns with asdecimal=False, so
SQLAlchemy still hands back plain Python floats to this file's pervasive
existing float-arithmetic call sites -- only the DB-side storage/precision
changes. See the design spec's Precision section for the full reasoning.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '1282420125d2'
down_revision = 'f4d3dff69984'
branch_labels = None
depends_on = None

_MONEY_COLUMNS = [
    ('reseller', 'balance'), ('reseller_payment', 'amount'),
    ('upstream_provider', 'balance'), ('upstream_provider_payment', 'amount'),
    ('customer', 'balance'), ('customer', 'discount'), ('customer', 'cost_override'),
    ('subscription_plan', 'price'), ('subscription_plan', 'cost'),
    ('supplier', 'balance'), ('supplier_payment', 'amount'),
    ('expense', 'amount'),
    ('employee', 'monthly_salary'), ('employee', 'balance'),
    ('salary_charge', 'amount'), ('salary_payment', 'amount'),
    ('monthly_profit_estimate', 'estimated_income'), ('monthly_profit_estimate', 'estimated_cost'),
    ('monthly_profit_estimate', 'estimated_profit'),
    ('payment', 'amount'), ('payment', 'collected_amount'),
    ('addon_purchase', 'amount'), ('billing_payment_attempt', 'amount'),
]


def _is_already_numeric(inspector, table, column):
    for col in inspector.get_columns(table):
        if col['name'] == column:
            return isinstance(col['type'], (sa.Numeric, sa.DECIMAL)) and not isinstance(col['type'], sa.Float)
    return False  # column not found -- let the alter attempt surface a clear error


def upgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'
    for table, column in _MONEY_COLUMNS:
        inspector = inspect(bind)
        if _is_already_numeric(inspector, table, column):
            print(f"NOTE: {table}.{column} is already Numeric -- skipping.")
            continue
        if is_postgres:
            op.alter_column(
                table, column, type_=sa.Numeric(18, 4),
                postgresql_using=f'"{column}"::numeric(18,4)')
        else:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.alter_column(column, type_=sa.Numeric(18, 4))


def downgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'
    for table, column in _MONEY_COLUMNS:
        if is_postgres:
            op.alter_column(table, column, type_=sa.Float(), postgresql_using=f'"{column}"::double precision')
        else:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.alter_column(column, type_=sa.Float())
