"""Read-only tools for the MCP connector -- see
docs/superpowers/specs/2026-10-21-mcp-connector-design.md. Each tool is
registered with mcp_server at import time (app.py imports this module).

Two ways a tool reads data:
- Simple per-row reads (customers, tickets, alerts) query the models directly,
  always through scoped(ctx, Model) so the grant's tenant is the only one
  visible.
- Anything with business logic (payments, cash flow, financial reports,
  network tree, resellers, upstreams) calls the app's own GET endpoint
  in-process as the signed-in user via call_api(), so the numbers and the
  endpoint's own role/module checks are exactly what the web app shows."""
import re
from datetime import datetime, time as dtime, timedelta, timezone

from flask_jwt_extended import create_access_token

from cs_agent_tools import normalize_lebanese_phone
from mcp_server import ALL_STAFF, FINANCE, PAYMENTS, Tool, ToolError, register_tool

MAX_ROWS = 50


# --- shared helpers ---------------------------------------------------------

def scoped(ctx, model):
    return model.query.filter_by(tenant_id=ctx.tenant.id)


def zone(ctx):
    am = ctx.appmod
    return am._zone_for(scoped(ctx, am.BusinessSettings).first())


def as_of(ctx):
    return datetime.now(zone(ctx)).strftime('%Y-%m-%d %H:%M %Z')


def today(ctx):
    return datetime.now(zone(ctx)).date()


def local(ctx, dt_utc):
    """Naive-UTC DB timestamp -> 'YYYY-MM-DD HH:MM' in the business timezone."""
    if dt_utc is None:
        return None
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(zone(ctx)).strftime('%Y-%m-%d %H:%M')


def day(d):
    return d.strftime('%Y-%m-%d') if d else None


def str_arg(args, key, required=False, max_len=100):
    v = args.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ToolError(f"'{key}' is required.")
        return None
    if not isinstance(v, str):
        raise ToolError(f"'{key}' must be text.")
    return v.strip()[:max_len]


def int_arg(args, key, default=None, lo=None, hi=None, required=False):
    v = args.get(key)
    if v is None or v == '':
        if required:
            raise ToolError(f"'{key}' is required.")
        return default
    if isinstance(v, bool):
        raise ToolError(f"'{key}' must be a whole number.")
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ToolError(f"'{key}' must be a whole number.")
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise ToolError(f"'{key}' must be between {lo} and {hi}.")
    return n


