# MCP Write Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a connected staff member make changes in ServiceBills from their AI app (customers, renewals, payments, expenses, cash, suppliers, payroll, resellers, tickets) with exactly the rights they have in the web app, behind a per-business admin switch that is off by default.

**Architecture:** Each write tool validates its arguments and calls the app's *existing* endpoint in-process as the signed-in user through an extended `call_api()` (POST/PUT/DELETE + JSON body), so every role check, day-close lock, balance log, upstream sync and WhatsApp send runs as for a web click. `mcp_server.py` gains `writes`/`destructive`/`dedupe` flags on `Tool`, hides and refuses write tools while `BusinessSettings.ai_connector_writes_enabled` is false, and refuses an identical successful dedupe call within 2 minutes. The new tools live in a new `mcp_write_tools.py`.

**Tech Stack:** Flask, Flask-SQLAlchemy, flask-jwt-extended, Alembic (Flask-Migrate), pytest; React (CRA) + MUI + axios + Jest/RTL for the toggle.

**Spec:** `docs/superpowers/specs/2026-11-07-mcp-write-tools-design.md`

## Global Constraints

- No new direct DB writes for business data: every write goes through an existing endpoint via `call_api`.
- The switch is `BusinessSettings.ai_connector_writes_enabled`, Boolean, NOT NULL, default false; read on every request, never cached.
- Write tools are hidden from `tools/list` unless the switch is on **and** the user has one of the tool's roles. Read tools keep today's behaviour (listed regardless of role).
- `update_customer` never sends `balance` or `is_subscription_active`.
- Every ⚠ (destructive) tool requires a `reason` argument.
- Dedupe window: 2 minutes, same user + tool + args summary (without `confirm_duplicate`), `ok = true`.
- Write error text: 400/402/403/404/409 relay the endpoint's `message`/`msg`/`error` only if it is a plain sentence (< 200 chars, no newline, no traceback/exception/SQL text, not a bare `'key'`); otherwise `ServiceBills rejected this change (<code>).`. 5xx (and an exception, as 500): `ServiceBills hit an error (<code>). Check in ServiceBills whether it was saved before trying again.` Any write answered with >= 400 is rolled back before the audit row is written.
- Writes-off message, verbatim: `Changes through the AI are turned off for this business. An admin can turn them on in Settings → AI connector.`
- New Alembic revision id `cc1af4ef875c`, `down_revision = '9af8c7a2648e'`; after adding it `flask db heads` must show exactly one head. ALTERs on Postgres use the lock_timeout + savepoint retry pattern (copy from `migrations/versions/18c24f5699dc_cs_agent_tool_secret.py`).
- Run backend tests with `python -m pytest <files> -q` from the repo root; frontend with `cd frontend && npx react-scripts test --watchAll=false <pattern>`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `migrations/versions/cc1af4ef875c_mcp_write_switch.py` (new) | Adds `business_settings.ai_connector_writes_enabled`, `mcp_tool_call.wrote` |
| `app.py` (modify) | Two model columns; import `mcp_write_tools` after `mcp_tools` |
| `mcp_oauth.py` (modify) | `writes_enabled` in `GET /api/mcp/grants`; new `PUT /api/mcp/settings` |
| `mcp_server.py` (modify) | Tool flags, role sets, switch, per-request instructions, visibility, dedupe guard, `wrote` audit |
| `mcp_tools.py` (modify) | `call_api(method, json)`, safe message relay, `amount_arg` |
| `mcp_write_tools.py` (new) | The 24 write tools + 3 read helpers |
| `tests/mcp_helpers.py` (modify) | `enable_writes()` |
| `tests/test_mcp_write_framework.py` (new) | Switch, settings API, visibility, annotations, dedupe, audit, error relay |
| `tests/test_mcp_write_customers.py` (new) | Customer/subscription tools + read helpers |
| `tests/test_mcp_write_money.py` (new) | Customer money tools |
| `tests/test_mcp_write_finance.py` (new) | Expenses, cash, transfers, suppliers, payroll, resellers |
| `tests/test_mcp_write_tickets.py` (new) | Ticket tools + e2e |
| `frontend/src/components/AIConnectorCard.js` (+ `.test.js`) | Admin toggle |

---

### Task 1: Switch column, audit column, settings API

**Files:**
- Create: `migrations/versions/cc1af4ef875c_mcp_write_switch.py`
- Modify: `app.py` (class `BusinessSettings` ~line 1566, class `McpToolCall` ~line 2210)
- Modify: `mcp_oauth.py` (`mcp_list_grants` ~line 419; add new route after `mcp_revoke_grant`)
- Modify: `tests/mcp_helpers.py`
- Test: `tests/test_mcp_write_framework.py`

**Interfaces:**
- Produces: `BusinessSettings.ai_connector_writes_enabled: bool`, `McpToolCall.wrote: bool`; `GET /api/mcp/grants` → adds `writes_enabled: bool`; `PUT /api/mcp/settings {writes_enabled: bool}` → `{writes_enabled}` (admin only); `tests.mcp_helpers.enable_writes(username, on=True)`.

- [ ] **Step 1: Add the test helper** — append to `tests/mcp_helpers.py`:

```python
def enable_writes(username, on=True):
    """Turn the 'Allow the AI to make changes' switch on/off for `username`'s tenant."""
    from app import db, BusinessSettings, User
    user = User.query.filter_by(username=username).first()
    s = BusinessSettings.query.filter_by(tenant_id=user.tenant_id).first()
    if s is None:
        s = BusinessSettings(tenant_id=user.tenant_id, business_name="Biz", address="", mobile="")
        db.session.add(s)
    s.ai_connector_writes_enabled = on
    db.session.commit()
```

- [ ] **Step 2: Write the failing tests** — create `tests/test_mcp_write_framework.py`:

```python
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import enable_connector


def _admin(client, username="wf_admin", business="WF Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    return hdr


def test_switch_defaults_off_and_admin_can_toggle(client):
    hdr = _admin(client)
    assert client.get("/api/mcp/grants", headers=hdr).get_json()["writes_enabled"] is False
    r = client.put("/api/mcp/settings", headers=hdr, json={"writes_enabled": True})
    assert r.status_code == 200 and r.get_json() == {"writes_enabled": True}
    assert client.get("/api/mcp/grants", headers=hdr).get_json()["writes_enabled"] is True


def test_settings_rejects_non_admin_and_bad_value(client):
    hdr = _admin(client)
    assert client.put("/api/mcp/settings", headers=hdr, json={"writes_enabled": "yes"}).status_code == 400
    fin = auth_headers(client, username="wf_fin", role="finance")
    assert client.put("/api/mcp/settings", headers=fin, json={"writes_enabled": True}).status_code == 403


def test_audit_column_defaults_false(client):
    import app as appmod
    assert appmod.McpToolCall.__table__.c.wrote.default.arg is False
    assert appmod.BusinessSettings.__table__.name == "business_settings"
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_mcp_write_framework.py -q`
Expected: FAIL (`KeyError: 'writes_enabled'` / 404 on `/api/mcp/settings` / `AttributeError: wrote`).

