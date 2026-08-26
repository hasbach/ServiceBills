"""add missing indexes on payment and customer hot columns

Revision ID: 386fdce26837
Revises: b1c3d5e79a02
Create Date: 2026-08-26 18:48:00.965995

Phase 3 of the post-audit roadmap: `payment.customer_id`, `payment.paid`,
`payment.collected`, and `customer.subscription_expiry_date` are the exact
columns the dashboard and overdue-payment queries filter on, and none of
them had an index -- fine at today's data volume, a real cost the first
time any tenant's payment/customer history grows into the thousands.

Given this project's documented history of migration-vs-production schema
drift (see c57bc44a51d0's docstring -- a constraint that already existed
under the same name, and a column that never existed at all, both only
discovered because production disagreed with what the local dev DB and the
migration assumed), this migration follows that same file's defensive
discipline rather than assuming a clean slate: each index is checked via
`inspect(bind)` for an existing same-named index before creating, and
skipped with a NOTE (not a crash) if already present. `CREATE INDEX`
doesn't no-op on a duplicate name any more than `ADD CONSTRAINT` did.

Deliberately single-column indexes only, matching what was scoped -- a
composite index (e.g. `(tenant_id, paid)`) would likely help more for the
tenant-scoped query patterns actually used throughout this app, but that's
a follow-up to consider once real query plans on production data justify
it, not a call to make unilaterally inside a "add the missing indexes"
migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '386fdce26837'
down_revision = 'b1c3d5e79a02'
branch_labels = None
depends_on = None


_INDEXES = [
    ('ix_payment_customer_id', 'payment', ['customer_id']),
    ('ix_payment_paid', 'payment', ['paid']),
    ('ix_payment_collected', 'payment', ['collected']),
    ('ix_customer_subscription_expiry_date', 'customer', ['subscription_expiry_date']),
]


def _existing_index_names(bind, table):
    return {i['name'] for i in inspect(bind).get_indexes(table)}


def _create_index_if_safe(bind, index_name, table, columns):
    if index_name in _existing_index_names(bind, table):
        print(f"NOTE: {index_name} on {table} already exists -- skipping create (nothing to do).")
        return
    op.create_index(index_name, table, columns)


def _drop_index_if_present(bind, index_name, table):
    if index_name not in _existing_index_names(bind, table):
        print(f"NOTE: {index_name} on {table} wasn't present to drop -- skipping.")
        return
    op.drop_index(index_name, table_name=table)


def upgrade():
    bind = op.get_bind()
    for index_name, table, columns in _INDEXES:
        _create_index_if_safe(bind, index_name, table, columns)


def downgrade():
    bind = op.get_bind()
    for index_name, table, columns in reversed(_INDEXES):
        _drop_index_if_present(bind, index_name, table)
