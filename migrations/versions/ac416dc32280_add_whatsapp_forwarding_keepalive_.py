"""add whatsapp forwarding keepalive template and last-sent tracking

Revision ID: ac416dc32280
Revises: 5bb2921fc906
Create Date: 2026-08-12 05:39:00.146031

Supports the daily WhatsApp keep-alive template send (send_daily_whatsapp_keepalive
in app.py) that prompts an auto-reply on forwarding_mobile's own device, which is
what actually opens the 24h session the restored raw customer-reply forward
depends on. See docs/superpowers/specs/2026-08-12-whatsapp-forwarding-keepalive.md.

NOTE: autogenerate again reported the same pre-existing, unrelated drift already
documented in fd0c324cba0d/5bb2921fc906 (stale payment.reseller_id, missing unique
constraints, cosmetic EncryptedString type diff) -- omitted here for the same
reason, not touched by this migration.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ac416dc32280'
down_revision = '5bb2921fc906'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('whats_app_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('template_forward_keepalive', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('last_forwarding_keepalive_sent_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('whats_app_settings', schema=None) as batch_op:
        batch_op.drop_column('last_forwarding_keepalive_sent_at')
        batch_op.drop_column('template_forward_keepalive')