- [ ] **Step 4: Add the model columns** in `app.py`.

In `class BusinessSettings`, after `reporting_currency = ...`:
```python
    # AI connector (MCP) may make changes, not just read. Off until an admin turns it on.
    ai_connector_writes_enabled = db.Column(db.Boolean, nullable=False, default=False,
                                            server_default=db.false())
```
In `class McpToolCall`, after `duration_ms`:
```python
    wrote = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
```

- [ ] **Step 5: Write the migration** `migrations/versions/cc1af4ef875c_mcp_write_switch.py`:

```python
"""mcp: write-tools switch + audit flag

Revision ID: cc1af4ef875c
Revises: 9af8c7a2648e
Create Date: 2026-11-07

business_settings.ai_connector_writes_enabled (off by default) gates the AI
connector's write tools; mcp_tool_call.wrote marks audit rows of write tools.
Additive only. Both tables are read on live requests, so Postgres ALTERs use
the lock_timeout + savepoint retry.
"""
import time

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

revision = 'cc1af4ef875c'
down_revision = '9af8c7a2648e'
branch_labels = None
depends_on = None

_COLUMNS = (('business_settings', 'ai_connector_writes_enabled'), ('mcp_tool_call', 'wrote'))


def _with_lock_retry(bind, ddl, attempts=60):
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
    for table, column in _COLUMNS:
        if column in {c['name'] for c in inspect(bind).get_columns(table)}:
            continue
        if pg:
            _with_lock_retry(bind, lambda t=table, c=column: bind.execute(text(
                'ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} BOOLEAN NOT NULL DEFAULT false'.format(t, c))))
        else:
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    bind = op.get_bind()
    for table, column in _COLUMNS:
        if column in {c['name'] for c in inspect(bind).get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
```

- [ ] **Step 6: Settings API** in `mcp_oauth.py`. In `mcp_list_grants`, compute the flag and add it to the response:
```python
        settings = tenant_query(appmod.BusinessSettings).first()
        return jsonify(mcp_url=origin() + '/mcp',
                       enabled=modules.is_enabled(current_tenant(), 'ai_connector'),
                       writes_enabled=bool(settings and settings.ai_connector_writes_enabled),
                       is_admin=is_admin,
                       grants=[g.to_dict(names.get(g.client_id)) for g in grants])
```
After `mcp_revoke_grant`, add:
```python
    @app.route('/api/mcp/settings', methods=['PUT'])
    @jwt_required()
    def mcp_update_settings():
        if 'admin' not in appmod._jwt_roles():
            return jsonify(msg='Only an admin can change this.'), 403
        value = (request.get_json(silent=True) or {}).get('writes_enabled')
        if not isinstance(value, bool):
            return jsonify(msg='writes_enabled must be true or false.'), 400
        settings = tenant_query(appmod.BusinessSettings).first()
        if settings is None:
            # Same defaults set_cash_opening uses for a tenant that never saved Settings.
            settings = appmod.BusinessSettings(business_name='Default Business', address='', mobile='')
            db.session.add(settings)
        settings.ai_connector_writes_enabled = value
        db.session.commit()
        return jsonify(writes_enabled=value)
```
(`request`, `jsonify`, `jwt_required`, `tenant_query`, `db` are already imported/bound in this module — check the top of `register_oauth_routes` and add any missing import.)

- [ ] **Step 7: Run tests + single head**

Run: `python -m pytest tests/test_mcp_write_framework.py tests/test_mcp_grants_api.py -q` → all PASS.
Run: `FLASK_APP=app.py DATABASE_URL=sqlite:///$TEMP/heads.db python -m flask db heads` → exactly `cc1af4ef875c (head)`.
Run (SQLite upgrade/downgrade smoke): `rm -f $TEMP/mig.db; FLASK_APP=app.py DATABASE_URL=sqlite:///$TEMP/mig.db python -m flask db upgrade && FLASK_APP=app.py DATABASE_URL=sqlite:///$TEMP/mig.db python -m flask db downgrade -1 && FLASK_APP=app.py DATABASE_URL=sqlite:///$TEMP/mig.db python -m flask db upgrade` → no errors.

- [ ] **Step 8: Commit**

```bash
git add app.py mcp_oauth.py migrations/versions/cc1af4ef875c_mcp_write_switch.py tests/mcp_helpers.py tests/test_mcp_write_framework.py
git commit -m "feat(mcp): write-tools switch, audit flag and settings API"
```

---

### Task 2: Server framework — flags, visibility, switch, dedupe, audit

**Files:**
- Modify: `mcp_server.py`
- Test: `tests/test_mcp_write_framework.py` (append)

**Interfaces:**
- Consumes: Task 1 columns, `enable_writes`.
- Produces (in `mcp_server`): role sets `DESK = ('admin','finance','cashier')`, `COLLECT = ('admin','finance','cashier','collector')`, `ADMIN = ('admin',)`; `Tool(..., writes=False, destructive=False, dedupe=False)`; `writes_enabled(ctx) -> bool`; `WRITES_OFF` string; handlers of `dedupe` tools receive `args` **without** `confirm_duplicate`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_mcp_write_framework.py`:

```python
import pytest

import mcp_server
from tests.mcp_helpers import (call_tool, connect, enable_writes, mcp, tool_data,
                               tool_error_text)

CALLS = []


def _fake_write(ctx, args):
    CALLS.append(dict(args))
    return {"ok": True, "args": args}


@pytest.fixture
def fake_tools(monkeypatch):
    CALLS.clear()
    monkeypatch.setitem(mcp_server.TOOLS, "zz_write", mcp_server.Tool(
        name="zz_write", title="Fake write", description="test",
        input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
        handler=_fake_write, roles=mcp_server.DESK, writes=True, dedupe=True))
    monkeypatch.setitem(mcp_server.TOOLS, "zz_undo", mcp_server.Tool(
        name="zz_undo", title="Fake undo", description="test",
        input_schema={"type": "object", "properties": {}},
        handler=_fake_write, roles=mcp_server.ADMIN, writes=True, destructive=True))


def _names(client, tok):
    return {t["name"]: t for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}


def test_write_tools_hidden_and_refused_while_switch_off(client, fake_tools):
    hdr = _admin(client)
    tok, _, _ = connect(client, hdr)
    assert "zz_write" not in _names(client, tok)
    assert "turned off for this business" in tool_error_text(call_tool(client, tok, "zz_write", {"n": 1}))
    assert CALLS == []
    instr = mcp(client, tok, "initialize", {"protocolVersion": "2025-06-18"}).get_json()["result"]["instructions"]
    assert "You cannot change anything" in instr


