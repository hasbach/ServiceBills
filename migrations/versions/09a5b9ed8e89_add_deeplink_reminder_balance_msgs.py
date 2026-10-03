"""add whats_app_settings.deeplink_msg_payment_reminder / deeplink_msg_current_balance

Revision ID: 09a5b9ed8e89
Revises: f2a5b6c7d8e9
Create Date: 2026-10-03

Editable deep-link texts for the "Send WhatsApp reminder" dialog on the
Subscriptions page (payment reminder / current balance) -- these were
hardcoded in SubscriptionsView.js. NULL falls back to the old text in
WhatsAppSettings.to_dict(), so existing tenants see no change.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '09a5b9ed8e89'
down_revision = 'f2a5b6c7d8e9'
branch_labels = None
depends_on = None

_COLUMNS = ('deeplink_msg_payment_reminder', 'deeplink_msg_current_balance')


def upgrade():
    bind = op.get_bind()
    columns = {c['name'] for c in inspect(bind).get_columns('whats_app_settings')}
    for name in _COLUMNS:
        if name in columns:
            print(f"NOTE: whats_app_settings.{name} already exists -- skipping.")
            continue
        op.add_column('whats_app_settings', sa.Column(name, sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    columns = {c['name'] for c in inspect(bind).get_columns('whats_app_settings')}
    for name in _COLUMNS:
        if name in columns:
            op.drop_column('whats_app_settings', name)
