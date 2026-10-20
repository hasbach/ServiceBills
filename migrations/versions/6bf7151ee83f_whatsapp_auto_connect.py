"""whatsapp auto-connect: connection columns, template param_map, connection events

Revision ID: 6bf7151ee83f
Revises: 58a2f084761c
Create Date: 2026-10-20

See specs/001-whatsapp-auto-connect/data-model.md. Additive only. Every
existing whats_app_settings row is backfilled as connection_method='manual'
(connected when it already holds a token and phone number id), so manual
tenants keep working unchanged. whats_app_settings is read on every send and
webhook, so its ALTERs go through the lock_timeout + savepoint retry.

Deliberately NO unique index on phone_number_id/business_account_id: existing
manual rows may already share them, and the index would fail the deploy.
"""
import time

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError


revision = '6bf7151ee83f'
down_revision = '58a2f084761c'
branch_labels = None
depends_on = None


def _with_lock_retry(bind, ddl, attempts=60):
    """Run `ddl` on Postgres without queueing behind the live app: short
    lock_timeout inside a savepoint, retried until a gap opens."""
    for attempt in range(attempts):
        savepoint = bind.begin_nested()
        try:
            bind.execute(text("SET LOCAL lock_timeout = '2s'"))
            ddl()
            bind.execute(text("SET LOCAL lock_timeout = 0"))
            savepoint.commit()
            return
        except OperationalError as exc:
            savepoint.rollback()
            if 'lock timeout' not in str(exc).lower() or attempt == attempts - 1:
                raise
        time.sleep(2)


# (name, postgres DDL type, sqlalchemy type)
_SETTINGS_COLUMNS = (
    ('connection_method', "VARCHAR(20) NOT NULL DEFAULT 'manual'",
     sa.String(length=20)),
    ('connection_status', "VARCHAR(20) NOT NULL DEFAULT 'disconnected'",
     sa.String(length=20)),
    ('status_reason', 'VARCHAR(300)', sa.String(length=300)),
    ('meta_business_id', 'VARCHAR(64)', sa.String(length=64)),
    ('meta_business_name', 'VARCHAR(200)', sa.String(length=200)),
    ('display_phone_number', 'VARCHAR(32)', sa.String(length=32)),
    ('verified_name', 'VARCHAR(200)', sa.String(length=200)),
    ('quality_rating', 'VARCHAR(16)', sa.String(length=16)),
    ('messaging_limit_tier', 'VARCHAR(32)', sa.String(length=32)),
    ('payment_method_status', 'VARCHAR(16)', sa.String(length=16)),
    ('registration_pin', 'TEXT', sa.Text()),
    ('token_obtained_at', 'TIMESTAMP WITHOUT TIME ZONE', sa.DateTime()),
    ('connected_at', 'TIMESTAMP WITHOUT TIME ZONE', sa.DateTime()),
    ('connected_by_user_id', 'INTEGER REFERENCES "user"(id) ON DELETE SET NULL', sa.Integer()),
    ('last_health_check_at', 'TIMESTAMP WITHOUT TIME ZONE', sa.DateTime()),
)
_NOT_NULL_DEFAULTS = {'connection_method': 'manual', 'connection_status': 'disconnected'}

_TEMPLATE_COLUMNS = (
    ('standard_key', 'VARCHAR(40)', sa.String(length=40)),
    ('param_map', 'JSON', sa.JSON()),
)


def _add_columns(bind, pg, table, columns):
    existing = {c['name'] for c in inspect(bind).get_columns(table)}
    for name, pg_type, sa_type in columns:
        if name in existing:
            continue
        if pg:
            _with_lock_retry(bind, lambda: bind.execute(text(
                'ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}'.format(table, name, pg_type))))
        else:
            default = _NOT_NULL_DEFAULTS.get(name)
            with op.batch_alter_table(table) as batch:
                if default is not None:
                    batch.add_column(sa.Column(name, sa_type, nullable=False, server_default=default))
                elif name == 'connected_by_user_id':
                    batch.add_column(sa.Column(name, sa_type, sa.ForeignKey(
                        'user.id', name='fk_whats_app_settings_connected_by_user_id',
                        ondelete='SET NULL'), nullable=True))
                else:
                    batch.add_column(sa.Column(name, sa_type, nullable=True))


def upgrade():
    bind = op.get_bind()
    pg = bind.dialect.name == 'postgresql'

    _add_columns(bind, pg, 'whats_app_settings', _SETTINGS_COLUMNS)
    _add_columns(bind, pg, 'whats_app_template', _TEMPLATE_COLUMNS)

    # FR-018: every pre-existing row is a manual configuration.
    bind.execute(text(
        "UPDATE whats_app_settings SET connection_method = 'manual', connection_status = "
        "CASE WHEN access_token IS NOT NULL AND access_token <> '' "
        "AND phone_number_id IS NOT NULL AND phone_number_id <> '' "
        "THEN 'connected' ELSE 'disconnected' END"))

    if 'whatsapp_connection_event' not in inspect(bind).get_table_names():
        def create_event_table():
            op.create_table(
                'whatsapp_connection_event',
                sa.Column('id', sa.Integer(), primary_key=True),
                sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
                sa.Column('user_id', sa.Integer(),
                          sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
                sa.Column('event', sa.String(length=32), nullable=False),
                sa.Column('method', sa.String(length=20), nullable=True),
                sa.Column('from_status', sa.String(length=20), nullable=True),
                sa.Column('to_status', sa.String(length=20), nullable=True),
                sa.Column('source', sa.String(length=20), nullable=False),
                sa.Column('detail', sa.String(length=500), nullable=True),
                sa.Column('created_at', sa.DateTime(), nullable=False),
            )
            op.create_index('ix_whatsapp_connection_event_tenant_id',
                            'whatsapp_connection_event', ['tenant_id'])
            op.create_index('ix_whatsapp_connection_event_created_at',
                            'whatsapp_connection_event', ['created_at'])

        # The foreign keys briefly lock tenant/user too.
        if pg:
            _with_lock_retry(bind, create_event_table)
        else:
            create_event_table()


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if 'whatsapp_connection_event' in insp.get_table_names():
        op.drop_index('ix_whatsapp_connection_event_created_at', table_name='whatsapp_connection_event')
        op.drop_index('ix_whatsapp_connection_event_tenant_id', table_name='whatsapp_connection_event')
        op.drop_table('whatsapp_connection_event')
    for table, columns in (('whats_app_template', _TEMPLATE_COLUMNS),
                           ('whats_app_settings', _SETTINGS_COLUMNS)):
        existing = {c['name'] for c in inspect(bind).get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for name, _, _ in columns:
                if name in existing:
                    batch.drop_column(name)
