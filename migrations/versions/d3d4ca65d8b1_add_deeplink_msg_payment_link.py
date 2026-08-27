"""add whats_app_settings.deeplink_msg_payment_link

Revision ID: d3d4ca65d8b1
Revises: bcf390ad78f1
Create Date: 2026-08-27

Deep-link message template for the manual "Resend payment link" action --
see docs/superpowers/plans/2026-08-27-tenant-whish-customer-payments.md,
Task 12. Table name confirmed as whats_app_settings (not whatsapp_settings)
per the note in bcf390ad78f1's own migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'd3d4ca65d8b1'
down_revision = 'bcf390ad78f1'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('whats_app_settings')}
    if 'deeplink_msg_payment_link' in columns:
        print("NOTE: whats_app_settings.deeplink_msg_payment_link already exists -- skipping (nothing to do).")
        return
    op.add_column('whats_app_settings', sa.Column('deeplink_msg_payment_link', sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('whats_app_settings')}
    if 'deeplink_msg_payment_link' in columns:
        op.drop_column('whats_app_settings', 'deeplink_msg_payment_link')
