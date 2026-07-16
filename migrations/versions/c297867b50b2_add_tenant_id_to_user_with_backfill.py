"""add tenant_id to user with backfill

Revision ID: c297867b50b2
Revises: b9f49987a15b
Create Date: 2026-07-17 01:33:20.336802

Adds a nullable tenant_id to the user table (NULL = platform super-admin) and
backfills existing users to the default tenant so they remain able to sign in
to tenant-scoped routes.

NOTE: autogenerate also reports missing FK constraints on the 22 domain tables
(the Task 1.3 SQLite migration intentionally omitted them). Those are left for
the Phase 3 Postgres migration, where a naming convention lets Alembic manage
them cleanly. This migration deliberately touches only the user table.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c297867b50b2'
down_revision = 'b9f49987a15b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_index('ix_user_tenant_id', 'user', ['tenant_id'])

    # Backfill existing users to the default tenant created in the previous migration.
    conn = op.get_bind()
    default_tenant_id = conn.execute(
        sa.text("SELECT id FROM tenant WHERE slug = 'default'")
    ).scalar()
    if default_tenant_id is not None:
        conn.execute(
            sa.text("UPDATE user SET tenant_id = :tid WHERE tenant_id IS NULL"),
            {"tid": default_tenant_id},
        )


def downgrade():
    op.drop_index('ix_user_tenant_id', table_name='user')
    with op.batch_alter_table('user') as batch:
        batch.drop_column('tenant_id')
