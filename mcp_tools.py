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
