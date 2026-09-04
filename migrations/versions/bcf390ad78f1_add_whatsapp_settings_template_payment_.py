"""add whats_app_settings.template_payment_link

Revision ID: bcf390ad78f1
Revises: ea2bafb2a3fd
Create Date: 2026-08-27

New WhatsApp Cloud API template name for automatic CustomerPaymentLink
delivery -- see
docs/superpowers/plans/2026-08-27-tenant-whish-customer-payments.md, Task 11.
Not part of the plan's own migration list (a gap caught during
implementation) -- WhatsAppSettings is an existing table, so this new
column needs the same defensive, existence-checked migration every other
schema change in this repo gets.

Real table name is `whats_app_settings`, NOT `whatsapp_settings` -- caught
by actually running this against real Postgres (crashed with
NoSuchTableError otherwise): the WhatsAppSettings model has no explicit
__tablename__, so SQLAlchemy's default CamelCase->snake_case splits
"WhatsApp" as two separate words ("Whats" + "App"), not one.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'bcf390ad78f1'
down_revision = 'ea2bafb2a3fd'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('whats_app_settings')}
    if 'template_payment_link' in columns:
        print("NOTE: whats_app_settings.template_payment_link already exists -- skipping (nothing to do).")
        return
    op.add_column('whats_app_settings', sa.Column('template_payment_link', sa.String(length=200), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('whats_app_settings')}
    if 'template_payment_link' in columns:
        op.drop_column('whats_app_settings', 'template_payment_link')