def test_switch_on_lists_with_annotations_and_role_filter(client, fake_tools):
    hdr = _admin(client)
    enable_writes("wf_admin")
    tok, _, _ = connect(client, hdr)
    tools = _names(client, tok)
    ann = tools["zz_write"]["annotations"]
    assert ann["readOnlyHint"] is False and ann["destructiveHint"] is False and ann["idempotentHint"] is False
    assert "confirm_duplicate" in tools["zz_write"]["inputSchema"]["properties"]
    assert tools["zz_undo"]["annotations"]["destructiveHint"] is True
    assert tools["search_customers"]["annotations"]["readOnlyHint"] is True
    instr = mcp(client, tok, "initialize", {"protocolVersion": "2025-06-18"}).get_json()["result"]["instructions"]
    assert "You cannot change anything" not in instr and "confirm amounts" in instr
    fin = auth_headers(client, username="wf_fin2", role="finance")
    tok_fin, _, _ = connect(client, fin)
    names = _names(client, tok_fin)
    assert "zz_write" in names and "zz_undo" not in names
    assert "role (finance)" in tool_error_text(call_tool(client, tok_fin, "zz_undo", {}))


def test_dedupe_guard(client, fake_tools):
    import app as appmod
    hdr = _admin(client)
    enable_writes("wf_admin")
    tok, _, _ = connect(client, hdr)
    assert tool_data(call_tool(client, tok, "zz_write", {"n": 1}))["ok"] is True
    msg = tool_error_text(call_tool(client, tok, "zz_write", {"n": 1}))
    assert "already made" in msg and "confirm_duplicate" in msg
    assert tool_data(call_tool(client, tok, "zz_write", {"n": 2}))["ok"] is True
    assert tool_data(call_tool(client, tok, "zz_write", {"n": 1, "confirm_duplicate": True}))["ok"] is True
    assert CALLS == [{"n": 1}, {"n": 2}, {"n": 1}]   # confirm_duplicate is stripped
    rows = appmod.McpToolCall.query.filter_by(tool="zz_write").all()
    assert len(rows) == 4 and all(r.wrote for r in rows)
    assert appmod.McpToolCall.query.filter_by(tool="search_customers").count() == 0


