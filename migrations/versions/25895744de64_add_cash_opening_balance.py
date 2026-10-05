"""add business_settings.cash_opening_date / cash_opening_amount

Revision ID: 25895744de64
Revises: 41eab113c931
Create Date: 2026-10-05

Opening cash on hand for the Daily Cash report's running balance (cash at
the start of each day = opening + all cash in - cash out since the opening
date). Both NULL = not set; the report then shows only the day's own
in / out / net, so existing tenants see no change until they set it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '25895744de64'
down_revision = '41eab113c931'
branch_labels = None
depends_on = None

_COLUMNS = (
    ('cash_opening_date', sa.Date()),
    ('cash_opening_amount', sa.Numeric(precision=18, scale=4)),
)


def upgrade():
    columns = {c['name'] for c in inspect(op.get_bind()).get_columns('business_settings')}
    for name, type_ in _COLUMNS:
        if name in columns:
            print(f"NOTE: business_settings.{name} already exists -- skipping.")
            continue
        op.add_column('business_settings', sa.Column(name, type_, nullable=True))


def downgrade():
    columns = {c['name'] for c in inspect(op.get_bind()).get_columns('business_settings')}
    for name, _ in _COLUMNS:
        if name in columns:
            op.drop_column('business_settings', name)
