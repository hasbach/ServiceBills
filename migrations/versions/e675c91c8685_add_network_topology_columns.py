"""add network topology columns

Revision ID: e675c91c8685
Revises: 1c4fbef90530
Create Date: 2026-09-04 03:31:06.285445

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e675c91c8685'
down_revision = '1c4fbef90530'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('network_device', schema=None) as batch_op:
        batch_op.add_column(sa.Column('device_type', sa.String(length=20),
                                      nullable=False, server_default='mikrotik_ccr'))
        batch_op.add_column(sa.Column('parent_device_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f('fk_network_device_parent_device_id_network_device'),
            'network_device', ['parent_device_id'], ['id'])

    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('onu_mac_address', sa.String(length=20), nullable=True))
        batch_op.create_index(batch_op.f('ix_customer_onu_mac_address'),
                              ['onu_mac_address'], unique=False)


def downgrade():
    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_customer_onu_mac_address'))
        batch_op.drop_column('onu_mac_address')

    with op.batch_alter_table('network_device', schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f('fk_network_device_parent_device_id_network_device'),
            type_='foreignkey')
        batch_op.drop_column('parent_device_id')
        batch_op.drop_column('device_type')
