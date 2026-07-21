"""drop system update settings table

Revision ID: 425728acb5e8
Revises: 9dd046c3dc10
Create Date: 2026-07-21 03:33:26.051810

The "Software & System Updates" feature (git-pull/subprocess self-update,
inherited from a pre-multi-tenant PythonAnywhere deployment model) made no
sense once deployment moved to git-push -> Render Docker rebuild, and its
apply-update route ran git/flask-db-upgrade subprocesses against the live
container for no benefit. Dropping the table with the feature.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '425728acb5e8'
down_revision = '9dd046c3dc10'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('system_update_settings')


def downgrade():
    op.create_table(
        'system_update_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('current_version', sa.String(length=50), nullable=False),
        sa.Column('github_repo', sa.String(length=200), nullable=False),
        sa.Column('auto_update_enabled', sa.Boolean(), nullable=False),
        sa.Column('auto_update_time', sa.String(length=10), nullable=False),
        sa.Column('platform', sa.String(length=50), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
        sa.Column('last_updated_at', sa.DateTime(), nullable=True),
        sa.Column('latest_available_version', sa.String(length=50), nullable=True),
        sa.Column('release_notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'],
                                 name=op.f('fk_system_update_settings_tenant_id_tenant')),
    )
    op.create_index(op.f('ix_system_update_settings_tenant_id'),
                     'system_update_settings', ['tenant_id'])
