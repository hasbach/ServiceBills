"""add submit_guard

Revision ID: 3c1cae393f7c
Revises: 7c41f2e9b6d5
Create Date: 2026-11-17 00:00:00.000000

submit_guard: short-lived rows that let the app refuse the same money
request sent twice within a few seconds (double click, network retry).
A new table only -- no existing table is altered, so there is no lock on a
hot table during deploy.

Guarded because production's real schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = '3c1cae393f7c'
down_revision = '7c41f2e9b6d5'
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table('submit_guard'):
        return
    op.create_table(
        'submit_guard',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('who', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_index('ix_submit_guard_created_at', 'submit_guard', ['created_at'])
    op.create_index('ix_submit_guard_who', 'submit_guard', ['who'])


def downgrade():
    if _has_table('submit_guard'):
        op.drop_table('submit_guard')
