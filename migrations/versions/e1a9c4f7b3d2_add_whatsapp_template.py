"""add whatsapp_template table

Revision ID: e1a9c4f7b3d2
Revises: 9e7d5ce8b323
Create Date: 2026-08-28

Local cache of a tenant's Meta WhatsApp message templates (see
docs/superpowers/specs/2026-08-28-whatsapp-template-management-design.md).
Additive-only: one new table. Follows this repo's defensive-migration
discipline (existence checks, skip-with-NOTE rather than crash) per
c57bc44a51d0's documented rationale.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'e1a9c4f7b3d2'
down_revision = '9e7d5ce8b323'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if 'whatsapp_template' not in existing_tables:
        op.create_table(
            'whatsapp_template',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('language', sa.String(length=10), nullable=False),
            sa.Column('category', sa.String(length=20), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
            sa.Column('rejected_reason', sa.String(length=500), nullable=True),
            sa.Column('components', sa.JSON(), nullable=False),
            sa.Column('meta_template_id', sa.String(length=64), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_whatsapp_template_tenant_id', 'whatsapp_template', ['tenant_id'])
        op.create_index('ix_whatsapp_template_meta_template_id', 'whatsapp_template', ['meta_template_id'])
    else:
        print("NOTE: whatsapp_template table already exists -- skipping create (nothing to do).")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'whatsapp_template' in set(inspector.get_table_names()):
        op.drop_table('whatsapp_template')
