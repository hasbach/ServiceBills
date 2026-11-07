"""Write tools (and the read helpers they need) for the MCP connector -- see
docs/superpowers/specs/2026-11-07-mcp-write-tools-design.md.

Every write calls the app's own endpoint in-process as the signed-in user via
call_api(), so role checks, the day-close lock, balance log, upstream sync and
WhatsApp sends run exactly as for a click in the web app. Nothing here writes
business data directly. Registered at import time (app.py imports this module
after mcp_tools)."""
import re

from mcp_server import ADMIN, ALL_STAFF, COLLECT, DESK, FINANCE, PAYMENTS, Tool, ToolError, register_tool
from mcp_tools import (MAX_ROWS, _customer_row, _payment_row, amount_arg, bool_arg, call_api,
                       date_arg, day, enum_arg, int_arg, scoped, str_arg, today)

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


def _subscription_action(method, suffix, needs_reason=False):
    def handler(ctx, args):
        cid = _cid(args)
        if needs_reason:
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
                 'change. Balance cannot be edited here. Changing reseller_id moves the customer\'s '
                 'balance/debt to the new reseller; confirm with the user first.'),
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
    description=('Cancel (deactivate) a customer\'s subscription. Undo with activate_subscription. '
                 'The reason is kept in the AI audit log.'),
    input_schema={'type': 'object', 'required': ['customer_id', 'reason'],
                  'properties': {'customer_id': CUSTOMER_ID, 'reason': REASON}},
    handler=_subscription_action('PUT', 'cancel_subscription', needs_reason=True), roles=DESK, writes=True, destructive=True))

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


# --- customer money ---------------------------------------------------------------

PAYMENT_ID = {'type': 'integer', 'description': 'From find_payments'}
PAID_VIA = {'type': 'string', 'enum': ['cash', 'whish'],
            'description': 'Which account the money came from (default cash)'}


def _put_paid_via(args, body):
    pv = enum_arg(args, 'paid_via', ('cash', 'whish'), None)
    if pv is not None:
        body['paid_via'] = pv


def _pid(args):
    return int_arg(args, 'payment_id', lo=1, required=True)


def receive_payment(ctx, args):
    cid = _cid(args)
    finance = any(r in ('admin', 'finance') for r in ctx.roles)
    body = {'amount': amount_arg(args, 'amount'),
            'method': enum_arg(args, 'method', ('cash', 'whish_transfer'), 'cash'),
            'action': enum_arg(args, 'action', ('pay', 'collect'), 'pay' if finance else 'collect')}
    ref = str_arg(args, 'reference', max_len=64)
    if ref:
        body['reference'] = ref
    resp = call_api(ctx, f'/api/customers/{cid}/receive-payment', method='POST', json=body)
    return _done(resp, customer=_customer_after(ctx, cid))


def confirm_collected(ctx, args):
    cid = _cid(args)
    resp = call_api(ctx, f'/api/customers/{cid}/confirm-collected', method='POST', json={})
    return _done(resp, customer=_customer_after(ctx, cid))


def revert_payment(ctx, args):
    pid = _pid(args)
    resp = call_api(ctx, f'/api/payments/{pid}/revert', method='PUT', json={'reason': _reason(args)})
    return _done(resp, payment_id=pid)


def refund_payment(ctx, args):
    pid = _pid(args)
    body = {'amount': amount_arg(args, 'amount'), 'reason': _reason(args)}
    _put_paid_via(args, body)
    resp = call_api(ctx, f'/api/payments/{pid}/refund', method='POST', json=body)
    return _done(resp, payment_id=pid)


def mark_payment_gratis(ctx, args):
    pid = _pid(args)
    resp = call_api(ctx, f'/api/payments/{pid}/mark_gratis', method='PUT',
                    json={'note': str_arg(args, 'note', max_len=200)})
    return _done(resp, payment_id=pid)


register_tool(Tool(
    name='receive_payment', title='Receive payment',
    description=("Take money from a customer against what they owe (oldest bills first; extra becomes credit). "
                 "action 'pay' (admin/finance, default for them) confirms it as received; 'collect' (collector/"
                 "cashier, their default) records it as collected, awaiting confirmation. Confirm the amount with "
                 "the user first."),
    input_schema={'type': 'object', 'required': ['customer_id', 'amount'], 'properties': {
        'customer_id': CUSTOMER_ID, 'amount': {'type': 'number', 'exclusiveMinimum': 0},
        'method': {'type': 'string', 'enum': ['cash', 'whish_transfer'], 'default': 'cash'},
        'action': {'type': 'string', 'enum': ['pay', 'collect']},
        'reference': {'type': 'string', 'description': 'Transfer reference, optional'}}},
    handler=receive_payment, roles=COLLECT, writes=True, dedupe=True))

