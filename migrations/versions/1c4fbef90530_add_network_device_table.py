"""add network_device table

Revision ID: 1c4fbef90530
Revises: 1282420125d2
Create Date: 2026-09-01 01:48:34.509802

"""
from alembic import op
import sqlalchemy as sa
import crypto


# revision identifiers, used by Alembic.
revision = '1c4fbef90530'
down_revision = '1282420125d2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('network_device',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=False),
    sa.Column('api_port', sa.Integer(), nullable=False),
    sa.Column('use_tls', sa.Boolean(), nullable=False),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('password', crypto.EncryptedString(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(), nullable=True),
    sa.Column('last_status', sa.String(length=20), nullable=True),
    sa.Column('interface_labels', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_network_device_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_network_device'))
    )
    with op.batch_alter_table('network_device', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_network_device_tenant_id'), ['tenant_id'], unique=False)


def downgrade():
    with op.batch_alter_table('network_device', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_network_device_tenant_id'))

    op.drop_table('network_device')
