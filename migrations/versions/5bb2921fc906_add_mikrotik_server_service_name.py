"""add mikrotik_server.service_name

Revision ID: 5bb2921fc906
Revises: fd0c324cba0d
Create Date: 2026-08-12 02:56:04.822401

RouterOS /ppp/secret allows duplicate `name` values as long as `service`
differs -- this is a real scenario on shared last-mile infrastructure where two
unrelated ISPs each have their own subscriber named e.g. "user1". Without this
column, a lookup/enable/disable by username alone could land on the wrong ISP's
customer. Nullable: a tenant with no such sharing leaves it blank.

NOTE: autogenerate again reported the same pre-existing, unrelated drift already
documented in fd0c324cba0d (stale payment.reseller_id, missing unique
constraints, cosmetic EncryptedString type diff) -- omitted here for the same
reason, not touched by this migration.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5bb2921fc906'
down_revision = 'fd0c324cba0d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mikrotik_server', schema=None) as batch_op:
        batch_op.add_column(sa.Column('service_name', sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table('mikrotik_server', schema=None) as batch_op:
        batch_op.drop_column('service_name')
