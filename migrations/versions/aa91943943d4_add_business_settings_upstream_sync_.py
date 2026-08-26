"""add business_settings upstream_sync_automation_enabled

Revision ID: aa91943943d4
Revises: 386fdce26837
Create Date: 2026-08-26 20:23:12.781164

Phase 3 network automation: a per-tenant opt-in flag for the new scheduled
upstream-status-sync job, off by default. Existing rows backfill to FALSE
via server_default so this is a genuine no-op for every tenant until they
explicitly opt in from Settings.

Follows this repo's established defensive-migration discipline (see
c57bc44a51d0's docstring for why): checked for an existing same-named
column first, skipped with a NOTE rather than crashing if already present,
given the documented history of this project's migrations disagreeing
with the real production schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'aa91943943d4'
down_revision = '386fdce26837'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c['name'] for c in inspect(bind).get_columns('business_settings')}
    if 'upstream_sync_automation_enabled' in columns:
        print("NOTE: business_settings.upstream_sync_automation_enabled already exists -- skipping add (nothing to do).")
        return
    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'upstream_sync_automation_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    bind = op.get_bind()
    columns = {c['name'] for c in inspect(bind).get_columns('business_settings')}
    if 'upstream_sync_automation_enabled' not in columns:
        print("NOTE: business_settings.upstream_sync_automation_enabled already absent -- skipping drop (nothing to do).")
        return
    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        batch_op.drop_column('upstream_sync_automation_enabled')
