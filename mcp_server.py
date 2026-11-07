"""MCP (Model Context Protocol) endpoint for the AI connector: stateless
Streamable HTTP, one JSON response per POST, no SSE stream, no sessions. See
docs/superpowers/specs/2026-10-21-mcp-connector-design.md. Registered from
app.py via register_mcp_routes(app, appmod); tools register themselves from
mcp_tools.py.

Auth is an OAuth access token issued by mcp_oauth.py. Every request re-reads
the user, tenant and module from the DB, so a revoke, demotion, suspension or
module switch-off applies on the very next call."""
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from flask import jsonify, make_response, request

import mcp_oauth
import modules

SUPPORTED_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26')
SERVER_INFO = {'name': 'servicebills', 'title': 'ServiceBills', 'version': '1.0.0'}
INSTRUCTIONS = (
    "Read-only access to one ISP business's ServiceBills data: customers, "
    "subscriptions, payments, cash flow, finances, support tickets and network "
    "status. You cannot change anything. Customer balance: negative means the "
    "customer owes money, positive is credit. Dates and times are in the "
    "business's own timezone unless a field name ends in _utc. Find a customer's "
    "id with search_customers before calling get_customer.")

INSTRUCTIONS_WRITE = (
    "Access to one ISP business's ServiceBills data: customers, subscriptions, "
    "payments, cash flow, finances, support tickets and network status, plus "
    "write tools that change real data (customers, renewals, payments, expenses, "
    "cash, suppliers, payroll, resellers, tickets). Write tools change real data -- "
    "confirm amounts and the customer with the user before calling them. Customer "
    "balance: negative means the customer owes money, positive is credit. Dates and "
    "times are in the business's own timezone unless a field name ends in _utc. Find "
    "a customer's id with search_customers before calling get_customer.")
WRITES_OFF = ("Changes through the AI are turned off for this business. "
              "An admin can turn them on in Settings → AI connector.")
DEDUPE_WINDOW = timedelta(minutes=2)

ALL_STAFF = ('admin', 'finance', 'employee', 'collector', 'cashier')
FINANCE = ('admin', 'finance')
PAYMENTS = ('admin', 'finance', 'cashier')
DESK = ('admin', 'finance', 'cashier')          # subscription_desk_required
COLLECT = ('admin', 'finance', 'cashier', 'collector')
ADMIN = ('admin',)


class ToolError(Exception):
    """Raised by a tool handler with a sentence the AI can relay to the user."""


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    handler: Callable
    roles: tuple = ALL_STAFF
    module: Optional[str] = None
    writes: bool = False        # changes data; hidden/refused while the switch is off
    destructive: bool = False   # undo-style (revert/refund/cancel/delete)
    dedupe: bool = False        # refuse an identical successful call within DEDUPE_WINDOW


@dataclass
class McpContext:
    appmod: object
    tenant: object
    user: object
    grant: object
    roles: list


TOOLS = {}


def register_tool(tool):
    TOOLS[tool.name] = tool
    return tool


def roles_of(user):
    return [r.strip().lower() for r in (user.role or '').split(',') if r.strip()]


def _settings(ctx):
    return ctx.appmod.BusinessSettings.query.filter_by(tenant_id=ctx.tenant.id).first()


def writes_enabled(ctx):
    """Re-read on every request so the admin toggle applies on the next call."""
    s = _settings(ctx)
    return bool(s and s.ai_connector_writes_enabled)


def _has_role(ctx, tool):
    return any(r in tool.roles for r in ctx.roles)


def _visible(ctx, tool, writes_on):
    if tool.module and not modules.is_enabled(ctx.tenant, tool.module):
        return False
    if tool.writes:
        return writes_on and _has_role(ctx, tool)
    return True


def _result(msg_id, result):
    return {'jsonrpc': '2.0', 'id': msg_id, 'result': result}


def _error(msg_id, code, message):
    return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': code, 'message': message}}


def _tool_error(text):
    return {'content': [{'type': 'text', 'text': text}], 'isError': True}


_CONFIRM_DUPLICATE = {'type': 'boolean',
                      'description': ('Only after this tool said the same change was just made AND the user '
                                      'confirmed they want it twice.')}


def _describe(tool):
    schema = tool.input_schema
    if tool.dedupe:
        schema = {**schema, 'properties': {**schema.get('properties', {}),
                                           'confirm_duplicate': _CONFIRM_DUPLICATE}}
    annotations = {'title': tool.title, 'readOnlyHint': not tool.writes,
                   'destructiveHint': tool.destructive, 'openWorldHint': False}
    if tool.writes:
        annotations['idempotentHint'] = False
    return {'name': tool.name, 'title': tool.title, 'description': tool.description,
            'inputSchema': schema, 'annotations': annotations}