register_tool(Tool(
    name='confirm_collected', title='Confirm collected money',
    description="Confirm as received everything a collector collected from this customer that awaits confirmation.",
    input_schema={'type': 'object', 'required': ['customer_id'], 'properties': {'customer_id': CUSTOMER_ID}},
    handler=confirm_collected, roles=FINANCE, writes=True))

register_tool(Tool(
    name='revert_payment', title='Revert payment',
    description='Undo a payment that was marked paid by mistake (the bill becomes unpaid again). Find the id with find_payments.',
    input_schema={'type': 'object', 'required': ['payment_id', 'reason'],
                  'properties': {'payment_id': PAYMENT_ID, 'reason': REASON}},
    handler=revert_payment, roles=FINANCE, writes=True, destructive=True))

register_tool(Tool(
    name='refund_payment', title='Refund payment',
    description=('Give money back on a paid payment (full or part, not more than the payment). Find the id with '
                 'find_payments. paid_via whish if the refund was sent from the Whish account; by default it '
                 'follows how the customer paid.'),
    input_schema={'type': 'object', 'required': ['payment_id', 'amount', 'reason'], 'properties': {
        'payment_id': PAYMENT_ID, 'amount': {'type': 'number', 'exclusiveMinimum': 0}, 'reason': REASON,
        'paid_via': PAID_VIA}},
    handler=refund_payment, roles=FINANCE, writes=True, destructive=True, dedupe=True))

register_tool(Tool(
    name='mark_payment_gratis', title='Mark bill free',
    description='Mark an unpaid bill as free of charge (gratis), e.g. compensation for an outage.',
    input_schema={'type': 'object', 'required': ['payment_id'], 'properties': {
        'payment_id': PAYMENT_ID, 'note': {'type': 'string'}}},
    handler=mark_payment_gratis, roles=FINANCE, writes=True))


# --- expenses, cash, suppliers ----------------------------------------------------

ACCOUNT = {'type': 'string', 'enum': ['cash', 'whish']}
DATE = {'type': 'string', 'description': 'YYYY-MM-DD; default today'}
MONEY = {'type': 'number', 'exclusiveMinimum': 0}


def _id(args, key):
    return int_arg(args, key, lo=1, required=True)


def _expense_fields(ctx, args, creating):
    body = {}
    amount = amount_arg(args, 'amount', required=creating)
    if amount is not None:
        body['amount'] = amount
    category = str_arg(args, 'category', required=creating)
    if category:
        body['category'] = category
    d = date_arg(args, 'date', default=today(ctx) if creating else None)
    if d is not None:
        body['date'] = day(d)
    desc = str_arg(args, 'description', max_len=500)
    if desc is not None:
        body['description'] = desc
    for key in ('supplier_id', 'employee_id'):
        v = int_arg(args, key, lo=1)
        if v is not None:
            body[key] = v
    if 'is_credit' in args:
        body['is_credit'] = bool_arg(args, 'is_credit')
    _put_paid_via(args, body)
    return body


def add_expense(ctx, args):
    resp = call_api(ctx, '/api/expenses', method='POST', json=_expense_fields(ctx, args, True))
    return {'result': 'Expense added.', 'expense': resp}


def update_expense(ctx, args):
    eid = _id(args, 'expense_id')
    body = _expense_fields(ctx, args, False)
    if not body:
        raise ToolError('Nothing to change: pass at least one field.')
    resp = call_api(ctx, f'/api/expenses/{eid}', method='PUT', json=body)
    return {'result': 'Expense updated.', 'expense': resp}


def delete_expense(ctx, args):
    eid = _id(args, 'expense_id')
    _reason(args)
    return _done(call_api(ctx, f'/api/expenses/{eid}', method='DELETE'), expense_id=eid)


def add_cash_entry(ctx, args):
    body = {'account': enum_arg(args, 'account', ('cash', 'whish'), None),
            'amount': amount_arg(args, 'amount'), 'reason': _reason(args)}
    if body['account'] is None:
        raise ToolError("'account' is required.")
    d = date_arg(args, 'date')
    if d is not None:
        body['date'] = day(d)
    return {'result': 'Cash entry added.', 'entry': call_api(ctx, '/api/cash-entries', method='POST', json=body)}


def delete_cash_entry(ctx, args):
    eid = _id(args, 'entry_id')
    _reason(args)
    return _done(call_api(ctx, f'/api/cash-entries/{eid}', method='DELETE'), entry_id=eid)


