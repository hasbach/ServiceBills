"""ledger books tables

Revision ID: 769620e9ddfd
Revises: b6e00fdf5d15
Create Date: 2026-11-17 00:00:00.000000

The double-entry books (roadmap 1C): ledger_account, journal_entry,
journal_line, ledger_start_line, ledger_state, ledger_problem. New tables
only -- no existing table is altered, so there is no lock on a hot table
during deploy.

Guarded because production's real schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = '769620e9ddfd'
down_revision = 'b6e00fdf5d15'
branch_labels = None
depends_on = None

_TABLES = ('journal_line', 'ledger_start_line', 'ledger_problem',
           'journal_entry', 'ledger_state', 'ledger_account')


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _money():
    return sa.Numeric(precision=18, scale=4, asdecimal=False)


def upgrade():
    if not _has_table('ledger_account'):
        op.create_table(
            'ledger_account',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('key', sa.String(length=60), nullable=False),
            sa.Column('number', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('type', sa.String(length=10), nullable=False),
            sa.Column('party_type', sa.String(length=20), nullable=True),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_ledger_account_tenant_id', 'ledger_account', ['tenant_id'])
        op.create_index('ux_ledger_account_tenant_key', 'ledger_account',
                        ['tenant_id', 'key'], unique=True)

    if not _has_table('journal_entry'):
        op.create_table(
            'journal_entry',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('entry_date', sa.Date(), nullable=False),
            sa.Column('kind', sa.String(length=12), nullable=False),
            sa.Column('memo', sa.String(length=300), nullable=True),
            sa.Column('created_by', sa.String(length=100), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_journal_entry_tenant_id', 'journal_entry', ['tenant_id'])

    if not _has_table('journal_line'):
        op.create_table(
            'journal_line',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('entry_id', sa.Integer(), nullable=False),
            sa.Column('account_id', sa.Integer(), nullable=False),
            sa.Column('party_type', sa.String(length=20), nullable=True),
            sa.Column('party_id', sa.Integer(), nullable=True),
            sa.Column('debit', _money(), nullable=False),
            sa.Column('credit', _money(), nullable=False),
            sa.Column('source_type', sa.String(length=30), nullable=True),
            sa.Column('source_id', sa.Integer(), nullable=True),
            sa.Column('entry_date', sa.Date(), nullable=False),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.ForeignKeyConstraint(['entry_id'], ['journal_entry.id']),
            sa.ForeignKeyConstraint(['account_id'], ['ledger_account.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_journal_line_tenant_id', 'journal_line', ['tenant_id'])
        op.create_index('ix_journal_line_tenant_account_date', 'journal_line',
                        ['tenant_id', 'account_id', 'entry_date'])
        op.create_index('ix_journal_line_source', 'journal_line', ['source_type', 'source_id'])
        op.create_index('ix_journal_line_tenant_party', 'journal_line',
                        ['tenant_id', 'party_type', 'party_id'])

    if not _has_table('ledger_start_line'):
        op.create_table(
            'ledger_start_line',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('source_type', sa.String(length=30), nullable=False),
            sa.Column('source_id', sa.Integer(), nullable=False),
            sa.Column('account_key', sa.String(length=60), nullable=False),
            sa.Column('party_type', sa.String(length=20), nullable=True),
            sa.Column('party_id', sa.Integer(), nullable=True),
            sa.Column('amount', _money(), nullable=False),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_ledger_start_line_tenant_id', 'ledger_start_line', ['tenant_id'])
        op.create_index('ix_ledger_start_line_source', 'ledger_start_line',
                        ['source_type', 'source_id'])

    if not _has_table('ledger_state'):
        op.create_table(
            'ledger_state',
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=False),
            sa.Column('start_day', sa.Date(), nullable=False),
            sa.Column('opening_entry_id', sa.Integer(), nullable=True),
            sa.Column('last_check_at', sa.DateTime(), nullable=True),
            sa.Column('last_check', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.PrimaryKeyConstraint('tenant_id'),
        )

    if not _has_table('ledger_problem'):
        op.create_table(
            'ledger_problem',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('source_type', sa.String(length=30), nullable=False),
            sa.Column('source_id', sa.Integer(), nullable=True),
            sa.Column('error', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_ledger_problem_tenant_id', 'ledger_problem', ['tenant_id'])


def downgrade():
    for name in _TABLES:  # children before parents
        if _has_table(name):
            op.drop_table(name)
