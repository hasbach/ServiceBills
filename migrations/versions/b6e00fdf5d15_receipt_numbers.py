"""receipt numbers on generated slips

Revision ID: b6e00fdf5d15
Revises: 3c1cae393f7c
Create Date: 2026-11-17

Roadmap 1B step 17. Every generated slip gets a number, sequential per
business and never reused:
- generated_receipt.receipt_number (nullable INTEGER) + unique index on
  (tenant_id, receipt_number);
- receipt_counter: the last number each business issued;
- existing slips are numbered per business in the order they were generated
  (generation_date, id), and each business's counter starts after them.
Additive only. generated_receipt is written in bulk once a month, but the
ALTER still uses the lock_timeout + savepoint retry so a deploy never queues
behind the live app.
"""
import time

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError


revision = 'b6e00fdf5d15'
down_revision = '3c1cae393f7c'
branch_labels = None
depends_on = None

TABLE = 'generated_receipt'
INDEX = 'uq_generated_receipt_tenant_number'


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


def upgrade():
    bind = op.get_bind()
    pg = bind.dialect.name == 'postgresql'
    insp = inspect(bind)

    if 'receipt_number' not in {c['name'] for c in insp.get_columns(TABLE)}:
        if pg:
            _with_lock_retry(bind, lambda: bind.execute(text(
                'ALTER TABLE {} ADD COLUMN IF NOT EXISTS receipt_number INTEGER'.format(TABLE))))
        else:
            with op.batch_alter_table(TABLE) as batch:
                batch.add_column(sa.Column('receipt_number', sa.Integer(), nullable=True))

    if 'receipt_counter' not in insp.get_table_names():
        op.create_table(
            'receipt_counter',
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('last_number', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.PrimaryKeyConstraint('tenant_id'),
        )

    # Number the slips that exist today, per business, oldest first.
    rows = bind.execute(text(
        'SELECT id, tenant_id FROM {} WHERE receipt_number IS NULL '
        'ORDER BY tenant_id, generation_date, id'.format(TABLE))).fetchall()
    last = {tid: n for tid, n in bind.execute(text(
        'SELECT tenant_id, COALESCE(MAX(receipt_number), 0) FROM {} GROUP BY tenant_id'.format(TABLE))).fetchall()}
    for rid, tid in rows:
        last[tid] = (last.get(tid) or 0) + 1
        bind.execute(text('UPDATE {} SET receipt_number = :n WHERE id = :id'.format(TABLE)),
                     {'n': last[tid], 'id': rid})
    counters = {tid for (tid,) in bind.execute(text('SELECT tenant_id FROM receipt_counter')).fetchall()}
    for tid, n in last.items():
        if tid in counters:
            bind.execute(text('UPDATE receipt_counter SET last_number = :n WHERE tenant_id = :t AND last_number < :n'),
                         {'n': n, 't': tid})
        else:
            bind.execute(text('INSERT INTO receipt_counter (tenant_id, last_number) VALUES (:t, :n)'),
                         {'t': tid, 'n': n})

    if INDEX not in {ix['name'] for ix in inspect(bind).get_indexes(TABLE)}:
        if pg:
            _with_lock_retry(bind, lambda: bind.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (tenant_id, receipt_number)'.format(INDEX, TABLE))))
        else:
            op.create_index(INDEX, TABLE, ['tenant_id', 'receipt_number'], unique=True)


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if INDEX in {ix['name'] for ix in insp.get_indexes(TABLE)}:
        op.drop_index(INDEX, table_name=TABLE)
    if 'receipt_counter' in insp.get_table_names():
        op.drop_table('receipt_counter')
    if 'receipt_number' in {c['name'] for c in insp.get_columns(TABLE)}:
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column('receipt_number')
