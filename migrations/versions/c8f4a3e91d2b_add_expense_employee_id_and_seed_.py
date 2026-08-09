"""add expense.employee_id and seed default expense categories

Revision ID: c8f4a3e91d2b
Revises: f3a8b1d02e47
Create Date: 2026-08-10 00:00:00.000000

Adds the FK that lets an Expense row represent a payroll payment to a specific
employee (see record_employee_payment / add_expense), and backfills the
Rent/Payroll/Electricity default categories onto every existing tenant that's
missing them -- new tenants get these at signup (register()); this is the
catch-up for tenants that already existed before that changed.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8f4a3e91d2b'
down_revision = 'f3a8b1d02e47'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('expense', schema=None) as batch_op:
        batch_op.add_column(sa.Column('employee_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_expense_employee_id_employee'), 'employee', ['employee_id'], ['id'])

    # Portable across Postgres/SQLite: one INSERT..SELECT per default category,
    # skipping any tenant that already has that category (e.g. created it
    # itself, or a name collision with a custom one it made before this ran).
    for name in ('Rent', 'Payroll', 'Electricity'):
        op.execute(sa.text("""
            INSERT INTO expense_category (tenant_id, name)
            SELECT t.id, :name FROM tenant t
            WHERE NOT EXISTS (
                SELECT 1 FROM expense_category ec
                WHERE ec.tenant_id = t.id AND ec.name = :name
            )
        """).bindparams(name=name))


def downgrade():
    with op.batch_alter_table('expense', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_expense_employee_id_employee'), type_='foreignkey')
        batch_op.drop_column('employee_id')
    # Backfilled categories are left in place -- by the time this would ever
    # run, real Expense rows may already reference them.
