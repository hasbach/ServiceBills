"""Write tools (and the read helpers they need) for the MCP connector -- see
docs/superpowers/specs/2026-11-07-mcp-write-tools-design.md.

Every write calls the app's own endpoint in-process as the signed-in user via
call_api(), so role checks, the day-close lock, balance log, upstream sync and
WhatsApp sends run exactly as for a click in the web app. Nothing here writes
business data directly. Registered at import time (app.py imports this module
after mcp_tools)."""
from mcp_server import ALL_STAFF, DESK, FINANCE, PAYMENTS, Tool, ToolError, register_tool
from mcp_tools import (MAX_ROWS, _customer_row, _payment_row, amount_arg, bool_arg, call_api,
                       date_arg, day, int_arg, scoped, str_arg, today)

REASON = {'type': 'string', 'description': 'Why this is being undone (required, kept in the record/audit)'}
CUSTOMER_ID = {'type': 'integer', 'description': 'From search_customers'}


def _done(resp, **extra):
    msg = resp.get('message') if isinstance(resp, dict) else None
    return {'result': msg if isinstance(msg, str) and msg else 'Done.', **extra}


def _reason(args):
    return str_arg(args, 'reason', required=True, max_len=200)


def _customer_after(ctx, cid):
    c = scoped(ctx, ctx.appmod.Customer).filter_by(id=cid).first()
    return _customer_row(ctx, c, today(ctx)) if c else None


def _cid(args):
    return int_arg(args, 'customer_id', lo=1, required=True)


# --- read helpers -----------------------------------------------------------------

def list_plans(ctx, args):
    plans = scoped(ctx, ctx.appmod.SubscriptionPlan).order_by(ctx.appmod.SubscriptionPlan.name).all()
    return {'plans': [{'id': p.id, 'name': p.name, 'price': float(p.price or 0), 'currency': p.currency,
                       'billing_cycle': p.billing_cycle, 'status': p.status} for p in plans]}


def list_expense_categories(ctx, args):
    cats = scoped(ctx, ctx.appmod.ExpenseCategory).order_by(ctx.appmod.ExpenseCategory.name).all()
    return {'categories': [c.name for c in cats]}


def find_payments(ctx, args):
    am = ctx.appmod
    cid = _cid(args)
    if scoped(ctx, am.Customer).filter_by(id=cid).first() is None:
        raise ToolError(f'Customer {cid} not found.')
    P = am.Payment
    rows = (scoped(ctx, P).filter_by(customer_id=cid).order_by(P.date.desc(), P.id.desc())
            .limit(int_arg(args, 'limit', default=20, lo=1, hi=MAX_ROWS)).all())
    return {'customer_id': cid, 'payments': [_payment_row(ctx, p) for p in rows]}


register_tool(Tool(
    name='list_plans', title='List subscription plans',
    description='Subscription plans with id, name, price and billing cycle. Use the id for create_customer/update_customer.',
    input_schema={'type': 'object', 'properties': {}}, handler=list_plans, roles=ALL_STAFF))

register_tool(Tool(
    name='list_expense_categories', title='List expense categories',
    description='Expense category names, for add_expense/update_expense.',
    input_schema={'type': 'object', 'properties': {}}, handler=list_expense_categories, roles=FINANCE))

register_tool(Tool(
    name='find_payments', title="Customer's payments",
    description=("One customer's recent bills/payments with ids, newest first: amount, paid/collected, "
                 'refund/gratis/reverted flags. Use the id for revert_payment, refund_payment, mark_payment_gratis.'),
    input_schema={'type': 'object', 'required': ['customer_id'], 'properties': {
        'customer_id': CUSTOMER_ID, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS, 'default': 20}}},
    handler=find_payments, roles=PAYMENTS))


# --- customers & subscriptions ---------------------------------------------------

_CUSTOMER_TEXT = (('name', 100), ('phone', 30), ('address', 300), ('sector', 100),
                  ('upstream_username', 100), ('pppoe_username', 100), ('cpe_mac_address', 30),
                  ('notes', 2000))
_CUSTOMER_IDS = ('subscription_plan_id', 'reseller_id', 'upstream_provider_id')

_CUSTOMER_PROPS = {
    'name': {'type': 'string'}, 'phone': {'type': 'string'}, 'address': {'type': 'string'},
    'sector': {'type': 'string'},
    'subscription_plan_id': {'type': 'integer', 'description': 'From list_plans'},
    'subscription_start_date': {'type': 'string', 'description': 'YYYY-MM-DD; default today'},
    'reseller_id': {'type': 'integer', 'description': 'From list_resellers'},
    'upstream_provider_id': {'type': 'integer', 'description': 'From list_upstreams'},
    'upstream_username': {'type': 'string'}, 'pppoe_username': {'type': 'string'},
    'cpe_mac_address': {'type': 'string', 'description': "Customer's own router MAC"},
    'discount': {'type': 'number', 'minimum': 0}, 'notes': {'type': 'string'},
}