def _unauthorized(description='Missing or invalid access token'):
    meta = mcp_oauth.origin() + '/.well-known/oauth-protected-resource'
    resp = make_response(jsonify(error='invalid_token', error_description=description), 401)
    challenge = f'Bearer resource_metadata="{meta}"'
    if request.headers.get('Authorization'):
        # RFC 6750 3.1: an error code only when credentials were presented
        # and rejected; a request with none just gets the bare challenge.
        challenge += ', error="invalid_token"'
    resp.headers['WWW-Authenticate'] = challenge
    return resp


def _forbidden(description):
    return make_response(jsonify(error='forbidden', error_description=description), 403)


def resolve_context(appmod):
    """(McpContext, None) for a valid bearer token, else (None, error response)."""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None, _unauthorized()
    token = auth[7:].strip()
    grant = appmod.McpGrant.query.filter_by(access_token_hash=mcp_oauth.hash_token(token)).first() if token else None
    now = datetime.utcnow()
    if (grant is None or grant.revoked_at is not None or grant.access_expires_at is None
            or grant.access_expires_at < now):
        return None, _unauthorized()
    db = appmod.db
    user = db.session.get(appmod.User, grant.user_id)
    tenant = db.session.get(appmod.Tenant, grant.tenant_id)
    if user is None or tenant is None or user.tenant_id != grant.tenant_id:
        return None, _unauthorized('This connection is no longer valid. Reconnect the AI app.')
    if tenant.status != 'active':
        return None, _forbidden("This business's ServiceBills subscription is inactive.")
    if not modules.is_enabled(tenant, 'ai_connector'):
        return None, _forbidden('The AI connector is not enabled for this business.')
    grant.last_used_at = now
    return McpContext(appmod, tenant, user, grant, roles_of(user)), None


def _summarize(args):
    """Human-readable args for the audit row, also the dedupe key. Past 500
    chars the tail is replaced by a hash of the full JSON so two calls that
    differ only after the cut (long notes) never look identical."""
    full = json.dumps(args, ensure_ascii=False, default=str, sort_keys=True)
    if len(full) <= 500:
        return full
    return full[:483] + '#' + hashlib.sha256(full.encode('utf-8')).hexdigest()[:16]


def _safe_commit(db):
    """Commit the audit/last_used writes; a failure here must never turn an
    already-computed answer into a 500."""
    try:
        db.session.commit()
    except Exception:
        logging.exception('MCP audit commit failed')
        try:
            db.session.rollback()
        except Exception:
            logging.exception('MCP rollback after failed commit also failed')


def _recent_duplicate(ctx, tool, summary):
    Call = ctx.appmod.McpToolCall
    return (Call.query.filter_by(user_id=ctx.user.id, tool=tool.name, args_summary=summary, ok=True)
            .filter(Call.created_at >= datetime.utcnow() - DEDUPE_WINDOW)
            .order_by(Call.created_at.desc()).first())


def _hhmm(ctx, dt_utc):
    zone = ctx.appmod._zone_for(_settings(ctx))
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(zone).strftime('%H:%M')


