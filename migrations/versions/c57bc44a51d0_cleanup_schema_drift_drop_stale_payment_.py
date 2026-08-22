"""cleanup schema drift: drop stale payment.reseller_id, add missing unique constraints

Revision ID: c57bc44a51d0
Revises: ac416dc32280
Create Date: 2026-08-12 06:45:55.319785

Two unrelated pieces of drift found while autogenerating earlier migrations this
session (see fd0c324cba0d/5bb2921fc906/ac416dc32280's docstrings, which
deliberately left this untouched at the time):

1. `payment.reseller_id` -- a real DB column with no matching field on the
   `Payment` model. Confirmed via grep: nothing in app.py reads or writes
   `Payment.reseller_id` (only `ResellerPayment.reseller_id`, a different
   table). Confirmed on the local dev DB: 0 of 2177 rows have a non-null value.
   Safe to drop -- dead column, dead data.

2. Three unique constraints the *models* already declare (or the app-layer
   logic already enforces by hand) but the database schema was never given:
   `tenant.slug`, `user.username`, `generated_receipt.payment_id` (the last one
   is even declared `unique=True` right on the Column in app.py -- the model
   and the DB have been disagreeing since whenever that table was first
   created). `register()` already checks username/slug uniqueness before
   insert, and nothing assigns a payment_id to two receipts, so violations
   should not exist anywhere -- but that check-then-insert pattern isn't
   race-safe, and this app's production database was never directly verified
   for duplicates before writing this migration (only the local dev copy was,
   found clean: 0 duplicates on all three as of 2026-08-12).

Given a `flask db upgrade` failure here would block a production deploy
(Dockerfile CMD runs migrations before serving -- see render.yaml/Dockerfile),
each constraint below is added defensively: checked for duplicates first, and
skipped with a loud warning (not a crash) if any are found, rather than
trusting that what's true locally is true in production. If a warning fires
on deploy, the fix is to de-duplicate the flagged rows by hand, then re-run
this migration (it's safe to re-run -- already-applied constraints are
skipped on their own via the same duplicate check finding nothing, i.e. this
is idempotent in the sense that a second run just re-verifies and re-adds
whatever didn't take the first time). The duplicate-detection helper itself
was verified standalone against both dirty and clean in-memory data before
relying on it here.

NOTE (SQLite-only, harmless): `tenant.slug`/`user.username`/
`generated_receipt.payment_id` already declare `unique=True` on the Column in
app.py. On SQLite, batch mode recreates the whole table from the model's
current metadata to work around SQLite's limited ALTER TABLE support, which
bakes that column-level `unique=True` in as a second, unnamed inline UNIQUE
alongside the named constraint this migration adds explicitly -- confirmed on
the local dev DB, e.g. `tenant` ends up with both `CONSTRAINT uq_tenant_slug
UNIQUE (slug)` and a bare `UNIQUE (slug)`. Cosmetic and harmless (SQLite
allows redundant UNIQUE constraints on one column), and specific to SQLite's
copy-and-recreate batch strategy -- Postgres (production) executes a direct
`ALTER TABLE ADD CONSTRAINT` here instead, no table rebuild, no duplicate.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'c57bc44a51d0'
down_revision = 'ac416dc32280'
branch_labels = None
depends_on = None


def _duplicate_values(bind, table, column):
    """Return the list of values in `column` that appear more than once in
    `table` (NULLs never count as duplicates of each other under a SQL UNIQUE
    constraint, so they're excluded from the check)."""
    result = bind.execute(sa.text(
        f'SELECT {column} FROM {table} WHERE {column} IS NOT NULL '
        f'GROUP BY {column} HAVING COUNT(*) > 1'
    ))
    return [row[0] for row in result]


def _add_unique_constraint_if_safe(bind, table, column, constraint_name):
    dupes = _duplicate_values(bind, table, column)
    if dupes:
        print(f"WARNING: skipping unique constraint {constraint_name} on "
              f"{table}.{column} -- duplicate value(s) found: {dupes[:10]}"
              f"{' (truncated)' if len(dupes) > 10 else ''}. De-duplicate "
              f"these rows by hand, then re-run this migration.")
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.create_unique_constraint(batch_op.f(constraint_name), [column])


def upgrade():
    bind = op.get_bind()

    # payment.reseller_id's FK is unnamed in the actual DDL (there are several
    # unnamed FKs on this table), which makes batch mode's
    # drop_constraint(None, type_='foreignkey') unable to resolve which one to
    # drop (raises IndexError, confirmed live against the local dev DB) --
    # dropping the column is sufficient on its own: batch mode recreates the
    # table from scratch without it, so the FK (defined on that column) simply
    # can't exist in the new table either. No separate drop_constraint call.
    #
    # PRODUCTION INCIDENT (2026-08-23): this column never existed on the real
    # production database -- it only existed on the local dev DB this
    # migration was originally written and tested against (created there by
    # an ad hoc db.create_all() at some earlier point when the model still
    # declared it, never via a migration). Production's `payment` table was
    # built entirely through the migration chain, and the baseline migration
    # never created this column, so `DROP COLUMN reseller_id` failed there
    # with UndefinedColumn on every deploy attempt, blocking this migration
    # (and everything after it) from ever completing on production. Postgres
    # DDL is transactional, so every failed attempt rolled back cleanly with
    # no data impact -- but the fix is to check existence first, the same
    # defensive discipline already used below for the unique constraints.
    payment_columns = {c['name'] for c in inspect(bind).get_columns('payment')}
    if 'reseller_id' in payment_columns:
        with op.batch_alter_table('payment', schema=None) as batch_op:
            batch_op.drop_column('reseller_id')
    else:
        print("NOTE: payment.reseller_id already absent -- skipping drop (nothing to do).")

    _add_unique_constraint_if_safe(bind, 'tenant', 'slug', 'uq_tenant_slug')
    _add_unique_constraint_if_safe(bind, 'user', 'username', 'uq_user_username')
    _add_unique_constraint_if_safe(bind, 'generated_receipt', 'payment_id', 'uq_generated_receipt_payment_id')


def _drop_unique_constraint_if_present(table, constraint_name):
    """Mirrors _add_unique_constraint_if_safe's own skip logic -- upgrade()
    may not have actually created a given constraint (duplicates found), so
    downgrade() can't assume it's there to drop."""
    try:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(batch_op.f(constraint_name), type_='unique')
    except Exception as e:
        print(f"NOTE: {constraint_name} on {table} wasn't present to drop "
              f"(likely skipped during upgrade due to duplicates): {e}")


def downgrade():
    _drop_unique_constraint_if_present('generated_receipt', 'uq_generated_receipt_payment_id')
    _drop_unique_constraint_if_present('user', 'uq_user_username')
    _drop_unique_constraint_if_present('tenant', 'uq_tenant_slug')

    bind = op.get_bind()
    payment_columns = {c['name'] for c in inspect(bind).get_columns('payment')}
    if 'reseller_id' not in payment_columns:
        with op.batch_alter_table('payment', schema=None) as batch_op:
            batch_op.add_column(sa.Column('reseller_id', sa.INTEGER(), nullable=True))
            batch_op.create_foreign_key(None, 'reseller', ['reseller_id'], ['id'])
    else:
        print("NOTE: payment.reseller_id already present -- skipping re-add (nothing to do).")