def transfer_between_accounts(ctx, args):
    body = {'from_account': enum_arg(args, 'from_account', ('cash', 'whish'), None),
            'to_account': enum_arg(args, 'to_account', ('cash', 'whish'), None),
            'amount': amount_arg(args, 'amount')}
    if body['from_account'] is None or body['to_account'] is None:
        raise ToolError("'from_account' and 'to_account' are required.")
    note = str_arg(args, 'note', max_len=200)
    if note:
        body['note'] = note
    d = date_arg(args, 'date')
    if d is not None:
        body['date'] = day(d)
    return {'result': 'Transfer recorded.',
            'transfer': call_api(ctx, '/api/account-transfers', method='POST', json=body)}


def delete_transfer(ctx, args):
    tid = _id(args, 'transfer_id')
    _reason(args)
    return _done(call_api(ctx, f'/api/account-transfers/{tid}', method='DELETE'), transfer_id=tid)


def pay_supplier(ctx, args):
    sid = _id(args, 'supplier_id')
    body = {'amount': amount_arg(args, 'amount'),
            'payment_method': str_arg(args, 'payment_method', max_len=50) or '',
            'reference_note': str_arg(args, 'reference_note', max_len=200) or ''}
    d = date_arg(args, 'payment_date')
    if d is not None:
        body['payment_date'] = day(d)
    _put_paid_via(args, body)
    resp = call_api(ctx, f'/api/suppliers/{sid}/payments', method='POST', json=body)
    return _done(resp, supplier=resp.get('supplier'), payment=resp.get('payment'))


def delete_supplier_payment(ctx, args):
    sid, pid = _id(args, 'supplier_id'), _id(args, 'payment_id')
    _reason(args)
    return _done(call_api(ctx, f'/api/suppliers/{sid}/payments/{pid}', method='DELETE'), payment_id=pid)


_EXPENSE_PROPS = {'amount': MONEY, 'category': {'type': 'string', 'description': 'From list_expense_categories'},
                  'date': DATE, 'description': {'type': 'string'},
                  'supplier_id': {'type': 'integer'}, 'employee_id': {'type': 'integer'},
                  'is_credit': {'type': 'boolean', 'description': 'Bought on credit from the supplier (not paid yet)'},
                  'paid_via': PAID_VIA}

register_tool(Tool(
    name='add_expense', title='Add expense',
    description=('Record a business expense in a category (see list_expense_categories). Counts in cash flow and '
                 'profit. paid_via whish if it was paid from the Whish account (default cash).'),
    input_schema={'type': 'object', 'required': ['amount', 'category'], 'properties': _EXPENSE_PROPS},
    handler=add_expense, roles=FINANCE, writes=True, dedupe=True))

register_tool(Tool(
    name='update_expense', title='Edit expense',
    description='Change an expense; only the fields you pass change.',
    input_schema={'type': 'object', 'required': ['expense_id'],
                  'properties': {'expense_id': {'type': 'integer'}, **_EXPENSE_PROPS}},
    handler=update_expense, roles=FINANCE, writes=True))

register_tool(Tool(
    name='delete_expense', title='Delete expense',
    description='Delete an expense recorded by mistake.',
    input_schema={'type': 'object', 'required': ['expense_id', 'reason'],
                  'properties': {'expense_id': {'type': 'integer'}, 'reason': REASON}},
    handler=delete_expense, roles=FINANCE, writes=True, destructive=True))

register_tool(Tool(
    name='add_cash_entry', title='Add cash-in',
    description=('Record money received that is not a customer or reseller payment, into the cash box or the '
                 'Whish account. Feeds the Daily Cash flow.'),
    input_schema={'type': 'object', 'required': ['account', 'amount', 'reason'], 'properties': {
        'account': ACCOUNT, 'amount': MONEY, 'date': DATE,
        'reason': {'type': 'string', 'description': 'What the money is'}}},
    handler=add_cash_entry, roles=FINANCE, writes=True, dedupe=True))

register_tool(Tool(
    name='delete_cash_entry', title='Delete cash-in',
    description='Delete a cash-in entry recorded by mistake (admin only).',
    input_schema={'type': 'object', 'required': ['entry_id', 'reason'],
                  'properties': {'entry_id': {'type': 'integer'}, 'reason': REASON}},
    handler=delete_cash_entry, roles=ADMIN, writes=True, destructive=True))

register_tool(Tool(
    name='transfer_between_accounts', title='Move money between accounts',
    description='Record moving money between the cash box and the Whish account.',
    input_schema={'type': 'object', 'required': ['from_account', 'to_account', 'amount'], 'properties': {
        'from_account': ACCOUNT, 'to_account': ACCOUNT, 'amount': MONEY, 'date': DATE,
        'note': {'type': 'string'}}},
    handler=transfer_between_accounts, roles=FINANCE, writes=True, dedupe=True))