def _customer_fields(args):
    body = {}
    for key, n in _CUSTOMER_TEXT:
        v = str_arg(args, key, max_len=n)
        if v is not None:
            body[key] = v
    for key in _CUSTOMER_IDS:
        v = int_arg(args, key, lo=1)
        if v is not None:
            body[key] = v
    d = date_arg(args, 'subscription_start_date')
    if d is not None:
        body['subscription_start_date'] = day(d)
    discount = amount_arg(args, 'discount', required=False, allow_zero=True)
    if discount is not None:
        body['discount'] = discount
    return body


def create_customer(ctx, args):
    for key in ('name', 'phone'):
        str_arg(args, key, required=True)
    int_arg(args, 'subscription_plan_id', lo=1, required=True)
    body = {'address': '', **_customer_fields(args)}
    resp = call_api(ctx, '/api/customers', method='POST', json=body)
    return _done(resp, customer=_customer_after(ctx, resp.get('customer_id')))


def update_customer(ctx, args):
    cid = _cid(args)
    body = _customer_fields(args)   # never balance / is_subscription_active
    if 'whatsapp_notifications_enabled' in args:
        body['whatsapp_notifications_enabled'] = bool_arg(args, 'whatsapp_notifications_enabled')
    if not body:
        raise ToolError('Nothing to change: pass at least one field.')
    resp = call_api(ctx, f'/api/customers/{cid}', method='PUT', json=body)
    return _done(resp, customer=_customer_after(ctx, cid))


def _subscription_action(method, suffix):
    def handler(ctx, args):
        cid = _cid(args)
        if suffix == 'cancel_subscription':
            _reason(args)
        resp = call_api(ctx, f'/api/customers/{cid}/{suffix}', method=method, json={})
        return _done(resp, customer=_customer_after(ctx, cid))
    return handler


register_tool(Tool(
    name='create_customer', title='Add customer',
    description=('Add a new customer on a subscription plan (find plan ids with list_plans). Creates the first '
                 'bill and sends the welcome WhatsApp exactly like the web app. Confirm details with the user first.'),
    input_schema={'type': 'object', 'required': ['name', 'phone', 'subscription_plan_id'],
                  'properties': _CUSTOMER_PROPS},
    handler=create_customer, roles=DESK, writes=True, dedupe=True))

register_tool(Tool(
    name='update_customer', title='Edit customer',
    description=('Change an existing customer: contact details, sector, plan, reseller, upstream/PPPoE '
                 'usernames, router MAC, discount, notes, WhatsApp notifications. Only the fields you pass '
                 'change. Balance cannot be edited here.'),
    input_schema={'type': 'object', 'required': ['customer_id'], 'properties': {
        'customer_id': CUSTOMER_ID, **_CUSTOMER_PROPS,
        'whatsapp_notifications_enabled': {'type': 'boolean'}}},
    handler=update_customer, roles=DESK, writes=True))

register_tool(Tool(
    name='renew_subscription', title='Renew subscription',
    description=('Renew a customer for one more billing cycle: extends expiry, adds the bill, settles from '
                 'credit if any and syncs the upstream, like Renew in the web app.'),
    input_schema={'type': 'object', 'required': ['customer_id'], 'properties': {'customer_id': CUSTOMER_ID}},
    handler=_subscription_action('POST', 'renew_subscription'), roles=DESK, writes=True, dedupe=True))

register_tool(Tool(
    name='activate_subscription', title='Activate subscription',
    description='Re-activate a customer whose subscription is cancelled/inactive.',
    input_schema={'type': 'object', 'required': ['customer_id'], 'properties': {'customer_id': CUSTOMER_ID}},
    handler=_subscription_action('PUT', 'activate_subscription'), roles=DESK, writes=True))

register_tool(Tool(
    name='cancel_subscription', title='Cancel subscription',
    description='Cancel (deactivate) a customer\'s subscription. Undo with activate_subscription.',
    input_schema={'type': 'object', 'required': ['customer_id', 'reason'],
                  'properties': {'customer_id': CUSTOMER_ID, 'reason': REASON}},
    handler=_subscription_action('PUT', 'cancel_subscription'), roles=DESK, writes=True, destructive=True))

register_tool(Tool(
    name='network_suspend', title='Suspend on network',
    description="Block a customer's internet on the business's own network (Mikrotik). Undo with network_unsuspend.",
    input_schema={'type': 'object', 'required': ['customer_id'], 'properties': {'customer_id': CUSTOMER_ID}},
    handler=_subscription_action('POST', 'network-suspend'), roles=FINANCE, module='network', writes=True))

register_tool(Tool(
    name='network_unsuspend', title='Unsuspend on network',
    description="Restore a customer's internet on the business's own network (Mikrotik).",
    input_schema={'type': 'object', 'required': ['customer_id'], 'properties': {'customer_id': CUSTOMER_ID}},
    handler=_subscription_action('POST', 'network-unsuspend'), roles=FINANCE, module='network', writes=True))
