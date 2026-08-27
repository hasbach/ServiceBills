"""add collected_via, whish_transaction_number, public_pay_slug

Revision ID: bd054e2e7cf9
Revises: d3d4ca65d8b1
Create Date: 2026-08-27 16:15:07.142537

Three small, independent, additive nullable columns for the tenant-wide
self-service Whish payment page (2026-08-27 plan amendment). Defensive
per this repo's documented history of migrations that pass on SQLite dev
and fail/drift on production Postgres -- see
migrations/versions/c57bc44a51d0_cleanup_schema_drift_drop_stale_payment_.py.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bd054e2e7cf9'
down_revision = 'd3d4ca65d8b1'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    payment_cols = {c['name'] for c in insp.get_columns('payment')}
    if 'collected_via' not in payment_cols:
        op.add_column('payment', sa.Column('collected_via', sa.String(20), nullable=True))
    else:
        print("NOTE: payment.collected_via already exists -- skipping")
    if 'whish_transaction_number' not in payment_cols:
        op.add_column('payment', sa.Column('whish_transaction_number', sa.String(64), nullable=True))
    else:
        print("NOTE: payment.whish_transaction_number already exists -- skipping")

    tenant_cols = {c['name'] for c in insp.get_columns('tenant')}
    if 'public_pay_slug' not in tenant_cols:
        op.add_column('tenant', sa.Column('public_pay_slug', sa.String(32), nullable=True))
        op.create_unique_constraint('uq_tenant_public_pay_slug', 'tenant', ['public_pay_slug'])
    else:
        print("NOTE: tenant.public_pay_slug already exists -- skipping")


def downgrade():
    pass  # additive-only, matches this repo's existing convention of no-op downgrades on defensive migrations
