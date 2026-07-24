"""add payroll tables

Revision ID: b7e2c4f19a3d
Revises: 142c9647d56c
Create Date: 2026-07-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7e2c4f19a3d'
down_revision = '142c9647d56c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('employee',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('monthly_salary', sa.Float(), nullable=False),
    sa.Column('hire_date', sa.DateTime(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('balance', sa.Float(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_employee_tenant_id_tenant')),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_employee_user_id_user')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employee'))
    )
    with op.batch_alter_table('employee', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_employee_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('salary_charge',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('period', sa.String(length=7), nullable=False),
    sa.Column('date', sa.DateTime(), nullable=True),
    sa.Column('reason', sa.String(length=200), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_salary_charge_tenant_id_tenant')),
    sa.ForeignKeyConstraint(['employee_id'], ['employee.id'], name=op.f('fk_salary_charge_employee_id_employee')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_salary_charge'))
    )
    with op.batch_alter_table('salary_charge', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_salary_charge_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('salary_payment',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('payment_date', sa.DateTime(), nullable=True),
    sa.Column('method', sa.String(length=50), nullable=True),
    sa.Column('is_advance', sa.Boolean(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_salary_payment_tenant_id_tenant')),
    sa.ForeignKeyConstraint(['employee_id'], ['employee.id'], name=op.f('fk_salary_payment_employee_id_employee')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_salary_payment'))
    )
    with op.batch_alter_table('salary_payment', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_salary_payment_tenant_id'), ['tenant_id'], unique=False)


def downgrade():
    with op.batch_alter_table('salary_payment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_salary_payment_tenant_id'))
    op.drop_table('salary_payment')

    with op.batch_alter_table('salary_charge', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_salary_charge_tenant_id'))
    op.drop_table('salary_charge')

    with op.batch_alter_table('employee', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_employee_tenant_id'))
    op.drop_table('employee')