def test_failed_call_does_not_block_retry(client, fake_tools, monkeypatch):
    hdr = _admin(client)
    enable_writes("wf_admin")
    tok, _, _ = connect(client, hdr)

    def boom(ctx, args):
        raise mcp_server.ToolError("Day 2026-11-06 is closed.")
    monkeypatch.setattr(mcp_server.TOOLS["zz_write"], "handler", boom)
    assert "closed" in tool_error_text(call_tool(client, tok, "zz_write", {"n": 5}))
    monkeypatch.setattr(mcp_server.TOOLS["zz_write"], "handler", _fake_write)
    assert tool_data(call_tool(client, tok, "zz_write", {"n": 5}))["ok"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_mcp_write_framework.py -q`
Expected: FAIL (`AttributeError: module 'mcp_server' has no attribute 'DESK'`).

- [ ] **Step 3: Implement in `mcp_server.py`.**

Imports: change `from datetime import datetime` to `from datetime import datetime, timedelta, timezone`.

After `INSTRUCTIONS = (...)` add:
```python
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
```
After `PAYMENTS = ...` add:
```python
DESK = ('admin', 'finance', 'cashier')          # subscription_desk_required
COLLECT = ('admin', 'finance', 'cashier', 'collector')
ADMIN = ('admin',)
```
Extend `Tool`:
```python
    module: Optional[str] = None
    writes: bool = False        # changes data; hidden/refused while the switch is off
    destructive: bool = False   # undo-style (revert/refund/cancel/delete)
    dedupe: bool = False        # refuse an identical successful call within DEDUPE_WINDOW
```
Add helpers after `roles_of`:
```python
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
```
Replace `_describe`:
```python
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
```
Add before `call_tool`:
```python
def _recent_duplicate(ctx, tool, summary):
    Call = ctx.appmod.McpToolCall
    return (Call.query.filter_by(user_id=ctx.user.id, tool=tool.name, args_summary=summary, ok=True)
            .filter(Call.created_at >= datetime.utcnow() - DEDUPE_WINDOW)
            .order_by(Call.created_at.desc()).first())


def _hhmm(ctx, dt_utc):
    zone = ctx.appmod._zone_for(_settings(ctx))
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(zone).strftime('%H:%M')
```
Replace the body of `call_tool` from `rolled_back = False` through the end of the `else:` branch (the generic exception handler) with:
```python
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
```
In the audit `McpToolCall(...)` insert that follows, use `args_summary=summary` and add `wrote=bool(tool.writes)`.

In `handle_message`:
```python
    if method == 'initialize':
        ...
        return _result(msg_id, {'protocolVersion': version,
                                'capabilities': {'tools': {'listChanged': False}},
                                'serverInfo': SERVER_INFO,
                                'instructions': INSTRUCTIONS_WRITE if writes_enabled(ctx) else INSTRUCTIONS})
    ...
    if method == 'tools/list':
        writes_on = writes_enabled(ctx)
        visible = [t for t in TOOLS.values() if _visible(ctx, t, writes_on)]
        return _result(msg_id, {'tools': [_describe(t) for t in visible]})
```
`tools/call` stays as is (unknown only when the module is off), so a write tool called while the switch is off reaches `call_tool` and gets `WRITES_OFF`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_mcp_write_framework.py tests/test_mcp_server.py tests/test_mcp_e2e.py tests/test_mcp_tools_customers.py tests/test_mcp_tools_finance.py tests/test_mcp_tools_other.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py tests/test_mcp_write_framework.py
git commit -m "feat(mcp): write-tool flags, switch gating, dedupe guard and write audit"
```

---

### Task 3: `call_api` writes, safe error relay, `amount_arg`

**Files:**
- Modify: `mcp_tools.py` (`call_api`, new helpers after `enum_arg`)
- Test: `tests/test_mcp_write_framework.py` (append)

**Interfaces:**
- Produces: `call_api(ctx, path, params=None, method='GET', json=None) -> dict|list`; `safe_message(data) -> str|None`; `amount_arg(args, key, required=True, allow_zero=False) -> float|None`.

- [ ] **Step 1: Write the failing tests** — append:

```python
from mcp_server import ToolError
from mcp_tools import amount_arg, safe_message


def test_safe_message_filters_internal_text():
    assert safe_message({"message": "Day 2026-11-06 is closed — ask an admin to reopen it."}).startswith("Day")
    assert safe_message({"error": "Category 'Fuel' not found."}) == "Category 'Fuel' not found."
    assert safe_message({"error": "'phone'"}) is None
    assert safe_message({"error": "(psycopg2.errors.UniqueViolation) duplicate key"}) is None
    assert safe_message({"error": "Traceback (most recent call last):\n ..."}) is None
    assert safe_message({"error": "x" * 250}) is None
    assert safe_message(["not a dict"]) is None


def test_amount_arg():
    assert amount_arg({"a": "12.5"}, "a") == 12.5
    assert amount_arg({}, "a", required=False) is None
    assert amount_arg({"a": 0}, "a", allow_zero=True) == 0.0
    for bad in (0, -1, "abc", True, 1.234):
        with pytest.raises(ToolError):
            amount_arg({"a": bad}, "a")
    with pytest.raises(ToolError):
        amount_arg({}, "a")
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_mcp_write_framework.py -q` → ImportError.

- [ ] **Step 3: Implement** in `mcp_tools.py`. After `enum_arg` add:

```python
def amount_arg(args, key, required=True, allow_zero=False):
    """Positive money amount with at most 2 decimals (0 allowed with allow_zero)."""
    v = args.get(key)
    if v is None or v == '':
        if required:
            raise ToolError(f"'{key}' is required.")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ToolError(f"'{key}' must be a number.")
    try:
        n = float(v)
    except ValueError:
        raise ToolError(f"'{key}' must be a number.")
    if not (0 <= n < 1e9) or (n == 0 and not allow_zero):
        raise ToolError(f"'{key}' must be greater than zero.")
    if round(n, 2) != n:
        raise ToolError(f"'{key}' can have at most 2 decimals.")
    return n


_INTERNAL = re.compile(r'traceback|exception|psycopg|sqlalchemy|integrityerror|\bsql\b|errno|'
                       r'object has no attribute|unsupported operand|not subscriptable', re.I)


def safe_message(data):
    """An endpoint's own error sentence, or None when it might be internal text
    (str(exception) is returned by several endpoints' catch-alls)."""
    if not isinstance(data, dict):
        return None
    for key in ('message', 'msg', 'error'):
        v = data.get(key)
        if not isinstance(v, str):
            continue
        v = v.strip()
        if (0 < len(v) < 200 and '\n' not in v and not _INTERNAL.search(v)
                and not re.fullmatch(r"'[\w.]+'", v)):
            return v
    return None
```
Replace `call_api` with (docstring kept, one sentence added):
```python
def call_api(ctx, path, params=None, method='GET', json=None):
    """... (existing docstring) ...

    method/json make the same in-process call for a write; the endpoint
    commits its own work."""
    app = ctx.appmod.app
    writing = method != 'GET'
    token = create_access_token(identity=ctx.user.username,
                                additional_claims={'role': ctx.user.role, 'tenant_id': ctx.user.tenant_id},
                                expires_delta=timedelta(minutes=5))
    query = {k: v for k, v in (params or {}).items() if v is not None}
    verb = 'save' if writing else 'load'
    try:
        with app.test_request_context(path, method=method, query_string=query, json=json,
                                      headers={'Authorization': f'Bearer {token}'}):
            resp = app.full_dispatch_request()
    except Exception:
        ctx.appmod.db.session.rollback()
        raise ToolError(f"ServiceBills couldn't {verb} this (500)."
                        + (' Nothing was changed.' if writing else ''))
    data = resp.get_json(silent=True)
    code = resp.status_code
    if code >= 400:
        if code >= 500:
            raise ToolError(f"ServiceBills couldn't {verb} this ({code})."
                            + (' Nothing was changed.' if writing else ''))
        if writing:
            raise ToolError(safe_message(data) or f"ServiceBills rejected this change ({code}).")
        # Reads: only `msg` from 403/404 is ours to relay (permission/not-found
        # wording); a 400 catch-all or an `error` key can carry str(exception).
        msg = data.get('msg') if isinstance(data, dict) and isinstance(data.get('msg'), str) else None
        if code == 403:
            raise ToolError(msg or "You don't have access to this in ServiceBills.")
        if code == 404 and msg:
            raise ToolError(f"ServiceBills couldn't load this (404): {msg}.")
        raise ToolError(f"ServiceBills couldn't load this ({code}).")
    return data
```
(`test_request_context(json=None)` sends no body — same as today for GETs.)

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_mcp_write_framework.py tests/test_mcp_tools_finance.py tests/test_mcp_tools_other.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_tools.py tests/test_mcp_write_framework.py
git commit -m "feat(mcp): call_api writes, safe error relay, amount_arg"
```

---

### Task 4: Customer & subscription tools + read helpers

**Files:**
- Create: `mcp_write_tools.py`
- Modify: `app.py` (line after `import mcp_tools  # noqa: F401 ...`)
- Test: `tests/test_mcp_write_customers.py`

**Interfaces:**
- Consumes: `call_api`, `amount_arg`, `str_arg`, `int_arg`, `bool_arg`, `date_arg`, `enum_arg`, `scoped`, `today`, `day`, `local`, `_customer_row`, `_payment_row` from `mcp_tools`; `Tool`, `register_tool`, `ToolError`, `ALL_STAFF`, `FINANCE`, `PAYMENTS`, `DESK`, `COLLECT`, `ADMIN` from `mcp_server`.
- Produces in `mcp_write_tools`: `_done(resp, **extra) -> dict`, `_reason(args) -> str`, `_customer_after(ctx, cid) -> dict`; tools `list_plans`, `list_expense_categories`, `find_payments`, `create_customer`, `update_customer`, `renew_subscription`, `activate_subscription`, `cancel_subscription`, `network_suspend`, `network_unsuspend`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_mcp_write_customers.py`:

```python
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import (call_tool, connect, enable_connector, enable_writes, mcp, tool_data,
                               tool_error_text)


def _setup(client, username="wc_admin", business="WC Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    enable_writes(username)
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "Basic", "price": 10, "billing_cycle": "monthly"})
    plan = r.get_json()["plan"]["id"]
    tok, _, _ = connect(client, hdr)
    return hdr, plan, tok


def test_list_plans(client):
    _, plan, tok = _setup(client)
    d = tool_data(call_tool(client, tok, "list_plans", {}))
    assert d["plans"][0]["id"] == plan and d["plans"][0]["price"] == 10.0


