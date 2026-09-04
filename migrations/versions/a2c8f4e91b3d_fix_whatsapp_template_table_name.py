"""fix whatsapp_template table name to match the ORM's auto-generated name

Revision ID: a2c8f4e91b3d
Revises: e1a9c4f7b3d2
Create Date: 2026-08-28

The original migration (e1a9c4f7b3d2) created the table as 'whatsapp_template',
but the WhatsAppTemplate model has no explicit __tablename__, so Flask-SQLAlchemy
auto-generates 'whats_app_template' instead -- confirmed directly via this
project's installed flask_sqlalchemy.model.camel_to_snake_case('WhatsAppTemplate')
== 'whats_app_template', matching the pre-existing WhatsAppSettings model's real
table name 'whats_app_settings' (see migrations/versions/e7f175c0f952_baseline_
existing_schema.py). This mismatch was invisible to every test in this feature's
9-task implementation because the test suite builds its schema via
db.create_all() (tests/conftest.py), which derives the table name from the model
itself and is therefore always self-consistent -- it only surfaced against the
real migration-built schema in production:
    psycopg2.errors.UndefinedTable: relation "whats_app_template" does not exist
Renames the table (via ALTER TABLE ... RENAME TO, not a drop+recreate) so any
rows written between the bad migration's deploy and this fix are preserved.
"""
from alembic import op
from sqlalchemy import inspect


revision = 'a2c8f4e91b3d'
down_revision = 'e1a9c4f7b3d2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if 'whatsapp_template' in existing_tables and 'whats_app_template' not in existing_tables:
        op.rename_table('whatsapp_template', 'whats_app_template')
    elif 'whats_app_template' in existing_tables:
        print("NOTE: whats_app_template already exists -- skipping rename (nothing to do).")
    else:
        print("NOTE: neither whatsapp_template nor whats_app_template exists -- skipping (nothing to do).")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'whats_app_template' in set(inspector.get_table_names()):
        op.rename_table('whats_app_template', 'whatsapp_template')
