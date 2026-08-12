"""add upstream provider, mikrotik server, network mode

Revision ID: fd0c324cba0d
Revises: c8f4a3e91d2b
Create Date: 2026-08-12 02:32:34.559974

Adds the two network-integration concepts from
docs/superpowers/specs/2026-08-12-network-enforcement-design.md: UpstreamProvider
(+ its payment ledger) for bridged subresellers, MikrotikServer for tenants
running their own local PPPoE, and BusinessSettings.network_mode to pick between
them (or neither). Customer gets one nullable link column pair per concept.

NOTE: autogenerate also reported several pre-existing drifts unrelated to this
change -- a cosmetic whats_app_settings.app_secret VARCHAR->EncryptedString type
change (same convention already established in d5b73c902f25/9dd046c3dc10: cosmetic,
intentionally omitted), a stale payment.reseller_id column no longer on the
Payment model, and missing unique constraints on tenant.slug/user.username/
generated_receipt.payment_id plus some SQLite id NOT NULL/autoincrement
representation diffs. None of that is touched here -- flagged separately, not
folded into this migration.
"""
from alembic import op
import sqlalchemy as sa
import crypto


# revision identifiers, used by Alembic.
revision = 'fd0c324cba0d'
down_revision = 'c8f4a3e91d2b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mikrotik_server',
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
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_mikrotik_server_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mikrotik_server'))
    )
    with op.batch_alter_table('mikrotik_server', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mikrotik_server_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('upstream_provider',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('product', sa.String(length=20), nullable=False),
    sa.Column('portal_url', sa.String(length=300), nullable=True),
    sa.Column('portal_username', sa.String(length=100), nullable=True),
    sa.Column('portal_password', crypto.EncryptedString(), nullable=True),
    sa.Column('balance', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_upstream_provider_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_upstream_provider'))
    )
    with op.batch_alter_table('upstream_provider', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_upstream_provider_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('upstream_provider_payment',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('upstream_provider_id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('type', sa.String(length=50), nullable=False),
    sa.Column('date', sa.DateTime(), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_upstream_provider_payment_customer_id_customer')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_upstream_provider_payment_tenant_id_tenant')),
    sa.ForeignKeyConstraint(['upstream_provider_id'], ['upstream_provider.id'], name=op.f('fk_upstream_provider_payment_upstream_provider_id_upstream_provider')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_upstream_provider_payment'))
    )
    with op.batch_alter_table('upstream_provider_payment', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_upstream_provider_payment_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_upstream_provider_payment_tenant_id'), ['tenant_id'], unique=False)

    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('network_mode', sa.String(length=20), nullable=False, server_default='none'))

    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('upstream_provider_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('upstream_username', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('mikrotik_server_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('pppoe_username', sa.String(length=100), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_customer_mikrotik_server_id_mikrotik_server'), 'mikrotik_server', ['mikrotik_server_id'], ['id'])
        batch_op.create_foreign_key(batch_op.f('fk_customer_upstream_provider_id_upstream_provider'), 'upstream_provider', ['upstream_provider_id'], ['id'])


def downgrade():
    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_customer_upstream_provider_id_upstream_provider'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('fk_customer_mikrotik_server_id_mikrotik_server'), type_='foreignkey')
        batch_op.drop_column('pppoe_username')
        batch_op.drop_column('mikrotik_server_id')
        batch_op.drop_column('upstream_username')
        batch_op.drop_column('upstream_provider_id')

    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        batch_op.drop_column('network_mode')

    with op.batch_alter_table('upstream_provider_payment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_upstream_provider_payment_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_upstream_provider_payment_customer_id'))

    op.drop_table('upstream_provider_payment')
    with op.batch_alter_table('upstream_provider', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_upstream_provider_tenant_id'))

    op.drop_table('upstream_provider')
    with op.batch_alter_table('mikrotik_server', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mikrotik_server_tenant_id'))

    op.drop_table('mikrotik_server')
