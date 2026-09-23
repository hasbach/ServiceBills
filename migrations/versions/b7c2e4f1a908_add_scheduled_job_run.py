"""add scheduled_job_run audit table

Revision ID: b7c2e4f1a908
Revises: f1a2b3c4d5e6
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c2e4f1a908'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'scheduled_job_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_name', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=15), nullable=False, server_default='running'),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_scheduled_job_run'))
    )
    with op.batch_alter_table('scheduled_job_run', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_scheduled_job_run_job_name'), ['job_name'], unique=False)


def downgrade():
    with op.batch_alter_table('scheduled_job_run', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_scheduled_job_run_job_name'))
    op.drop_table('scheduled_job_run')
