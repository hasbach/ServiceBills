"""add balance_log

Revision ID: 41eab113c931
Revises: 09a5b9ed8e89
Create Date: 2026-10-05

One row per change of a reseller's or supplier's balance (before -> after,
reason, who). Written by the _log_balance_changes before_flush listener in
app.py. Starts empty: changes made before this migration are not backfilled.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '41eab113c931'
down_revision = '09a5b9ed8e89'
branch_labels = None
depends_on = None


def upgrade():
    if 'balance_log' in inspect(op.get_bind()).get_table_names():
        print("NOTE: balance_log already exists -- skipping.")
        return
    op.create_table('balance_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('reseller_id', sa.Integer(), nullable=True),
    sa.Column('supplier_id', sa.Integer(), nullable=True),
    sa.Column('balance_before', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('balance_after', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('changed_by', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_balance_log_tenant_id_tenant')),
    sa.ForeignKeyConstraint(['reseller_id'], ['reseller.id'], name=op.f('fk_balance_log_reseller_id_reseller')),
    sa.ForeignKeyConstraint(['supplier_id'], ['supplier.id'], name=op.f('fk_balance_log_supplier_id_supplier')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_balance_log'))
    )
    with op.batch_alter_table('balance_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_balance_log_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_balance_log_reseller_id'), ['reseller_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_balance_log_supplier_id'), ['supplier_id'], unique=False)


def downgrade():
    with op.batch_alter_table('balance_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_balance_log_supplier_id'))
        batch_op.drop_index(batch_op.f('ix_balance_log_reseller_id'))
        batch_op.drop_index(batch_op.f('ix_balance_log_tenant_id'))
    op.drop_table('balance_log')