def bool_arg(args, key, default=False):
    v = args.get(key, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ('true', 'false'):
        return v.lower() == 'true'
    raise ToolError(f"'{key}' must be true or false.")


def date_arg(args, key, default=None):
    v = args.get(key)
    if v in (None, ''):
        return default
    try:
        return datetime.strptime(str(v), '%Y-%m-%d').date()
    except ValueError:
        raise ToolError(f"'{key}' must be a date like 2026-10-21.")


def enum_arg(args, key, choices, default):
    v = args.get(key, default)
    if v not in choices:
        raise ToolError(f"'{key}' must be one of: {', '.join(choices)}.")
    return v


def call_api(ctx, path, params=None):
    """GET one of this app's own JSON endpoints in-process as ctx.user.

    A short-lived JWT with the same claims /api/login issues (role and tenant
    re-read from the DB on this request) is attached, and the full Flask
    pipeline runs -- before_request hooks, the route's own @jwt_required and
    role/module decorators -- so the result is exactly what that user would
    see in the web app. test_request_context reuses the current app context,
    so the DB session is shared and nothing is committed by the call."""
    app = ctx.appmod.app
    token = create_access_token(identity=ctx.user.username,
                                additional_claims={'role': ctx.user.role, 'tenant_id': ctx.user.tenant_id},
                                expires_delta=timedelta(minutes=5))
    query = {k: v for k, v in (params or {}).items() if v is not None}
    with app.test_request_context(path, method='GET', query_string=query,
                                  headers={'Authorization': f'Bearer {token}'}):
        resp = app.full_dispatch_request()
    data = resp.get_json(silent=True)
    if resp.status_code >= 400:
        msg = None
        if isinstance(data, dict):
            msg = data.get('msg') or data.get('error') or data.get('message')
        if resp.status_code == 403:
            raise ToolError(msg or "You don't have access to this in ServiceBills.")
        raise ToolError(f"ServiceBills couldn't load this ({resp.status_code}){': ' + msg if msg else ''}.")
    return data


def _is_reverted(p):
    """Python twin of app._payment_not_reverted(): reverted_at stays set as an
    audit trail, so a re-collection stamped after it means 'not reverted'."""
    if p.reverted_at is None:
        return False
    later = p.collected_at or p.paid_at
    return later is None or p.reverted_at >= later


# --- customers ----------------------------------------------------------------

MAC_RE = re.compile(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$')


def _customer_row(ctx, c, today_):
    exp = c.subscription_expiry_date
    bal = float(c.balance or 0.0)
    return {
        'id': c.id, 'name': c.name, 'phone': c.phone, 'address': c.address, 'sector': c.sector,
        'plan': c.subscription_plan.name if c.subscription_plan else None,
        'active': bool(c.is_subscription_active),
        'expiry_date': day(exp),
        'days_to_expiry': (exp.date() - today_).days if exp else None,
        'balance': round(bal, 2),
        'owes': round(-bal, 2) if bal < 0 else 0.0,
        'upstream': c.upstream_provider.name if c.upstream_provider else None,
        'upstream_status': c.upstream_last_status,
    }


def search_customers(ctx, args):
    query = str_arg(args, 'query', required=True)
    limit = int_arg(args, 'limit', default=20, lo=1, hi=MAX_ROWS)
    am = ctx.appmod
    C = am.Customer
    base = scoped(ctx, C)
    found, seen = [], set()

    def add(rows):
        for c in rows:
            if c.id not in seen and len(found) < limit:
                seen.add(c.id)
                found.append(c)

    digits = re.sub(r'\D', '', query)
    if len(digits) >= 6:
        add(base.filter(C.phone.in_(normalize_lebanese_phone(query))).all())
        if len(digits) >= 7:
            add(base.filter(C.phone.like(f'%{digits[-7:]}%')).limit(limit).all())
    mac = am._canonical_mac(query)
    if mac and MAC_RE.match(mac):
        candidates = base.filter(am.db.or_(C.cpe_mac_address.isnot(None), C.onu_mac_address.isnot(None))).all()
        add([c for c in candidates
             if mac in (am._normalize_mac(c.cpe_mac_address), am._normalize_mac(c.onu_mac_address))])
    like = f'%{query}%'
    add(base.filter(am.db.or_(C.name.ilike(like), C.upstream_username.ilike(like),
                              C.pppoe_username.ilike(like)))
        .order_by(C.name).limit(limit).all())
    t = today(ctx)
    out = {'as_of': as_of(ctx), 'count': len(found), 'customers': [_customer_row(ctx, c, t) for c in found]}
    if not found:
        out['hint'] = 'No customer matched. Try part of the name, the phone number, or the PPPoE/upstream username.'
    return out


def _payment_row(ctx, p):
    return {'id': p.id, 'billed_date': day(p.date), 'amount': float(p.amount or 0), 'currency': p.currency,
            'paid': bool(p.paid), 'paid_at': local(ctx, p.paid_at),
            'collected': bool(p.collected), 'collected_at': local(ctx, p.collected_at),
            'is_refund': bool(p.is_refund), 'is_gratis': bool(p.is_gratis), 'reverted': _is_reverted(p),
            'reason': p.reason}


def _ticket_row(ctx, t):
    return {'id': t.id, 'title': t.title, 'status': t.status, 'priority': t.priority,
            'customer_id': t.customer_id, 'created_at': local(ctx, t.created_at),
            'description': (t.description or '')[:300]}


def get_customer(ctx, args):
    cid = int_arg(args, 'customer_id', required=True, lo=1)
    am = ctx.appmod
    c = scoped(ctx, am.Customer).filter_by(id=cid).first()
    if c is None:
        return {'as_of': as_of(ctx), 'found': False,
                'hint': 'No customer with that id in this business. Use search_customers to find the id.'}
    P, T = am.Payment, am.SupportTicket
    payments = scoped(ctx, P).filter_by(customer_id=c.id).order_by(P.date.desc(), P.id.desc()).limit(6).all()
    tickets = scoped(ctx, T).filter(T.customer_id == c.id, T.status.in_(('open', 'in_progress'))) \
        .order_by(T.created_at.desc()).limit(10).all()
    row = _customer_row(ctx, c, today(ctx))
    row.update({
        'start_date': day(c.subscription_start_date), 'notes': c.notes,
        'reseller': c.reseller.name if c.reseller else None,
        'upstream_username': c.upstream_username, 'upstream_expiry': day(c.upstream_actual_expiry),
        'upstream_synced_at': local(ctx, c.upstream_last_synced_at),
        'pppoe_username': c.pppoe_username, 'onu_mac': c.onu_mac_address, 'cpe_mac': c.cpe_mac_address,
        'onu_last_seen_at': local(ctx, c.onu_last_seen_at),
    })
    return {'as_of': as_of(ctx), 'found': True, 'customer': row,
            'recent_payments': [_payment_row(ctx, p) for p in payments],
            'open_tickets': [_ticket_row(ctx, t) for t in tickets]}


def list_customers(ctx, args):
    am = ctx.appmod
    C = am.Customer
    status = enum_arg(args, 'status', ('all', 'active', 'inactive'), 'all')
    expiring = int_arg(args, 'expiring_within_days', lo=0, hi=90)
    expired = int_arg(args, 'expired_within_days', lo=1, hi=365)
    owing = bool_arg(args, 'owing', False)
    sector = str_arg(args, 'sector')
    plan = str_arg(args, 'plan')
    upstream = str_arg(args, 'upstream')
    limit = int_arg(args, 'limit', default=MAX_ROWS, lo=1, hi=MAX_ROWS)

    q = scoped(ctx, C)
    if status == 'active':
        q = q.filter(C.is_subscription_active.is_(True))
    elif status == 'inactive':
        q = q.filter(C.is_subscription_active.is_(False))
    t = today(ctx)
    start_of_today = datetime.combine(t, dtime.min)
    if expiring is not None:
        q = q.filter(C.subscription_expiry_date >= start_of_today,
                     C.subscription_expiry_date < start_of_today + timedelta(days=expiring + 1))
    if expired is not None:
        q = q.filter(C.subscription_expiry_date >= start_of_today - timedelta(days=expired),
                     C.subscription_expiry_date < start_of_today)
    if owing:
        q = q.filter(C.balance < 0)
    if sector:
        q = q.filter(C.sector.ilike(sector))
    if plan:
        q = q.join(am.SubscriptionPlan, C.subscription_plan_id == am.SubscriptionPlan.id) \
            .filter(am.SubscriptionPlan.name.ilike(plan))
    if upstream:
        q = q.join(am.UpstreamProvider, C.upstream_provider_id == am.UpstreamProvider.id) \
            .filter(am.UpstreamProvider.name.ilike(upstream))
    total = q.count()
    rows = q.order_by(C.subscription_expiry_date, C.id).limit(limit).all()
    out = {'as_of': as_of(ctx), 'total': total, 'returned': len(rows),
           'customers': [_customer_row(ctx, c, t) for c in rows]}
    if owing:
        out['total_owed'] = round(-float(q.with_entities(am.db.func.coalesce(am.db.func.sum(C.balance), 0)).scalar()), 2)
    if total > len(rows):
        out['note'] = f'Showing {len(rows)} of {total}. Narrow the filter to see the rest.'
    return out


register_tool(Tool(
    name='search_customers', title='Search customers',
    description=('Find customers by part of their name, phone number (any Lebanese format), '
                 'PPPoE/upstream username, or router/ONU MAC address. Returns ids for get_customer.'),
    input_schema={'type': 'object', 'required': ['query'], 'properties': {
        'query': {'type': 'string', 'description': 'Name, phone, username or MAC'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS, 'default': 20}}},
    handler=search_customers, roles=ALL_STAFF))

register_tool(Tool(
    name='get_customer', title='Customer details',
    description=('Full details for one customer: plan, status, expiry, balance (negative = owes), '
                 'upstream and network identifiers, last 6 bills/payments and open support tickets.'),
    input_schema={'type': 'object', 'required': ['customer_id'], 'properties': {
        'customer_id': {'type': 'integer', 'description': 'From search_customers or list_customers'}}},
    handler=get_customer, roles=ALL_STAFF))

register_tool(Tool(
    name='list_customers', title='List customers',
    description=('List customers with filters: active/inactive, expiring within N days, expired in the '
                 'last N days, owing money, sector, plan name or upstream name. Sorted by expiry date.'),
    input_schema={'type': 'object', 'properties': {
        'status': {'type': 'string', 'enum': ['all', 'active', 'inactive'], 'default': 'all'},
        'expiring_within_days': {'type': 'integer', 'minimum': 0, 'maximum': 90},
        'expired_within_days': {'type': 'integer', 'minimum': 1, 'maximum': 365},
        'owing': {'type': 'boolean', 'description': 'Only customers with a negative balance'},
        'sector': {'type': 'string'}, 'plan': {'type': 'string'}, 'upstream': {'type': 'string'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS, 'default': MAX_ROWS}}},
    handler=list_customers, roles=ALL_STAFF))


# --- finance (via the app's own report endpoints) -----------------------------

_PAYMENT_KEYS = ('id', 'customer_id', 'customer_name', 'date', 'amount', 'currency', 'paid', 'paid_at',
                 'collected', 'collected_at', 'collected_by', 'collected_via', 'received_by',
                 'is_refund', 'is_gratis', 'reverted_at', 'reason')


def list_payments(ctx, args):
    limit = int_arg(args, 'limit', default=MAX_ROWS, lo=1, hi=MAX_ROWS)
    status = enum_arg(args, 'status', ('any', 'paid', 'unpaid'), 'any')
    params = {
        'start_date': day(date_arg(args, 'billed_from')),
        'end_date': day(date_arg(args, 'billed_to')),
        'paid_date_start': day(date_arg(args, 'paid_from')),
        'paid_date_end': day(date_arg(args, 'paid_to')),
        'customer_id': int_arg(args, 'customer_id', lo=1),
        'search_query': str_arg(args, 'customer_name'),
        'status': None if status == 'any' else status,
        'page': 1, 'per_page': limit,
    }
    data = call_api(ctx, '/api/payments', params)
    rows = [{k: p.get(k) for k in _PAYMENT_KEYS} for p in data.get('payments', [])]
    out = {'as_of': as_of(ctx), 'total': data.get('total'), 'returned': len(rows),
           'totals': data.get('totals'), 'payments': rows,
           'timestamps': 'paid_at / collected_at are UTC as stored; date is the billing date.'}
    if (data.get('total') or 0) > len(rows):
        out['note'] = f"Showing {len(rows)} of {data.get('total')}; totals cover all of them."
    return out


def get_cash_flow(ctx, args):
    the_day = date_arg(args, 'day')
    start, end = date_arg(args, 'start_date'), date_arg(args, 'end_date')
    if (start is None) != (end is None):
        raise ToolError("Give both 'start_date' and 'end_date', or neither.")
    if start is not None and the_day is not None:
        raise ToolError("Use either 'day' or a 'start_date'/'end_date' range, not both.")
    if start is None and the_day is None:
        the_day = today(ctx)
    params = {'start_date': day(start), 'end_date': day(end)} if start else {'day': day(the_day)}
    data = call_api(ctx, '/api/reports/daily-cash', params)
    groups = [{'collector': g.get('collector_name') or 'Office / Direct', 'is_office': g.get('is_office'),
               'total': g.get('total'), 'payments': g.get('payment_count')} for g in data.get('groups', [])]
    out = {'as_of': as_of(ctx), 'date_start': data.get('date_start'), 'date_end': data.get('date_end'),
           'currency': data.get('reporting_currency'), 'collected_total': data.get('grand_total'),
           'by_collector': groups}
    cf = data.get('cash_flow')
    if cf and not start:
        def tot(k):
            return (cf.get(k) or {}).get('total')
        out['cash_flow'] = {
            'day': cf.get('day'), 'closed': bool(cf.get('close')), 'locked_through': cf.get('locked_through'),
            'cash_start': cf.get('cash_start'), 'cash_in': tot('cash_in'), 'cash_out': tot('cash_out'),
            'cash_end': cf.get('cash_end'), 'whish_start': cf.get('whish_start'), 'whish_in': tot('whish_in'),
            'whish_out': tot('whish_out'), 'whish_end': cf.get('whish_end'),
            'total_in': cf.get('total_in'), 'total_out': cf.get('total_out'), 'total_net': cf.get('total_net'),
            'money_out_items': ((cf.get('cash_out') or {}).get('items') or [])[:20]
                               + ((cf.get('whish_out') or {}).get('items') or [])[:20],
        }
    return out


def _month_bounds(month):
    try:
        first = datetime.strptime(month, '%Y-%m').date()
    except ValueError:
        raise ToolError("'month' must look like 2026-10.")
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt - timedelta(days=1)


def get_financial_summary(ctx, args):
    month = str_arg(args, 'month', max_len=7)
    start, end = date_arg(args, 'start_date'), date_arg(args, 'end_date')
    if month and (start or end):
        raise ToolError("Use either 'month' or a 'start_date'/'end_date' range, not both.")
    if (start is None) != (end is None):
        raise ToolError("Give both 'start_date' and 'end_date', or neither.")
    if not start:
        start, end = _month_bounds(month or today(ctx).strftime('%Y-%m'))
    period = {'start_date': day(start), 'end_date': day(end)}
    report = call_api(ctx, '/api/reports/financial', period)
    dash = call_api(ctx, '/api/dashboard', period)
    return {'as_of': as_of(ctx), 'period': period, 'currency': report.get('currency'),
            'totals': report.get('totals'), 'by_month': report.get('monthly_data'),
            'snapshot': {'active_customers': dash.get('activeCustomers'),
                         'total_customers': dash.get('totalCustomers'),
                         'outstanding_balance': dash.get('outstandingBalance')},
            'note': 'income = money received; profit = income - expenses; estimated_* include '
                    'payroll estimates. Customer counts are as of now, not for the period.'}


register_tool(Tool(
    name='list_payments', title='List payments',
    description=('Bills and payments with filters (billing date range, paid date range, paid/unpaid, '
                 'customer id or name). Totals cover every match, converted to the reporting currency.'),
    input_schema={'type': 'object', 'properties': {
        'billed_from': {'type': 'string', 'format': 'date'}, 'billed_to': {'type': 'string', 'format': 'date'},
        'paid_from': {'type': 'string', 'format': 'date'}, 'paid_to': {'type': 'string', 'format': 'date'},
        'status': {'type': 'string', 'enum': ['any', 'paid', 'unpaid'], 'default': 'any'},
        'customer_id': {'type': 'integer'}, 'customer_name': {'type': 'string'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS, 'default': MAX_ROWS}}},
    handler=list_payments, roles=PAYMENTS))

register_tool(Tool(
    name='get_cash_flow', title='Daily cash flow',
    description=('Money collected per collector for a day (default today) or a date range, plus for a '
                 'single day the cash/Whish register: opening, in, out, closing and whether the day is closed.'),
    input_schema={'type': 'object', 'properties': {
        'day': {'type': 'string', 'format': 'date'},
        'start_date': {'type': 'string', 'format': 'date'}, 'end_date': {'type': 'string', 'format': 'date'}}},
    handler=get_cash_flow, roles=FINANCE))

register_tool(Tool(
    name='get_financial_summary', title='Financial summary',
    description=('Income, expenses (manual, supplier, payroll) and profit for a month (default this month) '
                 'or a date range, broken down by month, plus current customer counts and outstanding balance.'),
    input_schema={'type': 'object', 'properties': {
        'month': {'type': 'string', 'description': 'YYYY-MM'},
        'start_date': {'type': 'string', 'format': 'date'}, 'end_date': {'type': 'string', 'format': 'date'}}},
    handler=get_financial_summary, roles=FINANCE))


# --- tickets --------------------------------------------------------------------

def list_tickets(ctx, args):
    T = ctx.appmod.SupportTicket
    status = enum_arg(args, 'status', ('open_or_in_progress', 'open', 'in_progress', 'resolved', 'closed', 'all'),
                      'open_or_in_progress')
    customer_id = int_arg(args, 'customer_id', lo=1)
    limit = int_arg(args, 'limit', default=MAX_ROWS, lo=1, hi=MAX_ROWS)
    q = scoped(ctx, T)
    if status == 'open_or_in_progress':
        q = q.filter(T.status.in_(('open', 'in_progress')))
    elif status != 'all':
        q = q.filter(T.status == status)
    if customer_id:
        q = q.filter(T.customer_id == customer_id)
    total = q.count()
    rows = q.order_by(T.created_at.desc()).limit(limit).all()
    names = {c.id: c.name for c in scoped(ctx, ctx.appmod.Customer).filter(
        ctx.appmod.Customer.id.in_({t.customer_id for t in rows if t.customer_id})).all()} if rows else {}
    out = {'as_of': as_of(ctx), 'total': total, 'returned': len(rows),
           'tickets': [{**_ticket_row(ctx, t), 'customer_name': names.get(t.customer_id)} for t in rows]}
    if total > len(rows):
        out['note'] = f'Showing the newest {len(rows)} of {total}.'
    return out


# --- network --------------------------------------------------------------------

def _flatten_tree(nodes, parent, out):
    for n in nodes or []:
        out.append({'id': n.get('id'), 'name': n.get('name'), 'type': n.get('device_type'), 'parent': parent,
                    'status': n.get('status'), 'last_status': n.get('last_status'),
                    'last_checked_at_utc': n.get('last_checked_at'),
                    'last_result_operation': n.get('last_result_operation'),
                    'last_result_at': n.get('last_result_at')})
        _flatten_tree(n.get('children'), n.get('name'), out)


def get_network_status(ctx, args):
    am = ctx.appmod
    customer_id = int_arg(args, 'customer_id', lo=1)
    tree = call_api(ctx, '/api/network-tree').get('tree') or []
    devices = []
    _flatten_tree(tree, None, devices)
    A = am.NetworkAlertEvent
    alerts = scoped(ctx, A).order_by(A.created_at.desc()).limit(10).all()
    out = {'as_of': as_of(ctx), 'devices': devices,
           'recent_alerts': [{'kind': a.kind, 'message': a.message, 'at': local(ctx, a.created_at)} for a in alerts],
           'note': 'Last known results only; nothing was polled live for this answer.'}
    if customer_id:
        c = scoped(ctx, am.Customer).filter_by(id=customer_id).first()
        out['customer'] = None if c is None else {
            'id': c.id, 'name': c.name, 'upstream_status': c.upstream_last_status,
            'upstream_synced_at': local(ctx, c.upstream_last_synced_at),
            'onu_mac': c.onu_mac_address, 'onu_last_seen_at': local(ctx, c.onu_last_seen_at),
            'pppoe_username': c.pppoe_username}
    return out


# --- resellers & upstreams --------------------------------------------------------

def list_resellers(ctx, args):
    data = call_api(ctx, '/api/resellers') or []
    return {'as_of': as_of(ctx), 'resellers': [
        {'id': r.get('id'), 'name': r.get('name'), 'phone': r.get('phone'), 'type': r.get('type'),
         'balance': r.get('balance'), 'customer_count': len(r.get('customers') or [])} for r in data]}


def list_upstreams(ctx, args):
    data = call_api(ctx, '/api/upstream-providers') or []
    keep = ('id', 'name', 'product', 'balance', 'status')
    return {'as_of': as_of(ctx), 'upstreams': [{k: p.get(k) for k in keep} for p in data],
            'note': 'balance is what this business owes or has prepaid with the upstream provider.'}


register_tool(Tool(
    name='list_tickets', title='Support tickets',
    description='Support tickets, newest first. Default: open and in-progress. Filter by status or customer id.',
    input_schema={'type': 'object', 'properties': {
        'status': {'type': 'string', 'enum': ['open_or_in_progress', 'open', 'in_progress', 'resolved', 'closed', 'all'],
                   'default': 'open_or_in_progress'},
        'customer_id': {'type': 'integer'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS, 'default': MAX_ROWS}}},
    handler=list_tickets, roles=ALL_STAFF))

register_tool(Tool(
    name='get_network_status', title='Network status',
    description=('Last known status of every network device (router, OLT, ...) in the topology, recent '
                 'network alerts, and optionally one customer\'s ONU/upstream state. No live polling.'),
    input_schema={'type': 'object', 'properties': {'customer_id': {'type': 'integer'}}},
    handler=get_network_status, roles=ALL_STAFF, module='network'))

register_tool(Tool(
    name='list_resellers', title='Resellers',
    description='All resellers with their balance and number of customers.',
    input_schema={'type': 'object', 'properties': {}},
    handler=list_resellers, roles=FINANCE))

register_tool(Tool(
    name='list_upstreams', title='Upstream providers',
    description='Upstream RADIUS providers this business resells, with balance and status.',
    input_schema={'type': 'object', 'properties': {}},
    handler=list_upstreams, roles=FINANCE, module='upstream_sync'))
