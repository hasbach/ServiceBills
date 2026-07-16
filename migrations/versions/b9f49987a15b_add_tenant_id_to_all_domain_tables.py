"""add tenant_id to all domain tables

Revision ID: b9f49987a15b
Revises: e7f175c0f952
Create Date: 2026-07-17 01:29:02.754563

Hand-written from the autogenerate scaffold: because every domain table is
already populated, tenant_id is added nullable, backfilled to a single
"default" tenant, then made NOT NULL with an index. FK constraints are not
emitted here (SQLite does not enforce them by default); they are reconciled
when the schema moves to Postgres in Phase 3.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9f49987a15b'
down_revision = 'e7f175c0f952'
branch_labels = None
depends_on = None


DOMAIN_TABLES = [
    "addon_purchase", "business_settings", "customer", "customer_feedback",
    "expense", "expense_category", "generated_receipt", "payment",
    "payment_reminder", "push_subscription", "reseller", "reseller_payment",
    "sector", "service_outage", "service_status", "subscription_plan",
    "supplier", "supplier_payment", "support_ticket", "system_update_settings",
    "ticket_log", "whats_app_settings",
]


def upgrade():
    # 1. Create the tenant table.
    op.create_table(
        'tenant',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('slug', sa.String(length=80), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('plan', sa.String(length=20), nullable=False),
        sa.Column('stripe_customer_id', sa.String(length=120), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )

    conn = op.get_bind()

    # 2. Create the default tenant that all existing data belongs to.
    conn.execute(sa.text(
        "INSERT INTO tenant (name, slug, status, plan, created_at) "
        "VALUES ('Default Business', 'default', 'active', 'pro', CURRENT_TIMESTAMP)"
    ))
    default_tenant_id = conn.execute(
        sa.text("SELECT id FROM tenant WHERE slug = 'default'")
    ).scalar()

    # 3. For each domain table: add nullable column, backfill, make NOT NULL, index.
    for table in DOMAIN_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        conn.execute(
            sa.text(f"UPDATE {table} SET tenant_id = :tid"),
            {"tid": default_tenant_id},
        )
        with op.batch_alter_table(table) as batch:
            batch.alter_column('tenant_id', existing_type=sa.Integer(), nullable=False)
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])


def downgrade():
    for table in DOMAIN_TABLES:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column('tenant_id')
    op.drop_table('tenant')