register_tool(Tool(
    name='delete_transfer', title='Delete account transfer',
    description='Delete an account transfer recorded by mistake (admin only).',
    input_schema={'type': 'object', 'required': ['transfer_id', 'reason'],
                  'properties': {'transfer_id': {'type': 'integer'}, 'reason': REASON}},
    handler=delete_transfer, roles=ADMIN, writes=True, destructive=True))

register_tool(Tool(
    name='pay_supplier', title='Pay supplier',
    description=('Record a payment to a supplier (reduces what the business owes them). paid_via whish if paid '
                 'from the Whish account (default cash).'),
    input_schema={'type': 'object', 'required': ['supplier_id', 'amount'], 'properties': {
        'supplier_id': {'type': 'integer'}, 'amount': MONEY, 'payment_date': DATE,
        'payment_method': {'type': 'string'}, 'reference_note': {'type': 'string'}, 'paid_via': PAID_VIA}},
    handler=pay_supplier, roles=FINANCE, writes=True, dedupe=True))

register_tool(Tool(
    name='delete_supplier_payment', title='Delete supplier payment',
    description='Delete a supplier payment recorded by mistake (admin only).',
    input_schema={'type': 'object', 'required': ['supplier_id', 'payment_id', 'reason'], 'properties': {
        'supplier_id': {'type': 'integer'}, 'payment_id': {'type': 'integer'}, 'reason': REASON}},
    handler=delete_supplier_payment, roles=ADMIN, writes=True, destructive=True))


# --- payroll (admin only) ------------------------------------------------------------

def add_employee_charge(ctx, args):
    emp = _id(args, 'employee_id')
    body = {'type': enum_arg(args, 'type', ('salary', 'bonus', 'deduction'), None),
            'amount': amount_arg(args, 'amount'), 'reason': str_arg(args, 'reason', max_len=200) or ''}
    if body['type'] is None:
        raise ToolError("'type' is required.")
    period = str_arg(args, 'period', max_len=7)
    if period:
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
            raise ToolError("'period' must look like 2026-11.")
        body['period'] = period
    d = date_arg(args, 'date')
    if d is not None:
        body['date'] = day(d)
    resp = call_api(ctx, f'/api/employees/{emp}/charges', method='POST', json=body)
    return {'result': f"{body['type'].capitalize()} added.", 'employee': resp.get('employee'),
            'charge': resp.get('charge')}


def pay_employee(ctx, args):
    emp = _id(args, 'employee_id')
    body = {'amount': amount_arg(args, 'amount'), 'is_advance': bool_arg(args, 'is_advance'),
            'method': str_arg(args, 'method', max_len=50) or '', 'note': str_arg(args, 'note', max_len=200) or ''}
    d = date_arg(args, 'payment_date')
    if d is not None:
        body['payment_date'] = day(d)
    _put_paid_via(args, body)
    resp = call_api(ctx, f'/api/employees/{emp}/payments', method='POST', json=body)
    return _done(resp, employee=resp.get('employee'), payment=resp.get('payment'))


register_tool(Tool(
    name='add_employee_charge', title='Add salary / bonus / deduction',
    description=("Add what an employee is owed for a period: 'salary' or 'bonus' increase it, 'deduction' "
                 'reduces it. Pay it with pay_employee.'),
    input_schema={'type': 'object', 'required': ['employee_id', 'type', 'amount'], 'properties': {
        'employee_id': {'type': 'integer'}, 'type': {'type': 'string', 'enum': ['salary', 'bonus', 'deduction']},
        'amount': MONEY, 'period': {'type': 'string', 'description': 'YYYY-MM; default the month of date'},
        'date': DATE, 'reason': {'type': 'string'}}},
    handler=add_employee_charge, roles=ADMIN, writes=True, dedupe=True))

register_tool(Tool(
    name='pay_employee', title='Pay employee',
    description=('Pay an employee their salary, or an advance (is_advance true). Recorded as an expense. '
                 'paid_via whish if paid from the Whish account (default cash).'),
    input_schema={'type': 'object', 'required': ['employee_id', 'amount'], 'properties': {
        'employee_id': {'type': 'integer'}, 'amount': MONEY, 'is_advance': {'type': 'boolean', 'default': False},
        'method': {'type': 'string'}, 'payment_date': DATE, 'note': {'type': 'string'},
        'paid_via': PAID_VIA}},
    handler=pay_employee, roles=ADMIN, writes=True, dedupe=True))