def test_create_update_and_never_touch_balance(client):
    import app as appmod
    _, plan, tok = _setup(client)
    d = tool_data(call_tool(client, tok, "create_customer", {
        "name": "Ali Hassan", "phone": "03123456", "subscription_plan_id": plan}))
    cid = d["customer"]["id"]
    assert d["customer"]["name"] == "Ali Hassan" and "added" in d["result"].lower()
    c = appmod.db.session.get(appmod.Customer, cid)
    before = float(c.balance)
    d = tool_data(call_tool(client, tok, "update_customer", {
        "customer_id": cid, "address": "Hamra", "balance": 999}))
    assert d["customer"]["address"] == "Hamra"
    assert float(appmod.db.session.get(appmod.Customer, cid).balance) == before
    tools = {t["name"]: t for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert "balance" not in tools["update_customer"]["inputSchema"]["properties"]


def test_create_requires_fields_before_calling(client):
    import app as appmod
    _, plan, tok = _setup(client)
    assert "'name' is required" in tool_error_text(call_tool(client, tok, "create_customer", {
        "phone": "03123456", "subscription_plan_id": plan}))
    assert appmod.Customer.query.count() == 0


def test_cancel_activate_renew(client):
    import app as appmod
    _, plan, tok = _setup(client)
    cid = tool_data(call_tool(client, tok, "create_customer", {
        "name": "Rana", "phone": "70111222", "subscription_plan_id": plan}))["customer"]["id"]
    assert "'reason' is required" in tool_error_text(call_tool(client, tok, "cancel_subscription",
                                                                {"customer_id": cid}))
    d = tool_data(call_tool(client, tok, "cancel_subscription", {"customer_id": cid, "reason": "moved away"}))
    assert d["customer"]["active"] is False
    d = tool_data(call_tool(client, tok, "activate_subscription", {"customer_id": cid}))
    assert d["customer"]["active"] is True
    old = appmod.db.session.get(appmod.Customer, cid).subscription_expiry_date
    d = tool_data(call_tool(client, tok, "renew_subscription", {"customer_id": cid}))
    assert appmod.db.session.get(appmod.Customer, cid).subscription_expiry_date > old
    # second identical renew within 2 minutes is refused by the dedupe guard
    assert "already made" in tool_error_text(call_tool(client, tok, "renew_subscription", {"customer_id": cid}))


def test_other_tenant_customer_not_found(client):
    _, plan_a, tok_a = _setup(client, "wa_a", "Biz WA")
    cid = tool_data(call_tool(client, tok_a, "create_customer", {
        "name": "A cust", "phone": "03999999", "subscription_plan_id": plan_a}))["customer"]["id"]
    _, _, tok_b = _setup(client, "wb_b", "Biz WB")
    assert "not found" in tool_error_text(call_tool(client, tok_b, "update_customer",
                                                    {"customer_id": cid, "notes": "x"})).lower()


def test_employee_cannot_see_or_use_desk_tools(client):
    _setup(client)
    emp = auth_headers(client, username="wc_emp", role="employee")
    tok, _, _ = connect(client, emp)
    names = {t["name"] for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert "create_customer" not in names
    assert "role (employee)" in tool_error_text(call_tool(client, tok, "renew_subscription", {"customer_id": 1}))
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_mcp_write_customers.py -q` → FAIL (unknown tool `list_plans`).

- [ ] **Step 3: Create `mcp_write_tools.py`:**

```python
"""Write tools (and the read helpers they need) for the MCP connector -- see
docs/superpowers/specs/2026-11-07-mcp-write-tools-design.md.

Every write calls the app's own endpoint in-process as the signed-in user via
call_api(), so role checks, the day-close lock, balance log, upstream sync and
WhatsApp sends run exactly as for a click in the web app. Nothing here writes
business data directly. Registered at import time (app.py imports this module
after mcp_tools)."""
from mcp_server import (ADMIN, ALL_STAFF, COLLECT, DESK, FINANCE, PAYMENTS, Tool, ToolError,
                        register_tool)
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
```
Note: `cancel_subscription`'s reason is required and kept in the audit (`args_summary`); the endpoint takes no reason.

In `app.py`, directly after `import mcp_tools  # noqa: F401 -- registers the MCP connector's tools` add:
```python
import mcp_write_tools  # noqa: F401 -- registers the MCP connector's write tools
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_mcp_write_customers.py tests/test_mcp_write_framework.py -q` → PASS. If `renew_subscription` fails because the customer's first bill blocks renewal, read `_renew_subscription_core` (app.py ~line 10568) and adjust the test setup (not the tool) so it renews a customer the web app would allow to renew.

- [ ] **Step 5: Commit**

```bash
git add mcp_write_tools.py app.py tests/test_mcp_write_customers.py
git commit -m "feat(mcp): customer and subscription write tools, plan/category/payment lookups"
```

---

### Task 5: Customer money tools

**Files:**
- Modify: `mcp_write_tools.py` (append)
- Test: `tests/test_mcp_write_money.py`

**Interfaces:**
- Consumes: Task 4 helpers `_done`, `_reason`, `_cid`, `_customer_after`, `CUSTOMER_ID`, `REASON`.
- Produces: tools `receive_payment`, `confirm_collected`, `revert_payment`, `refund_payment`, `mark_payment_gratis`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_mcp_write_money.py`:

```python
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import (call_tool, connect, enable_connector, enable_writes, tool_data,
                               tool_error_text)


def _setup(client, username="wm_admin", business="WM Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    enable_writes(username)
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "Basic", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    tok, _, _ = connect(client, hdr)
    cid = tool_data(call_tool(client, tok, "create_customer", {
        "name": "Payer", "phone": "03222333", "subscription_plan_id": plan}))["customer"]["id"]
    return hdr, tok, cid


def _paid_payment(client, tok, cid):
    pays = tool_data(call_tool(client, tok, "find_payments", {"customer_id": cid}))["payments"]
    return next(p for p in pays if p["paid"] and not p["reverted"] and not p["is_refund"])


def test_receive_payment_then_revert(client):
    import app as appmod
    _, tok, cid = _setup(client)
    d = tool_data(call_tool(client, tok, "receive_payment", {"customer_id": cid, "amount": 10}))
    assert d["customer"]["balance"] >= 0
    p = _paid_payment(client, tok, cid)
    assert "'reason' is required" in tool_error_text(call_tool(client, tok, "revert_payment",
                                                                {"payment_id": p["id"]}))
    tool_data(call_tool(client, tok, "revert_payment", {"payment_id": p["id"], "reason": "wrong customer"}))
    assert appmod.db.session.get(appmod.Payment, p["id"]).reverted_at is not None


def test_refund(client):
    _, tok, cid = _setup(client)
    tool_data(call_tool(client, tok, "receive_payment", {"customer_id": cid, "amount": 10}))
    p = _paid_payment(client, tok, cid)
    d = tool_data(call_tool(client, tok, "refund_payment", {"payment_id": p["id"], "amount": 4, "reason": "outage"}))
    assert "result" in d
    msg = tool_error_text(call_tool(client, tok, "refund_payment", {"payment_id": p["id"], "amount": 50,
                                                                    "reason": "too much"}))
    assert "cannot exceed" in msg


def test_collector_collects_and_admin_confirms(client):
    _, tok, cid = _setup(client)
    col = auth_headers(client, username="wm_col", role="collector")
    tok_col, _, _ = connect(client, col)
    d = tool_data(call_tool(client, tok_col, "receive_payment", {"customer_id": cid, "amount": 10}))
    assert "awaiting confirmation" in d["result"]
    assert "Only finance or admin" in tool_error_text(call_tool(
        client, tok_col, "receive_payment", {"customer_id": cid, "amount": 10, "action": "pay"}))
    d = tool_data(call_tool(client, tok, "confirm_collected", {"customer_id": cid}))
    assert "Confirmed" in d["result"]


def test_amount_validated_before_call(client):
    import app as appmod
    _, tok, cid = _setup(client)
    n = appmod.Payment.query.count()
    assert "at most 2 decimals" in tool_error_text(call_tool(client, tok, "receive_payment",
                                                             {"customer_id": cid, "amount": 1.234}))
    assert appmod.Payment.query.count() == n
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_mcp_write_money.py -q` → FAIL (unknown tool `receive_payment`).

- [ ] **Step 3: Append to `mcp_write_tools.py`:**

```python
# --- customer money ---------------------------------------------------------------

PAYMENT_ID = {'type': 'integer', 'description': 'From find_payments'}


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
    description='Give money back on a paid payment (full or part, not more than the payment). Find the id with find_payments.',
    input_schema={'type': 'object', 'required': ['payment_id', 'amount', 'reason'], 'properties': {
        'payment_id': PAYMENT_ID, 'amount': {'type': 'number', 'exclusiveMinimum': 0}, 'reason': REASON}},
    handler=refund_payment, roles=FINANCE, writes=True, destructive=True, dedupe=True))

register_tool(Tool(
    name='mark_payment_gratis', title='Mark bill free',
    description='Mark an unpaid bill as free of charge (gratis), e.g. compensation for an outage.',
    input_schema={'type': 'object', 'required': ['payment_id'], 'properties': {
        'payment_id': PAYMENT_ID, 'note': {'type': 'string'}}},
    handler=mark_payment_gratis, roles=FINANCE, writes=True))
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_mcp_write_money.py -q` → PASS. If `receive_payment` with `amount: 10` is refused because the first bill isn't owed yet (balance 0), read `receive_customer_payment` (app.py ~line 6575) and make the test customer owe money via the web API the way an office would (e.g. `subscription_start_date` in the past), not by changing the tool.

- [ ] **Step 5: Commit**

```bash
git add mcp_write_tools.py tests/test_mcp_write_money.py
git commit -m "feat(mcp): receive/confirm/revert/refund/gratis payment tools"
```

---

### Task 6: Expenses, cash, transfers, suppliers, payroll, resellers

**Files:**
- Modify: `mcp_write_tools.py` (append)
- Test: `tests/test_mcp_write_finance.py`

**Interfaces:**
- Consumes: `_done`, `_reason`, `REASON`, `amount_arg`, `date_arg`, `day`.
- Produces: tools `add_expense`, `update_expense`, `delete_expense`, `add_cash_entry`, `delete_cash_entry`, `transfer_between_accounts`, `delete_transfer`, `pay_supplier`, `delete_supplier_payment`, `add_employee_charge`, `pay_employee`, `reseller_add_credit`, `reseller_collect_payment`.

Role notes (from the endpoints): `delete_cash_entry`, `delete_transfer`, `delete_supplier_payment`, `add_employee_charge`, `pay_employee` are **admin only**; the rest admin + finance. Cash entries are cash-*in* only (positive amount, account `cash` or `whish`) — there is no direction argument.

- [ ] **Step 1: Write the failing tests** — create `tests/test_mcp_write_finance.py`:

```python
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import (call_tool, connect, enable_connector, enable_writes, mcp, tool_data,
                               tool_error_text)


def _setup(client, username="wx_admin", business="WX Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    enable_writes(username)
    tok, _, _ = connect(client, hdr)
    return hdr, tok


def test_expense_add_update_delete(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/expense_categories", headers=hdr, json={"name": "Fuel"})
    assert tool_data(call_tool(client, tok, "list_expense_categories", {}))["categories"] == ["Fuel"]
    d = tool_data(call_tool(client, tok, "add_expense", {"amount": 25, "category": "Fuel",
                                                          "description": "generator"}))
    eid = d["expense"]["id"]
    tool_data(call_tool(client, tok, "update_expense", {"expense_id": eid, "amount": 30}))
    assert float(appmod.db.session.get(appmod.Expense, eid).amount) == 30.0
    assert "not found" in tool_error_text(call_tool(client, tok, "add_expense",
                                                    {"amount": 5, "category": "Nope"}))
    tool_data(call_tool(client, tok, "delete_expense", {"expense_id": eid, "reason": "duplicate"}))
    assert appmod.db.session.get(appmod.Expense, eid) is None


def test_cash_entry_and_transfer(client):
    import app as appmod
    _, tok = _setup(client)
    d = tool_data(call_tool(client, tok, "add_cash_entry", {"account": "cash", "amount": 50, "reason": "float"}))
    eid = d["entry"]["id"]
    d = tool_data(call_tool(client, tok, "transfer_between_accounts",
                            {"from_account": "cash", "to_account": "whish", "amount": 20}))
    tid = d["transfer"]["id"]
    tool_data(call_tool(client, tok, "delete_transfer", {"transfer_id": tid, "reason": "typo"}))
    tool_data(call_tool(client, tok, "delete_cash_entry", {"entry_id": eid, "reason": "typo"}))
    assert appmod.CashEntry.query.count() == 0 and appmod.AccountTransfer.query.count() == 0


def test_supplier_payment(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/suppliers", headers=hdr, json={"name": "Fiber Co"})
    sid = appmod.Supplier.query.filter_by(name="Fiber Co").one().id
    d = tool_data(call_tool(client, tok, "pay_supplier", {"supplier_id": sid, "amount": 100}))
    pid = d["payment"]["id"]
    tool_data(call_tool(client, tok, "delete_supplier_payment",
                        {"supplier_id": sid, "payment_id": pid, "reason": "wrong supplier"}))


def test_payroll_admin_only(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/employees", headers=hdr, json={"name": "Sami"})
    emp_id = appmod.Employee.query.filter_by(name="Sami").one().id
    d = tool_data(call_tool(client, tok, "add_employee_charge",
                            {"employee_id": emp_id, "type": "salary", "amount": 400}))
    assert d["employee"]["balance"] == 400.0
    d = tool_data(call_tool(client, tok, "pay_employee", {"employee_id": emp_id, "amount": 150, "is_advance": True}))
    assert d["employee"]["balance"] == 250.0
    fin = auth_headers(client, username="wx_fin", role="finance")
    tok_fin, _, _ = connect(client, fin)
    names = {t["name"] for t in mcp(client, tok_fin, "tools/list").get_json()["result"]["tools"]}
    assert "pay_employee" not in names and "add_expense" in names


def test_reseller_credit_and_collect(client):
    import app as appmod
    hdr, tok = _setup(client)
    client.post("/api/resellers", headers=hdr, json={"name": "R1", "phone": "1", "type": "type1"})
    rid = appmod.Reseller.query.filter_by(name="R1").one().id
    d = tool_data(call_tool(client, tok, "reseller_add_credit", {"reseller_id": rid, "amount": 30}))
    assert "Credit added" in d["result"]
    tool_data(call_tool(client, tok, "reseller_collect_payment", {"reseller_id": rid, "amount": 10}))
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_mcp_write_finance.py -q` → FAIL.

- [ ] **Step 3: Append to `mcp_write_tools.py`:**

```python
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
    resp = call_api(ctx, f'/api/suppliers/{sid}/payments', method='POST', json=body)
    return _done(resp, supplier=resp.get('supplier'), payment=resp.get('payment'))


def delete_supplier_payment(ctx, args):
    sid, pid = _id(args, 'supplier_id'), _id(args, 'payment_id')
    _reason(args)
    return _done(call_api(ctx, f'/api/suppliers/{sid}/payments/{pid}', method='DELETE'), payment_id=pid)


_EXPENSE_PROPS = {'amount': MONEY, 'category': {'type': 'string', 'description': 'From list_expense_categories'},
                  'date': DATE, 'description': {'type': 'string'},
                  'supplier_id': {'type': 'integer'}, 'employee_id': {'type': 'integer'},
                  'is_credit': {'type': 'boolean', 'description': 'Bought on credit from the supplier (not paid yet)'}}

register_tool(Tool(
    name='add_expense', title='Add expense',
    description='Record a business expense in a category (see list_expense_categories). Counts in cash flow and profit.',
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
    description='Record a payment to a supplier (reduces what the business owes them).',
    input_schema={'type': 'object', 'required': ['supplier_id', 'amount'], 'properties': {
        'supplier_id': {'type': 'integer'}, 'amount': MONEY, 'payment_date': DATE,
        'payment_method': {'type': 'string'}, 'reference_note': {'type': 'string'}}},
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
        if not re.fullmatch(r'\d{4}-\d{2}', period):
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
    description='Pay an employee their salary, or an advance (is_advance true). Recorded as an expense.',
    input_schema={'type': 'object', 'required': ['employee_id', 'amount'], 'properties': {
        'employee_id': {'type': 'integer'}, 'amount': MONEY, 'is_advance': {'type': 'boolean', 'default': False},
        'method': {'type': 'string'}, 'payment_date': DATE, 'note': {'type': 'string'}}},
    handler=pay_employee, roles=ADMIN, writes=True, dedupe=True))


# --- resellers ------------------------------------------------------------------------

def _reseller_money(suffix, default_desc):
    def handler(ctx, args):
        rid = _id(args, 'reseller_id')
        body = {'amount': amount_arg(args, 'amount'),
                'description': str_arg(args, 'description', max_len=200) or default_desc}
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
    description='Record money received from a reseller.',
    input_schema={'type': 'object', 'required': ['reseller_id', 'amount'], 'properties': _RESELLER_PROPS},
    handler=_reseller_money('collect_payment', 'Payment received'), roles=FINANCE, writes=True, dedupe=True))
```
Add `import re` at the top of `mcp_write_tools.py`.

Note: `add_expense`/`update_expense` return the endpoint's expense dict (`Expense.to_dict()`, which has `id`); cash entry / transfer return `to_dict()` (has `id`). If any test key (`d["expense"]["id"]`, `d["entry"]["id"]`, `d["transfer"]["id"]`, `d["payment"]["id"]`, `d["employee"]["balance"]`) is missing, read that model's `to_dict()` in `app.py` and fix the **test** to the real key.

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_mcp_write_finance.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_write_tools.py tests/test_mcp_write_finance.py
git commit -m "feat(mcp): expense, cash, transfer, supplier, payroll and reseller write tools"
```

---

### Task 7: Ticket tools, e2e, full MCP suite

**Files:**
- Modify: `mcp_write_tools.py` (append)
- Modify: `tests/test_mcp_write_customers.py` (tighten the employee test)
- Test: `tests/test_mcp_write_tickets.py`

**Interfaces:**
- Produces: tools `create_ticket`, `update_ticket`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_mcp_write_tickets.py`:

```python
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import (call_tool, connect, enable_connector, enable_writes, mcp, tool_data,
                               tool_error_text)


def _setup(client):
    hdr = make_tenant(client, "WT Biz", "wt_admin")
    enable_connector("wt_admin")
    enable_writes("wt_admin")
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "Basic", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    tok, _, _ = connect(client, hdr)
    cid = tool_data(call_tool(client, tok, "create_customer", {
        "name": "Tick", "phone": "03444555", "subscription_plan_id": plan}))["customer"]["id"]
    return hdr, tok, cid


def test_employee_opens_and_updates_ticket(client):
    import app as appmod
    _, _, cid = _setup(client)
    emp = auth_headers(client, username="wt_emp", role="employee")
    tok, _, _ = connect(client, emp)
    d = tool_data(call_tool(client, tok, "create_ticket", {"customer_id": cid, "title": "No internet"}))
    tid = d["ticket"]["id"]
    t = appmod.db.session.get(appmod.SupportTicket, tid)
    assert t.priority == "medium" and t.status == "open"
    d = tool_data(call_tool(client, tok, "update_ticket", {"ticket_id": tid, "status": "resolved"}))
    assert d["ticket"]["status"] == "resolved"
    assert "must be one of" in tool_error_text(call_tool(client, tok, "update_ticket",
                                                         {"ticket_id": tid, "status": "done"}))


def test_write_through_mcp_end_to_end_and_switch_off_again(client):
    import app as appmod
    hdr, tok, cid = _setup(client)
    r = mcp(client, tok, "tools/call", {"name": "create_ticket",
                                         "arguments": {"customer_id": cid, "title": "Slow"}})
    assert r.status_code == 200 and r.get_json()["result"]["isError"] is False
    row = appmod.McpToolCall.query.filter_by(tool="create_ticket").one()
    assert row.wrote is True and row.ok is True
    client.put("/api/mcp/settings", headers=hdr, json={"writes_enabled": False})
    names = {t["name"] for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert "create_ticket" not in names and "search_customers" in names
    assert "turned off" in tool_error_text(call_tool(client, tok, "create_ticket",
                                                     {"customer_id": cid, "title": "Again"}))
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_mcp_write_tickets.py -q` → FAIL.

- [ ] **Step 3: Append to `mcp_write_tools.py`:**

```python
# --- tickets -------------------------------------------------------------------------

STATUSES = ('open', 'in_progress', 'resolved', 'closed')
PRIORITIES = ('low', 'medium', 'high')


def _ticket_after(ctx, tid):
    t = scoped(ctx, ctx.appmod.SupportTicket).filter_by(id=tid).first()
    return {'id': t.id, 'title': t.title, 'status': t.status, 'priority': t.priority,
            'customer_id': t.customer_id} if t else None


def create_ticket(ctx, args):
    body = {'customer_id': _cid(args), 'title': str_arg(args, 'title', required=True, max_len=200),
            'description': str_arg(args, 'description', max_len=2000) or '',
            'priority': enum_arg(args, 'priority', PRIORITIES, 'medium')}
    resp = call_api(ctx, '/api/support-tickets', method='POST', json=body)
    tid = resp.get('id') if isinstance(resp, dict) else None
    if tid is None:
        T = ctx.appmod.SupportTicket
        tid = scoped(ctx, T).filter_by(customer_id=body['customer_id']).order_by(T.id.desc()).first().id
    return {'result': 'Ticket opened.', 'ticket': _ticket_after(ctx, tid)}


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
```
Before running, confirm the status/priority sets against the code: `grep -n "'in_progress'\|'resolved'\|'closed'\|priority" app.py | sed -n 1,40p` and the ticket UI (`grep -rn "priority" frontend/src/components/*Ticket* frontend/src/components/Service* 2>/dev/null | head`). If the app uses another value (e.g. `urgent`), add it to the tuple.

- [ ] **Step 4: Tighten the employee test** — in `tests/test_mcp_write_customers.py::test_employee_cannot_see_or_use_desk_tools` replace the line `assert "create_customer" not in names` with:
```python
    assert "create_customer" not in names and "create_ticket" in names
```

- [ ] **Step 5: Run the whole MCP suite** — `python -m pytest tests/test_mcp_*.py -q` → all PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_write_tools.py tests/test_mcp_write_tickets.py tests/test_mcp_write_customers.py
git commit -m "feat(mcp): ticket write tools and end-to-end write test"
```

---

### Task 8: Admin toggle in the AI connector card

**Files:**
- Modify: `frontend/src/components/AIConnectorCard.js`
- Modify: `frontend/src/components/AIConnectorCard.test.js`

**Interfaces:**
- Consumes: `GET /api/mcp/grants` → `writes_enabled`, `is_admin`; `PUT /api/mcp/settings {writes_enabled}`.

- [ ] **Step 1: Write the failing tests** — in `AIConnectorCard.test.js` change the mock to `jest.mock('axios', () => ({ get: jest.fn(), delete: jest.fn(), put: jest.fn() }));`, add `axios.put.mockReset();` in `beforeEach`, add `writes_enabled: false,` to `LIST`, and add:

```javascript
test('admin can allow the AI to make changes', async () => {
    axios.get.mockResolvedValue({ data: LIST });
    axios.put.mockResolvedValue({ data: { writes_enabled: true } });
    render(<AIConnectorCard />);
    const toggle = await screen.findByRole('checkbox', { name: /allow the ai to make changes/i });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(axios.put).toHaveBeenCalledWith(
        '/api/mcp/settings', { writes_enabled: true }, expect.anything()));
    await waitFor(() => expect(toggle).toBeChecked());
});

test('non-admins see the setting but cannot change it', async () => {
    axios.get.mockResolvedValue({ data: { ...LIST, is_admin: false, writes_enabled: true } });
    render(<AIConnectorCard />);
    const toggle = await screen.findByRole('checkbox', { name: /allow the ai to make changes/i });
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify failure** — `cd frontend && npx react-scripts test --watchAll=false AIConnectorCard` → FAIL (no checkbox).

- [ ] **Step 3: Implement** in `AIConnectorCard.js`:
- Add `FormControlLabel, Switch` to the `@mui/material` import.
- Add the handler after `disconnect`:
```javascript
    const setWrites = async (on) => {
        try {
            const res = await axios.put('/api/mcp/settings', { writes_enabled: on }, auth());
            setData(d => ({ ...d, writes_enabled: res.data.writes_enabled }));
        } catch (err) {
            setError(err.response?.data?.msg || 'Could not change this setting.');
        }
    };
```
- Replace the sentence `Let Claude, ChatGPT or another AI app answer questions about your business (read-only).` with `Let Claude, ChatGPT or another AI app answer questions about your business{data.writes_enabled ? ' and make changes for you' : ' (read-only)'}.`
- Directly after the setup-hints `<Typography ... component="div" ...>` block, insert:
```javascript
            <FormControlLabel
                sx={{ mb: 1 }}
                control={<Switch checked={!!data.writes_enabled} disabled={!data.is_admin}
                                 onChange={e => setWrites(e.target.checked)} />}
                label="Allow the AI to make changes" />
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                When on, connected staff can add and edit customers, renew, take payments, record expenses,
                cash, supplier and salary payments, and manage tickets from their AI app, limited to what their
                role can do here. Deleting customers, bulk actions, day close and settings stay in ServiceBills.
                {!data.is_admin && ' Only an admin can change this.'}
            </Typography>
```

- [ ] **Step 4: Run tests** — `cd frontend && npx react-scripts test --watchAll=false AIConnectorCard` → PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AIConnectorCard.js frontend/src/components/AIConnectorCard.test.js
git commit -m "feat(mcp): admin toggle to let the AI connector make changes"
```

---

### Task 9: Full verification and docs

**Files:**
- Modify: `docs/superpowers/specs/2026-11-07-mcp-write-tools-design.md` (status line + `add_cash_entry` row)

- [ ] **Step 1: Run the full backend suite** — `python -m pytest -q -x` (run in background; it is long). Expected: no failures beyond any that also fail on `origin/main` (check a failure with `git stash; python -m pytest <that test>; git stash pop` before blaming this branch).
- [ ] **Step 2: Run the frontend suite** — `cd frontend && npx react-scripts test --watchAll=false` → PASS.
- [ ] **Step 3: Single migration head** — `FLASK_APP=app.py DATABASE_URL=sqlite:///$TEMP/heads2.db python -m flask db heads` → only `cc1af4ef875c (head)`.
- [ ] **Step 4: Update the spec** — change `Status:` to `implemented on feat/mcp-write-tools`; in the `add_cash_entry` row replace `direction (in \| out — mapped to the endpoint's sign convention)` with `date` and add the sentence "Cash entries are cash-in only (the endpoint takes a positive amount)." under the Expenses table; note that `delete_cash_entry`, `delete_transfer` and `delete_supplier_payment` are admin only (as their endpoints are).
- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-11-07-mcp-write-tools-design.md
git commit -m "docs: mark MCP write tools spec implemented"
```

After this the branch is ready for a PR; deploy needs `flask db upgrade` (runs at Render deploy). Post-deploy owner check: turn the toggle on for Delta Net, from Claude do a renew, a payment, an expense and a ticket, and check each in the web app.
