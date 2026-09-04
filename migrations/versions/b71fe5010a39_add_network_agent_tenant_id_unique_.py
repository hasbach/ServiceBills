"""add network_agent.tenant_id unique constraint

Revision ID: b71fe5010a39
Revises: 5f65a6fd6e8d
Create Date: 2026-09-04 15:00:00.000000

"One agent per tenant" was enforced only by the create route's pre-insert
`tenant_query(NetworkAgent).first()` check (see create_network_agent in
app.py) -- convention, not schema. Two concurrent creates for the same
tenant can both pass that check before either commits, leaving one tenant
with two live agents and two valid tokens. Not reachable today (this repo
runs a single synchronous gunicorn worker, so requests serialize), but the
whole point of a schema constraint is that it survives a deployment change
nobody remembers to reason about.

This tightens network_agent.tenant_id from the plain, non-unique index
created by 5f65a6fd6e8d to a unique constraint. The application-level check
stays -- it produces the friendly 400; this is the backstop that makes a
double-insert impossible even if that check is ever raced past.

Hand-written, matching 5f65a6fd6e8d's own note: this repo's local SQLite dev
database can't reach the real Postgres-targeted head (bd054e2e7cf9 calls
op.create_unique_constraint outside batch mode, unsupported by SQLite), so
autogenerate has nothing valid to diff against locally. See
tests/test_topology_migration.py for how this migration's upgrade()/
downgrade() are exercised directly against a bootstrapped pre-migration
schema instead of by walking the full chain.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b71fe5010a39'
down_revision = '5f65a6fd6e8d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('network_agent', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_network_agent_tenant_id'))
        batch_op.create_unique_constraint(
            batch_op.f('uq_network_agent_tenant_id'), ['tenant_id'])


def downgrade():
    with op.batch_alter_table('network_agent', schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f('uq_network_agent_tenant_id'), type_='unique')
        batch_op.create_index(
            batch_op.f('ix_network_agent_tenant_id'), ['tenant_id'], unique=False)