# --- resellers ------------------------------------------------------------------------

def _reseller_money(suffix, default_desc, takes_paid_via=False):
    def handler(ctx, args):
        rid = _id(args, 'reseller_id')
        body = {'amount': amount_arg(args, 'amount'),
                'description': str_arg(args, 'description', max_len=200) or default_desc}
        if takes_paid_via:
            _put_paid_via(args, body)
        resp = call_api(ctx, f'/api/resellers/{rid}/{suffix}', method='POST', json=body)
        return _done(resp, reseller=resp.get('reseller'))
    return handler


_RESELLER_PROPS = {'reseller_id': {'type': 'integer', 'description': 'From list_resellers'},
                   'amount': MONEY, 'description': {'type': 'string'}}

register_tool(Tool(
    name='reseller_add_credit', title='Add reseller credit',
    description="Add credit to a reseller's account.",
    input_schema={'type': 'object', 'required': ['reseller_id', 'amount'], 'properties': _RESELLER_PROPS},
    handler=_reseller_money('add_credit', 'Manual credit addition'), roles=FINANCE, writes=True, dedupe=True))

register_tool(Tool(
    name='reseller_collect_payment', title='Collect from reseller',
    description=('Record money received from a reseller. paid_via whish if it arrived in the Whish account '
                 '(default cash).'),
    input_schema={'type': 'object', 'required': ['reseller_id', 'amount'],
                  'properties': {**_RESELLER_PROPS, 'paid_via': PAID_VIA}},
    handler=_reseller_money('collect_payment', 'Payment received', takes_paid_via=True), roles=FINANCE, writes=True, dedupe=True))


# --- tickets -------------------------------------------------------------------------

STATUSES = ('open', 'in_progress', 'resolved', 'closed')
PRIORITIES = ('low', 'medium', 'high', 'critical')


def _ticket_after(ctx, tid):
    t = scoped(ctx, ctx.appmod.SupportTicket).filter_by(id=tid).first()
    return {'id': t.id, 'title': t.title, 'status': t.status, 'priority': t.priority,
            'customer_id': t.customer_id} if t else None


def create_ticket(ctx, args):
    cid = _cid(args)
    if not scoped(ctx, ctx.appmod.Customer).filter_by(id=cid).first():
        raise ToolError(f'Customer {cid} not found.')
    body = {'customer_id': cid, 'title': str_arg(args, 'title', required=True, max_len=200),
            'description': str_arg(args, 'description', max_len=2000) or '',
            'priority': enum_arg(args, 'priority', PRIORITIES, 'medium')}
    resp = call_api(ctx, '/api/support-tickets', method='POST', json=body)
    return {'result': 'Ticket opened.', 'ticket': _ticket_after(ctx, resp['id'])}


def update_ticket(ctx, args):
    tid = int_arg(args, 'ticket_id', lo=1, required=True)
    body = {}
    if args.get('status') is not None:
        body['status'] = enum_arg(args, 'status', STATUSES, None)
    if args.get('priority') is not None:
        body['priority'] = enum_arg(args, 'priority', PRIORITIES, None)
    for key, n in (('title', 200), ('description', 2000)):
        v = str_arg(args, key, max_len=n)
        if v is not None:
            body[key] = v
    if not body:
        raise ToolError('Nothing to change: pass status, priority, title or description.')
    call_api(ctx, f'/api/support-tickets/{tid}', method='PUT', json=body)
    return {'result': 'Ticket updated.', 'ticket': _ticket_after(ctx, tid)}


register_tool(Tool(
    name='create_ticket', title='Open support ticket',
    description='Open a support ticket for a customer (staff get the usual push notification).',
    input_schema={'type': 'object', 'required': ['customer_id', 'title'], 'properties': {
        'customer_id': CUSTOMER_ID, 'title': {'type': 'string'}, 'description': {'type': 'string'},
        'priority': {'type': 'string', 'enum': list(PRIORITIES), 'default': 'medium'}}},
    handler=create_ticket, roles=ALL_STAFF, writes=True, dedupe=True))

register_tool(Tool(
    name='update_ticket', title='Update support ticket',
    description='Change a ticket\'s status, priority, title or description (find ids with list_tickets).',
    input_schema={'type': 'object', 'required': ['ticket_id'], 'properties': {
        'ticket_id': {'type': 'integer'}, 'status': {'type': 'string', 'enum': list(STATUSES)},
        'priority': {'type': 'string', 'enum': list(PRIORITIES)},
        'title': {'type': 'string'}, 'description': {'type': 'string'}}},
    handler=update_ticket, roles=ALL_STAFF, writes=True))
