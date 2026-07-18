"""reconcile tenant FKs and per-tenant unique names

Revision ID: 56a5ae0f8bb0
Revises: c297867b50b2
Create Date: 2026-07-17 15:57:05.592422

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '56a5ae0f8bb0'
down_revision = 'c297867b50b2'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect as _sa_inspect
    # Naming convention lets batch reflection name a legacy UNNAMED unique index so
    # drop_constraint can resolve it. Legacy create_all schemas vary (some tables have
    # a single-column unique on `name`, some don't), so drop only when present.
    _naming = {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
    _insp = _sa_inspect(op.get_bind())

    def _has_name_unique(table):
        for u in _insp.get_unique_constraints(table):
            if u.get('column_names') == ['name']:
                return True
        for ix in _insp.get_indexes(table):
            if ix.get('unique') and ix.get('column_names') == ['name']:
                return True
        return False

    with op.batch_alter_table('addon_purchase', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_addon_purchase_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_business_settings_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_customer_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('customer_feedback', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_customer_feedback_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('expense', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_expense_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('expense_category', schema=None, naming_convention=_naming) as batch_op:
        if _has_name_unique('expense_category'):
            batch_op.drop_constraint('uq_expense_category_name', type_='unique')
        batch_op.create_unique_constraint('uq_expense_category_tenant_name', ['tenant_id', 'name'])
        batch_op.create_foreign_key('fk_expense_category_tenant_id_tenant', 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('generated_receipt', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_generated_receipt_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_payment_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('payment_reminder', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_payment_reminder_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('push_subscription', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_push_subscription_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('reseller', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_reseller_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('reseller_payment', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_reseller_payment_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('sector', schema=None, naming_convention=_naming) as batch_op:
        if _has_name_unique('sector'):
            batch_op.drop_constraint('uq_sector_name', type_='unique')
        batch_op.create_unique_constraint('uq_sector_tenant_name', ['tenant_id', 'name'])
        batch_op.create_foreign_key('fk_sector_tenant_id_tenant', 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('service_outage', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_service_outage_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('service_status', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_service_status_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('subscription_plan', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_subscription_plan_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('supplier', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_supplier_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('supplier_payment', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_supplier_payment_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('support_ticket', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_support_ticket_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('system_update_settings', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_system_update_settings_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('ticket_log', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_ticket_log_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_user_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('whats_app_settings', schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f('fk_whats_app_settings_tenant_id_tenant'), 'tenant', ['tenant_id'], ['id'])

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('whats_app_settings', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_whats_app_settings_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_user_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('ticket_log', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_ticket_log_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('system_update_settings', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_system_update_settings_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('support_ticket', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_support_ticket_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('supplier_payment', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_supplier_payment_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('supplier', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_supplier_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('subscription_plan', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_subscription_plan_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('service_status', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_service_status_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('service_outage', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_service_outage_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('sector', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_sector_tenant_id_tenant'), type_='foreignkey')
        batch_op.drop_constraint('uq_sector_tenant_name', type_='unique')
        batch_op.create_unique_constraint(batch_op.f('uq_sector_name'), ['name'])

    with op.batch_alter_table('reseller_payment', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_reseller_payment_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('reseller', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_reseller_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('push_subscription', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_push_subscription_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('payment_reminder', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_payment_reminder_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_payment_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('generated_receipt', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_generated_receipt_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('expense_category', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_expense_category_tenant_id_tenant'), type_='foreignkey')
        batch_op.drop_constraint('uq_expense_category_tenant_name', type_='unique')
        batch_op.create_unique_constraint(batch_op.f('uq_expense_category_name'), ['name'])

    with op.batch_alter_table('expense', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_expense_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('customer_feedback', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_customer_feedback_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_customer_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('business_settings', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_business_settings_tenant_id_tenant'), type_='foreignkey')

    with op.batch_alter_table('addon_purchase', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_addon_purchase_tenant_id_tenant'), type_='foreignkey')

    # ### end Alembic commands ###