def call_tool(ctx, tool, args):
    started = time.monotonic()
    appmod = ctx.appmod
    db = appmod.db
    # Plain values: a rollback below expires the ORM objects on ctx.
    tenant_id, user_id, grant_id = ctx.tenant.id, ctx.user.id, ctx.grant.id
    role = ctx.user.role
    rolled_back = False
    ok, error = True, None
    confirmed = tool.dedupe and args.get('confirm_duplicate') is True
    if tool.dedupe:
        args = {k: v for k, v in args.items() if k != 'confirm_duplicate'}
    summary = _summarize(args)
    if not _has_role(ctx, tool):
        ok, error = False, f"Your role ({role}) can't use {tool.name}."
    elif tool.writes and not writes_enabled(ctx):
        ok, error = False, WRITES_OFF
    elif tool.dedupe and not confirmed:
        duplicate = _recent_duplicate(ctx, tool, summary)
        if duplicate is not None:
            ok, error = False, (f"This exact change was already made at {_hhmm(ctx, duplicate.created_at)}. "
                                "Call again with confirm_duplicate: true if you really want it twice.")
    if not ok:
        out = _tool_error(error)
    else:
        try:
            data = tool.handler(ctx, args)
            out = {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False, default=str)}],
                   'isError': False}
        except ToolError as e:
            ok, error = False, str(e)
            out = _tool_error(error)
            if tool.writes:
                # A write tool may have staged changes (directly or via an
                # endpoint that then refused); nothing of a refused write may
                # ride along with the audit commit. Harmless if already committed.
                try:
                    db.session.rollback()
                except Exception:
                    logging.exception('MCP rollback failed')
                rolled_back = True
        except Exception:
            logging.exception('MCP tool %s failed', tool.name)
            try:
                db.session.rollback()
            except Exception:
                logging.exception('MCP rollback failed')
            rolled_back = True
            ok, error = False, 'internal error'
            out = _tool_error(
                'Something went wrong saving this. Check in ServiceBills whether it was saved before trying again.'
                if tool.writes else 'Something went wrong reading this data. Try again or narrow the request.')
    try:
        if rolled_back:   # the rollback also discarded last_used_at set in resolve_context
            appmod.McpGrant.query.filter_by(id=grant_id).update(
                {'last_used_at': datetime.utcnow()}, synchronize_session=False)
        db.session.add(appmod.McpToolCall(
            tenant_id=tenant_id, user_id=user_id, grant_id=grant_id, tool=tool.name,
            args_summary=summary, wrote=bool(tool.writes), ok=ok, error=(error or None) and error[:300],
            duration_ms=int((time.monotonic() - started) * 1000)))
    except Exception:
        logging.exception('MCP audit write failed')
        try:
            db.session.rollback()
        except Exception:
            logging.exception('MCP rollback failed')
    return out


def handle_message(ctx, msg):
    msg_id = msg.get('id')
    method = msg.get('method')
    if not isinstance(method, str):
        return _error(msg_id, -32600, 'Invalid Request')
    params = msg.get('params')
    if params is None:
        params = {}
    elif not isinstance(params, dict):
        return _error(msg_id, -32602, 'params must be an object')
    if method == 'initialize':
        requested = params.get('protocolVersion')
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(msg_id, {'protocolVersion': version,
                                'capabilities': {'tools': {'listChanged': False}},
                                'serverInfo': SERVER_INFO,
                                'instructions': INSTRUCTIONS_WRITE if writes_enabled(ctx) else INSTRUCTIONS})
    if method == 'ping':
        return _result(msg_id, {})
    if method == 'tools/list':
        writes_on = writes_enabled(ctx)
        visible = [t for t in TOOLS.values() if _visible(ctx, t, writes_on)]
        return _result(msg_id, {'tools': [_describe(t) for t in visible]})
    if method == 'tools/call':
        name = params.get('name')
        if not isinstance(name, str):
            return _error(msg_id, -32602, 'Tool name must be a string')
        args = params.get('arguments') or {}
        tool = TOOLS.get(name)
        if tool is None or (tool.module and not modules.is_enabled(ctx.tenant, tool.module)):
            return _error(msg_id, -32602, f'Unknown tool: {name}')
        if not isinstance(args, dict):
            return _error(msg_id, -32602, 'arguments must be an object')
        return _result(msg_id, call_tool(ctx, tool, args))
    return _error(msg_id, -32601, f'Method not found: {method}')


def register_mcp_routes(app, appmod):
    db = appmod.db

    def _per_token_key():
        return mcp_oauth.hash_token(request.headers.get('Authorization', '') or (request.remote_addr or ''))

    @app.route('/mcp', methods=['POST'])
    @appmod.limiter.limit('300/minute')   # per-IP backstop
    @appmod.limiter.limit('60/minute', key_func=_per_token_key)
    def mcp_endpoint():
        ctx, err = resolve_context(appmod)
        if err is not None:
            return err
        version = request.headers.get('MCP-Protocol-Version')
        if version and version not in SUPPORTED_VERSIONS:
            return jsonify(_error(None, -32600, f'Unsupported MCP-Protocol-Version: {version}')), 400
        try:
            msg = json.loads(request.get_data() or b'')
        except ValueError:   # includes JSONDecodeError and UnicodeDecodeError
            return jsonify(_error(None, -32700, 'Parse error')), 400
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
            return jsonify(_error(None, -32600, 'Invalid Request (batches are not supported)')), 400
        is_response = 'method' not in msg and ('result' in msg or 'error' in msg)
        if 'id' not in msg or is_response:   # notification or client response: nothing to answer
            _safe_commit(db)
            return ('', 202)
        out = handle_message(ctx, msg)
        _safe_commit(db)
        return jsonify(out)

    @app.route('/mcp', methods=['GET', 'DELETE'])
    def mcp_no_stream():
        return jsonify(error='This server has no SSE stream or sessions; POST JSON-RPC to /mcp.'), 405
