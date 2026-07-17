"""add user email and email_verified

Revision ID: d5b73c902f25
Revises: 56a5ae0f8bb0
Create Date: 2026-07-17 16:51:17.312049

Adds nullable email (unique) and email_verified to user. Existing users predate
verification, so they are backfilled verified=True (not locked out); the column
is then made NOT NULL (new signups default False via the model).

NOTE: autogenerate also reported an app_secret VARCHAR->EncryptedString "type
change" — that is cosmetic (EncryptedString stores as TEXT; same column) and is
intentionally omitted to avoid a needless table rebuild.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5b73c902f25'
down_revision = '56a5ae0f8bb0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('email_verified', sa.Boolean(), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('uq_user_email'), ['email'])

    op.execute(sa.text('UPDATE "user" SET email_verified = :v').bindparams(v=True))

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('email_verified', existing_type=sa.Boolean(), nullable=False)


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_user_email'), type_='unique')
        batch_op.drop_column('email_verified')
        batch_op.drop_column('email')
