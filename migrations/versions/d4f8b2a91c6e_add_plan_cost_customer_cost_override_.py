"""add subscription plan cost, customer cost override, monthly profit estimate

Revision ID: d4f8b2a91c6e
Revises: b7e2c4f19a3d
Create Date: 2026-07-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4f8b2a91c6e'
down_revision = 'b7e2c4f19a3d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('subscription_plan', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cost', sa.Float(), nullable=False, server_default='0.0'))

    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cost_override', sa.Float(), nullable=True))

    op.create_table('monthly_profit_estimate',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('month', sa.String(length=7), nullable=False),
    sa.Column('estimated_income', sa.Float(), nullable=False),
    sa.Column('estimated_cost', sa.Float(), nullable=False),
    sa.Column('estimated_profit', sa.Float(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_monthly_profit_estimate_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_monthly_profit_estimate')),
    sa.UniqueConstraint('tenant_id', 'month', name='uq_monthly_profit_estimate_tenant_month')
    )
    with op.batch_alter_table('monthly_profit_estimate', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_monthly_profit_estimate_tenant_id'), ['tenant_id'], unique=False)


def downgrade():
    with op.batch_alter_table('monthly_profit_estimate', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_monthly_profit_estimate_tenant_id'))
    op.drop_table('monthly_profit_estimate')

    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.drop_column('cost_override')

    with op.batch_alter_table('subscription_plan', schema=None) as batch_op:
        batch_op.drop_column('cost')
