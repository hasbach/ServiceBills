# ServiceBills MCP Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an ISP's staff add ServiceBills as a custom connector in Claude.ai, ChatGPT, Gemini CLI, Cursor or any MCP client and ask read-only questions about their own business.

**Architecture:** Three new modules plugged into the existing Flask app:
- `mcp_oauth.py` is an OAuth 2.1 authorization server (discovery, dynamic client registration, PKCE, consent, token rotation).
- `mcp_server.py` is a stateless Streamable-HTTP JSON-RPC endpoint at `POST /mcp`, with a tool registry, role and module gating and an audit log.
- `mcp_tools.py` holds the read-only tools. Customer, ticket and alert tools query the DB directly, filtered by the grant's tenant. Finance, network-tree, reseller and upstream tools call the app's **own existing GET endpoints in-process**, as the signed-in user, so their numbers and permission checks are exactly what the web app shows.

The frontend gets a consent page (`/oauth/consent`) and an "AI Connector" Settings tab.

**Tech Stack:** Flask 3.1, Flask-SQLAlchemy 3.1, Flask-JWT-Extended 4.7, Flask-Limiter 4.1, Alembic, pytest; React (CRA, react-scripts 5), MUI, axios, Jest/Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-21-mcp-connector-design.md`

**Deviations from the spec (decided while planning):**
- Finance, network-tree, reseller and upstream tools wrap the existing GET endpoints in-process (`call_api`) instead of re-querying with `tenant_query`. This keeps totals identical to the web reports, which hold subtle rules for reverts, refunds, gratis and FX, and it reuses each endpoint's own role and module checks.
- `list_upstreams` needs the `upstream_sync` module, because `/api/upstream-providers` already requires it.
- `get_network_status` also returns the last 10 `NetworkAlertEvent` rows (the spec's "active alerts").
- The Settings "AI Connector" tab is admin-only, because Settings itself is admin-only today. Non-admin staff can revoke their own connections through `DELETE /api/mcp/grants/<id>`, and an admin can revoke anyone's.
- `McpGrant` gains `resource`, `code_used_at` and `prev_refresh_token_hash` (for code-replay and refresh-reuse detection).

## Global Constraints

- Read-only: no tool may write, send a message, or start a live device job.
- Cloud SaaS only; no on-prem changes.
- New paid module key: `ai_connector` (in `modules.PAID`, in the `pro` plan bundle).
- Tokens: 32-byte random URL-safe strings; only SHA-256 hex hashes are stored.
- Lifetimes: auth code 60 s, access token 1 h, refresh token 30 days (rotated on every use).
- PKCE `S256` is mandatory. Redirect URIs must be `https://…` or loopback `http://localhost` / `127.0.0.1` / `[::1]`, matched exactly.
- Every `/mcp` request re-reads user, tenant, tenant status and the module from the DB.
- `/mcp` rate limit: 60 requests/minute per access token. `/oauth/register`: 20/hour per IP.
- List results are capped at 50 rows, with a "narrow your filter" note when more match.
- Every tool result carries `as_of` in the business timezone (`app._zone_for`).
- Customer balance sign: **negative = owes money**, positive = credit.
- Supported MCP protocol versions: `2025-11-25`, `2025-06-18`, `2025-03-26` (newest first).
- Alembic revision id `9af8c7a2648e`, `down_revision = '18c24f5699dc'` (the current single head). Re-check that it is still the single head before committing the migration.
- Follow the `register_*_routes(app, appmod)` module pattern (see `whatsapp_inbox_routes.py`); don't grow `app.py` beyond models, registry lists and the registration lines.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run the backend tests with `python -m pytest`, from the repo root.

## File Structure

| File | Responsibility |
|---|---|
| `modules.py`, `plans.py` | `ai_connector` module key and plan bundle |
| `app.py` | `McpClient`, `McpGrant`, `McpToolCall` models; tenant registries; CORS; registration lines |
| `migrations/versions/9af8c7a2648e_add_mcp_connector_tables.py` | The three tables |
| `mcp_oauth.py` | Token helpers + all OAuth routes + connection-management API |
| `mcp_server.py` | `/mcp` endpoint, auth, JSON-RPC, tool registry, gating, audit |
| `mcp_tools.py` | Tool handlers + `call_api` in-process helper |
| `tests/mcp_helpers.py` | Shared OAuth/MCP test helpers |
| `tests/test_mcp_*.py` | Tests per task |
| `frontend/src/components/OAuthConsentView.js` (+ `oauthConsent.js` helper, test) | Consent page |
| `frontend/src/components/AIConnectorCard.js` (+ test) | Settings tab content |
| `frontend/src/App.js`, `SettingsView.js`, `SuperAdminView.js` | Wiring |

---

### Task 1: `ai_connector` module key

**Files:**
- Modify: `modules.py:11` (`PAID`)
- Modify: `plans.py` (`pro` bundle)
- Modify: `frontend/src/components/SuperAdminView.js` (`PAID_MODULES`)
- Test: `tests/test_modules.py`

**Interfaces:**
- Produces: module key string `'ai_connector'`, usable with `modules.is_enabled(tenant, 'ai_connector')` and `Tenant.module_overrides = {'ai_connector': True}`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_modules.py`)

```python
def test_ai_connector_is_paid_and_in_pro_only():
    assert "ai_connector" in modules.PAID
    assert "ai_connector" in modules.enabled_for(T("pro"))
    assert "ai_connector" not in modules.enabled_for(T("free"))
    assert "ai_connector" in modules.enabled_for(T("free", {"ai_connector": True}))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_modules.py -v`
Expected: `test_ai_connector_is_paid_and_in_pro_only` FAILS (`assert 'ai_connector' in frozenset(...)`).

- [ ] **Step 3: Implement**

`modules.py` line 11:
```python
PAID = frozenset({"whatsapp", "ai_cs", "network", "upstream_sync", "whish_payments", "ai_connector"})
```

`plans.py`, the `pro` entry:
```python
        "modules": ("whatsapp", "ai_cs", "network", "upstream_sync", "whish_payments", "ai_connector"),
```

`frontend/src/components/SuperAdminView.js`, the `PAID_MODULES` array: add after the `whish_payments` line:
```js
    { key: 'ai_connector', label: 'AI connector (Claude / ChatGPT)' },
```

- [ ] **Step 4: Run the module tests and the gating tests**

Run: `python -m pytest tests/test_modules.py tests/test_admin_modules.py tests/test_gating.py -v`
Expected: all PASS (`test_pro_bundle_is_everything` keeps passing because pro now has every PAID key).

- [ ] **Step 5: Commit**

```bash
git add modules.py plans.py frontend/src/components/SuperAdminView.js tests/test_modules.py
git commit -m "feat(modules): add ai_connector paid module (pro bundle)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Data model and migration

**Files:**
- Modify: `app.py`: add models immediately before `class WhatsAppConversation(db.Model):` (≈ line 2137); extend `TENANT_OWNED_MODELS` (≈ line 2326), `_TENANT_DELETE_ORDER` and `_EXPORT_COLUMN_DENYLIST`
- Create: `migrations/versions/9af8c7a2648e_add_mcp_connector_tables.py`
- Test: `tests/test_mcp_models.py`

**Interfaces:**
- Produces: `app.McpClient(client_id, client_name, redirect_uris, created_at)`; `app.McpGrant(...)` with the columns below and `to_dict(client_name)`; `app.McpToolCall(...)`. All tables are `mcp_client`, `mcp_grant`, `mcp_tool_call`.

- [ ] **Step 1: Write the failing tests** (`tests/test_mcp_models.py`)

```python
"""Models + migration for the MCP connector. The migration is run directly
through Alembic Operations on a throwaway SQLite file, like
tests/test_inbox_migration.py (the full chain can't be walked on SQLite)."""
import importlib.util
import os
from datetime import datetime, timedelta

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.conftest import make_tenant

MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "migrations", "versions", "9af8c7a2648e_add_mcp_connector_tables.py")


def _load():
    spec = importlib.util.spec_from_file_location("mcp_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mcp_migration_up_and_down(tmp_path):
    mod = _load()
    assert mod.down_revision == "18c24f5699dc"
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE tenant (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
        ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
        with Operations.context(ctx):
            mod.upgrade()
            mod.upgrade()  # idempotent: guarded by inspect()
        insp = sa.inspect(conn)
        assert {"mcp_client", "mcp_grant", "mcp_tool_call"} <= set(insp.get_table_names())
        grant_cols = {c["name"] for c in insp.get_columns("mcp_grant")}
        assert {"access_token_hash", "refresh_token_hash", "prev_refresh_token_hash",
                "code_hash", "code_challenge", "code_used_at", "revoked_at"} <= grant_cols
        with Operations.context(ctx):
            mod.downgrade()
        insp = sa.inspect(conn)
        assert not ({"mcp_client", "mcp_grant", "mcp_tool_call"} & set(insp.get_table_names()))


def test_mcp_models_registered_and_serialize(app, client):
    import app as appmod
    make_tenant(client, "Biz MCP", "mcp_admin")
    user = appmod.User.query.filter_by(username="mcp_admin").first()
    appmod.db.session.add(appmod.McpClient(client_id="cid1", client_name="Claude",
                                           redirect_uris=["https://claude.ai/cb"]))
    g = appmod.McpGrant(tenant_id=user.tenant_id, user_id=user.id, client_id="cid1",
                        access_token_hash="a" * 64, access_expires_at=datetime.utcnow() + timedelta(hours=1))
    appmod.db.session.add(g)
    appmod.db.session.commit()
    d = g.to_dict("Claude")
    assert d["client_name"] == "Claude" and d["username"] == "mcp_admin"
    assert "access_token_hash" not in d and "refresh_token_hash" not in d
    assert appmod.McpGrant in appmod.TENANT_OWNED_MODELS
    assert appmod.McpToolCall in appmod.TENANT_OWNED_MODELS
    order = appmod._TENANT_DELETE_ORDER
    assert order.index(appmod.McpToolCall) < order.index(appmod.McpGrant)
    assert "access_token_hash" in appmod._EXPORT_COLUMN_DENYLIST[appmod.McpGrant]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_mcp_models.py -v`
Expected: FAIL (migration file missing; `AttributeError: module 'app' has no attribute 'McpClient'`).

- [ ] **Step 3: Add the models to `app.py`** (immediately before `class WhatsAppConversation(db.Model):`)

```python
class McpClient(db.Model):
    """An AI app (Claude, ChatGPT, ...) registered through OAuth dynamic client
    registration for the MCP connector. Not tenant-scoped: one registration is
    shared by every tenant whose staff connect that app. See
    docs/superpowers/specs/2026-10-21-mcp-connector-design.md."""
    __tablename__ = 'mcp_client'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    client_name = db.Column(db.String(200), nullable=False)
    redirect_uris = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class McpGrant(db.Model):
    """One user's consent for one AI app on their tenant. Holds the pending
    auth code, then the current access/refresh token pair -- SHA-256 hashes
    only, never the tokens themselves. prev_refresh_token_hash is kept so a
    replayed (already-rotated) refresh token can be detected and the whole
    grant revoked."""
    __tablename__ = 'mcp_grant'
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    client_id = db.Column(db.String(64), nullable=False, index=True)
    scope = db.Column(db.String(100), nullable=False, default='read')
    resource = db.Column(db.String(300), nullable=True)
    code_hash = db.Column(db.String(64), nullable=True, unique=True, index=True)
    code_challenge = db.Column(db.String(128), nullable=True)
    redirect_uri = db.Column(db.String(500), nullable=True)
    code_expires_at = db.Column(db.DateTime, nullable=True)
    code_used_at = db.Column(db.DateTime, nullable=True)
    access_token_hash = db.Column(db.String(64), nullable=True, unique=True, index=True)
    access_expires_at = db.Column(db.DateTime, nullable=True)
    refresh_token_hash = db.Column(db.String(64), nullable=True, unique=True, index=True)
    prev_refresh_token_hash = db.Column(db.String(64), nullable=True, index=True)
    refresh_expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User')

    def to_dict(self, client_name=None):
        def z(dt):
            return dt.strftime('%Y-%m-%dT%H:%M:%SZ') if dt else None
        return {'id': self.id, 'client_id': self.client_id, 'client_name': client_name,
                'username': self.user.username if self.user else None,
                'role': self.user.role if self.user else None,
                'created_at': z(self.created_at), 'last_used_at': z(self.last_used_at)}


class McpToolCall(db.Model):
    """Audit row for every MCP tools/call (success or failure)."""
    __tablename__ = 'mcp_tool_call'
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    grant_id = db.Column(db.Integer, db.ForeignKey('mcp_grant.id'), nullable=False, index=True)
    tool = db.Column(db.String(64), nullable=False)
    args_summary = db.Column(db.String(500), nullable=True)
    ok = db.Column(db.Boolean, nullable=False, default=True)
    error = db.Column(db.String(300), nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
```

- [ ] **Step 4: Register them in the tenant lists in `app.py`**

`TENANT_OWNED_MODELS`: change the last line to:
```python
    WhatsAppConversation, WhatsAppMessage, BalanceLog, WhatsAppConnectionEvent,
    McpGrant, McpToolCall,
)
```

`_TENANT_DELETE_ORDER`: insert as the very first entries (they hold FKs to `user`, and `mcp_tool_call` holds one to `mcp_grant`):
```python
_TENANT_DELETE_ORDER = [
    # MCP connector rows hold FKs to user (and tool calls to mcp_grant), so
    # they go first: tool calls, then grants.
    McpToolCall, McpGrant,
    # First: it holds FKs to customer, network_device, user and
```
(keep the existing `NetworkWriteAudit` comment and entries unchanged after it).

`_EXPORT_COLUMN_DENYLIST`: add an entry:
```python
    # Bearer-secret hashes for the MCP connector -- same class of secret as
    # NetworkAgent.token_hash above.
    McpGrant: {"code_hash", "code_challenge", "access_token_hash",
               "refresh_token_hash", "prev_refresh_token_hash"},
```

- [ ] **Step 5: Write the migration** (`migrations/versions/9af8c7a2648e_add_mcp_connector_tables.py`)

```python
"""mcp connector: OAuth clients, grants, tool-call audit

Revision ID: 9af8c7a2648e
Revises: 18c24f5699dc
Create Date: 2026-10-21

New tables only -- nothing existing is altered, so no lock_timeout retry is
needed. Each create is guarded with inspect() because production's real schema
has drifted from the migration history before. See
docs/superpowers/specs/2026-10-21-mcp-connector-design.md.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '9af8c7a2648e'
down_revision = '18c24f5699dc'
branch_labels = None
depends_on = None


def upgrade():
    existing = set(inspect(op.get_bind()).get_table_names())
    if 'mcp_client' not in existing:
        op.create_table(
            'mcp_client',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('client_id', sa.String(64), nullable=False),
            sa.Column('client_name', sa.String(200), nullable=False),
            sa.Column('redirect_uris', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_mcp_client_client_id', 'mcp_client', ['client_id'], unique=True)
    if 'mcp_grant' not in existing:
        op.create_table(
            'mcp_grant',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('client_id', sa.String(64), nullable=False),
            sa.Column('scope', sa.String(100), nullable=False),
            sa.Column('resource', sa.String(300), nullable=True),
            sa.Column('code_hash', sa.String(64), nullable=True),
            sa.Column('code_challenge', sa.String(128), nullable=True),
            sa.Column('redirect_uri', sa.String(500), nullable=True),
            sa.Column('code_expires_at', sa.DateTime(), nullable=True),
            sa.Column('code_used_at', sa.DateTime(), nullable=True),
            sa.Column('access_token_hash', sa.String(64), nullable=True),
            sa.Column('access_expires_at', sa.DateTime(), nullable=True),
            sa.Column('refresh_token_hash', sa.String(64), nullable=True),
            sa.Column('prev_refresh_token_hash', sa.String(64), nullable=True),
            sa.Column('refresh_expires_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('last_used_at', sa.DateTime(), nullable=True),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_mcp_grant_tenant_id', 'mcp_grant', ['tenant_id'])
        op.create_index('ix_mcp_grant_user_id', 'mcp_grant', ['user_id'])
        op.create_index('ix_mcp_grant_client_id', 'mcp_grant', ['client_id'])
        op.create_index('ix_mcp_grant_code_hash', 'mcp_grant', ['code_hash'], unique=True)
        op.create_index('ix_mcp_grant_access_token_hash', 'mcp_grant', ['access_token_hash'], unique=True)
        op.create_index('ix_mcp_grant_refresh_token_hash', 'mcp_grant', ['refresh_token_hash'], unique=True)
        op.create_index('ix_mcp_grant_prev_refresh_token_hash', 'mcp_grant', ['prev_refresh_token_hash'])
    if 'mcp_tool_call' not in existing:
        op.create_table(
            'mcp_tool_call',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('grant_id', sa.Integer(), sa.ForeignKey('mcp_grant.id'), nullable=False),
            sa.Column('tool', sa.String(64), nullable=False),
            sa.Column('args_summary', sa.String(500), nullable=True),
            sa.Column('ok', sa.Boolean(), nullable=False),
            sa.Column('error', sa.String(300), nullable=True),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_mcp_tool_call_tenant_id', 'mcp_tool_call', ['tenant_id'])
        op.create_index('ix_mcp_tool_call_grant_id', 'mcp_tool_call', ['grant_id'])
        op.create_index('ix_mcp_tool_call_created_at', 'mcp_tool_call', ['created_at'])


def downgrade():
    existing = set(inspect(op.get_bind()).get_table_names())
    for table in ('mcp_tool_call', 'mcp_grant', 'mcp_client'):
        if table in existing:
            op.drop_table(table)
```

- [ ] **Step 6: Check single head and run tests**

Run:
```bash
python - <<'EOF'
from alembic.config import Config
from alembic.script import ScriptDirectory
cfg = Config(); cfg.set_main_option("script_location", "migrations")
print(ScriptDirectory.from_config(cfg).get_heads())
EOF
python -m pytest tests/test_mcp_models.py tests/test_lifecycle.py -v
```
Expected: `['9af8c7a2648e']` (exactly one head); all tests PASS. If another head appeared on `main` meanwhile, set `down_revision` to it and update the test's assertion.

- [ ] **Step 7: Commit**

```bash
git add app.py migrations/versions/9af8c7a2648e_add_mcp_connector_tables.py tests/test_mcp_models.py
git commit -m "feat(mcp): McpClient/McpGrant/McpToolCall models and migration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: OAuth discovery, client registration, CORS

**Files:**
- Create: `mcp_oauth.py`
- Modify: `app.py:100` (CORS resources); `app.py` registration block after `customer_import.register_customer_import_routes(...)` (≈ line 10362)
- Create: `tests/mcp_helpers.py`
- Test: `tests/test_mcp_oauth_discovery.py`

**Interfaces:**
- Produces in `mcp_oauth.py`:
  - constants `ACCESS_TTL`, `REFRESH_TTL`, `CODE_TTL`, `SCOPE = 'read'`, `AUTHORIZE_KEYS`
  - `new_token() -> str`, `hash_token(token: str) -> str`, `pkce_s256(verifier: str) -> str`
  - `origin() -> str` (request origin, no trailing slash), `valid_redirect_uri(uri: str) -> bool`
  - `oauth_error(code: str, description: str, status: int) -> Response`
  - `register_mcp_oauth_routes(app, appmod)` (Tasks 4, 5 and 10 add more routes inside this function)

- [ ] **Step 1: Write the shared test helpers** (`tests/mcp_helpers.py`)

```python
"""Shared helpers for the MCP connector tests."""
import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlparse

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "http://localhost/mcp"


def enable_connector(username):
    """Switch the ai_connector module on for `username`'s tenant."""
    from app import db, Tenant, User
    user = User.query.filter_by(username=username).first()
    tenant = db.session.get(Tenant, user.tenant_id)
    tenant.module_overrides = {**(tenant.module_overrides or {}), "ai_connector": True}
    db.session.commit()
    return tenant


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def register_client(client, name="Claude", redirect=REDIRECT):
    r = client.post("/oauth/register", json={"client_name": name, "redirect_uris": [redirect]})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["client_id"]


def authorize_params(client_id, challenge, redirect=REDIRECT, state="st8"):
    return {"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
            "scope": "read", "resource": RESOURCE}


def approve(client, jwt_headers, params, decision="allow"):
    return client.post("/api/mcp/oauth/approve", headers=jwt_headers,
                       json={**params, "decision": decision})


def query_of(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def exchange(client, code, client_id, verifier, redirect=REDIRECT):
    return client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": redirect, "code_verifier": verifier})


def connect(client, jwt_headers):
    """Full OAuth dance as the user behind jwt_headers.
    Returns (access_token, refresh_token, client_id)."""
    cid = register_client(client)
    verifier, challenge = pkce_pair()
    r = approve(client, jwt_headers, authorize_params(cid, challenge))
    assert r.status_code == 200, r.get_data(as_text=True)
    code = query_of(r.get_json()["redirect_url"])["code"]
    t = exchange(client, code, cid, verifier)
    assert t.status_code == 200, t.get_data(as_text=True)
    j = t.get_json()
    return j["access_token"], j["refresh_token"], cid


def mcp(client, token, method, params=None, msg_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    h = {"Authorization": f"Bearer {token}", **(headers or {})}
    return client.post("/mcp", json=body, headers=h)


def call_tool(client, token, name, arguments=None):
    r = mcp(client, token, "tools/call", {"name": name, "arguments": arguments or {}})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["result"]


def tool_data(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def tool_error_text(result):
    assert result["isError"] is True, result
    return result["content"][0]["text"]
```

- [ ] **Step 2: Write the failing tests** (`tests/test_mcp_oauth_discovery.py`)

```python
from mcp_oauth import hash_token, new_token, pkce_s256, valid_redirect_uri


def test_token_helpers():
    t = new_token()
    assert len(t) >= 43 and new_token() != t
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    # RFC 7636 appendix B test vector
    assert pkce_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_redirect_uri_rules():
    assert valid_redirect_uri("https://claude.ai/api/mcp/auth_callback")
    assert valid_redirect_uri("http://localhost:6274/oauth/callback")
    assert valid_redirect_uri("http://127.0.0.1:33418/callback")
    assert not valid_redirect_uri("http://evil.example/cb")
    assert not valid_redirect_uri("https://claude.ai/cb#frag")
    assert not valid_redirect_uri("javascript:alert(1)")
    assert not valid_redirect_uri("")


def test_protected_resource_metadata(client):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        j = client.get(path).get_json()
        assert j["resource"] == "http://localhost/mcp"
        assert j["authorization_servers"] == ["http://localhost"]


def test_authorization_server_metadata(client):
    j = client.get("/.well-known/oauth-authorization-server").get_json()
    assert j["issuer"] == "http://localhost"
    assert j["authorization_endpoint"] == "http://localhost/oauth/authorize"
    assert j["token_endpoint"] == "http://localhost/oauth/token"
    assert j["registration_endpoint"] == "http://localhost/oauth/register"
    assert j["code_challenge_methods_supported"] == ["S256"]
    assert j["token_endpoint_auth_methods_supported"] == ["none"]


def test_register_public_client(client):
    import app as appmod
    r = client.post("/oauth/register", json={
        "client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "token_endpoint_auth_method": "client_secret_post"})
    assert r.status_code == 201
    j = r.get_json()
    assert j["token_endpoint_auth_method"] == "none"   # replaced: public clients only
    assert "client_secret" not in j
    row = appmod.McpClient.query.filter_by(client_id=j["client_id"]).first()
    assert row.client_name == "Claude"
    assert row.redirect_uris == ["https://claude.ai/api/mcp/auth_callback"]


def test_register_rejects_bad_redirects(client):
    for uris in (None, [], ["http://evil.example/cb"], ["https://ok.example/cb"] * 11):
        r = client.post("/oauth/register", json={"client_name": "x", "redirect_uris": uris})
        assert r.status_code == 400
        assert r.get_json()["error"] == "invalid_redirect_uri"


def test_cors_on_discovery_and_token(client):
    r = client.get("/.well-known/oauth-authorization-server", headers={"Origin": "https://inspector.example"})
    assert r.headers.get("Access-Control-Allow-Origin") == "*"
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python -m pytest tests/test_mcp_oauth_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_oauth'`.

- [ ] **Step 4: Create `mcp_oauth.py`**

```python
"""OAuth 2.1 authorization server for the MCP connector -- see
docs/superpowers/specs/2026-10-21-mcp-connector-design.md. Registered from
app.py via register_mcp_oauth_routes(app, appmod) so app.py doesn't grow.

Public clients only (PKCE S256, no client secrets). Tokens are random
strings; only their SHA-256 hashes are stored (McpGrant)."""
import base64
import hashlib
import secrets
import time
from datetime import timedelta
from urllib.parse import urlparse

from flask import jsonify, request

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(seconds=60)
SCOPE = 'read'
AUTHORIZE_KEYS = ('client_id', 'redirect_uri', 'response_type', 'code_challenge',
                  'code_challenge_method', 'state', 'scope', 'resource')
_LOOPBACK = ('localhost', '127.0.0.1', '::1')


def new_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def pkce_s256(verifier):
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def origin():
    """This server's public origin as the client reached it. Behind Render
    (TRUST_PROXY=1 -> ProxyFix x_proto) this is the https URL."""
    return request.url_root.rstrip('/')


def valid_redirect_uri(uri):
    if not isinstance(uri, str) or not uri:
        return False
    try:
        p = urlparse(uri)
    except ValueError:
        return False
    if p.fragment:
        return False
    if p.scheme == 'https' and p.netloc:
        return True
    return p.scheme == 'http' and p.hostname in _LOOPBACK


def oauth_error(code, description, status=400):
    resp = jsonify(error=code, error_description=description)
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def register_mcp_oauth_routes(app, appmod):
    db = appmod.db
    Client = appmod.McpClient

    @app.route('/.well-known/oauth-protected-resource', methods=['GET'])
    @app.route('/.well-known/oauth-protected-resource/mcp', methods=['GET'])
    def mcp_protected_resource_metadata():
        o = origin()
        return jsonify({'resource': o + '/mcp', 'authorization_servers': [o],
                        'scopes_supported': [SCOPE], 'bearer_methods_supported': ['header'],
                        'resource_name': 'ServiceBills'})

    @app.route('/.well-known/oauth-authorization-server', methods=['GET'])
    def mcp_authorization_server_metadata():
        o = origin()
        return jsonify({
            'issuer': o,
            'authorization_endpoint': o + '/oauth/authorize',
            'token_endpoint': o + '/oauth/token',
            'registration_endpoint': o + '/oauth/register',
            'revocation_endpoint': o + '/oauth/revoke',
            'response_types_supported': ['code'],
            'grant_types_supported': ['authorization_code', 'refresh_token'],
            'code_challenge_methods_supported': ['S256'],
            'token_endpoint_auth_methods_supported': ['none'],
            'revocation_endpoint_auth_methods_supported': ['none'],
            'scopes_supported': [SCOPE],
        })

    @app.route('/oauth/register', methods=['POST'])
    @appmod.limiter.limit('20/hour')
    def mcp_oauth_register():
        data = request.get_json(silent=True) or {}
        uris = data.get('redirect_uris')
        if (not isinstance(uris, list) or not uris or len(uris) > 10
                or not all(isinstance(u, str) and len(u) <= 500 and valid_redirect_uri(u) for u in uris)):
            return oauth_error('invalid_redirect_uri',
                               'redirect_uris must be 1-10 https:// or loopback http:// URLs')
        name = str(data.get('client_name') or 'AI app').strip()[:200] or 'AI app'
        client = Client(client_id=secrets.token_urlsafe(24), client_name=name, redirect_uris=uris)
        db.session.add(client)
        db.session.commit()
        # RFC 7591 lets the server replace requested metadata: whatever auth
        # method was asked for, this is a public client with no secret.
        resp = jsonify({'client_id': client.client_id, 'client_name': name, 'redirect_uris': uris,
                        'grant_types': ['authorization_code', 'refresh_token'],
                        'response_types': ['code'], 'token_endpoint_auth_method': 'none',
                        'client_id_issued_at': int(time.time())})
        resp.status_code = 201
        resp.headers['Cache-Control'] = 'no-store'
        return resp
```

- [ ] **Step 5: CORS + registration in `app.py`**

Replace line 100:
```python
CORS(app, resources={
    r"/api/*": {"origins": Config.CORS_ORIGINS},
    # MCP connector: AI clients (including browser-based ones like MCP
    # Inspector) call discovery, registration, token and /mcp cross-origin.
    # Bearer tokens only, never cookies, so a wildcard origin is safe here.
    r"/.well-known/oauth-*": {"origins": "*"},
    r"/oauth/*": {"origins": "*"},
    r"/mcp": {"origins": "*", "expose_headers": ["WWW-Authenticate"]},
})
```

After the `customer_import.register_customer_import_routes(app, sys.modules[__name__])` line:
```python

import mcp_oauth
mcp_oauth.register_mcp_oauth_routes(app, sys.modules[__name__])
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_mcp_oauth_discovery.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add mcp_oauth.py app.py tests/mcp_helpers.py tests/test_mcp_oauth_discovery.py
git commit -m "feat(mcp): OAuth discovery metadata and dynamic client registration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Authorization request and consent API

**Files:**
- Modify: `mcp_oauth.py` (new helpers + routes inside `register_mcp_oauth_routes`)
- Test: `tests/test_mcp_oauth_authorize.py`

**Interfaces:**
- Consumes: Task 3 helpers.
- Produces:
  - `GET /oauth/authorize`: validates, then 302s to `/oauth/consent?<same params>`
  - `GET /api/mcp/oauth/consent-info?<authorize params>` (JWT) returns `{client_name, tenant_name, username, role, redirect_host}`
  - `POST /api/mcp/oauth/approve` (JWT), with JSON `{<authorize params>, decision: 'allow'|'deny'}`, returns `{redirect_url}`. On allow it creates an `McpGrant` holding the code.
  - `validate_authorize_params(params: dict, expected_resource: str) -> tuple[str, str] | None` (an `(error, description)` pair)

- [ ] **Step 1: Write the failing tests** (`tests/test_mcp_oauth_authorize.py`)

```python
from tests.conftest import make_tenant
from tests.mcp_helpers import (REDIRECT, approve, authorize_params, enable_connector,
                               pkce_pair, query_of, register_client)


def _setup(client, username="ox_admin"):
    hdr = make_tenant(client, "OX Biz", username)
    enable_connector(username)
    cid = register_client(client)
    _, challenge = pkce_pair()
    return hdr, cid, authorize_params(cid, challenge)


def test_authorize_redirects_to_consent_page(client):
    _, _, params = _setup(client)
    r = client.get("/oauth/authorize", query_string=params)
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert loc.startswith("/oauth/consent?")
    assert query_of(loc)["client_id"] == params["client_id"]


def test_authorize_unknown_client_or_redirect_is_not_redirected(client):
    _, _, params = _setup(client)
    r = client.get("/oauth/authorize", query_string={**params, "client_id": "nope"})
    assert r.status_code == 400 and "Location" not in r.headers
    r = client.get("/oauth/authorize", query_string={**params, "redirect_uri": "https://evil.example/cb"})
    assert r.status_code == 400 and "Location" not in r.headers


def test_authorize_without_pkce_redirects_back_with_error(client):
    _, _, params = _setup(client)
    bad = {k: v for k, v in params.items() if k != "code_challenge"}
    r = client.get("/oauth/authorize", query_string=bad)
    assert r.status_code == 302
    q = query_of(r.headers["Location"])
    assert r.headers["Location"].startswith(REDIRECT)
    assert q["error"] == "invalid_request" and q["state"] == "st8"


def test_authorize_wrong_resource_rejected(client):
    _, _, params = _setup(client)
    r = client.get("/oauth/authorize", query_string={**params, "resource": "https://other.example/mcp"})
    assert query_of(r.headers["Location"])["error"] == "invalid_target"


def test_consent_info(client):
    hdr, _, params = _setup(client)
    r = client.get("/api/mcp/oauth/consent-info", query_string=params, headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["client_name"] == "Claude" and j["tenant_name"] == "OX Biz"
    assert j["username"] == "ox_admin" and j["redirect_host"] == "claude.ai"


def test_consent_info_module_off(client):
    hdr = make_tenant(client, "Off Biz", "off_admin")   # free plan, no override
    cid = register_client(client)
    _, challenge = pkce_pair()
    r = client.get("/api/mcp/oauth/consent-info", query_string=authorize_params(cid, challenge), headers=hdr)
    assert r.status_code == 403
    assert "not part of this business's plan" in r.get_json()["error"]


def test_approve_allow_creates_grant_with_code(client):
    import app as appmod
    hdr, cid, params = _setup(client)
    r = approve(client, hdr, params)
    assert r.status_code == 200
    q = query_of(r.get_json()["redirect_url"])
    assert q["state"] == "st8" and q["iss"] == "http://localhost" and q["code"]
    g = appmod.McpGrant.query.one()
    assert g.client_id == cid and g.code_challenge == params["code_challenge"]
    assert g.access_token_hash is None and g.code_hash != q["code"]   # hash, not the code


def test_approve_deny(client):
    import app as appmod
    hdr, _, params = _setup(client)
    r = approve(client, hdr, params, decision="deny")
    q = query_of(r.get_json()["redirect_url"])
    assert q["error"] == "access_denied" and q["state"] == "st8"
    assert appmod.McpGrant.query.count() == 0


def test_approve_requires_login(client):
    _, _, params = _setup(client)
    r = client.post("/api/mcp/oauth/approve", json={**params, "decision": "allow"})
    assert r.status_code == 401
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_mcp_oauth_authorize.py -v`
Expected: FAIL (404 on `/oauth/authorize`).

- [ ] **Step 3: Add module-level helpers to `mcp_oauth.py`** (below `oauth_error`)

```python
import re
from urllib.parse import urlencode

_CHALLENGE_RE = re.compile(r'^[A-Za-z0-9_-]{43,128}$')


def with_query(uri, params):
    """Append params to a redirect URI that may already carry a query."""
    clean = {k: v for k, v in params.items() if v is not None}
    return uri + ('&' if '?' in uri else '?') + urlencode(clean)


def validate_authorize_params(params, expected_resource):
    """(error, description) for a bad authorization request, else None.
    Assumes client_id and redirect_uri were already checked."""
    if params.get('response_type') != 'code':
        return 'unsupported_response_type', 'response_type must be "code"'
    if params.get('code_challenge_method') != 'S256' or not _CHALLENGE_RE.match(
            str(params.get('code_challenge') or '')):
        return 'invalid_request', 'PKCE with code_challenge_method=S256 is required'
    resource = params.get('resource')
    if resource and str(resource).rstrip('/') != expected_resource:
        return 'invalid_target', 'This server only issues tokens for ' + expected_resource
    return None
```
(Move the `re` and `urlencode` imports up into the import block at the top of the file.)

- [ ] **Step 4: Add the routes inside `register_mcp_oauth_routes`** (after `mcp_oauth_register`)

```python
    Grant = appmod.McpGrant

    def _client_and_redirect(params):
        """(client, error message) -- client_id/redirect_uri must match a
        registration exactly; errors here are never redirected."""
        client = Client.query.filter_by(client_id=str(params.get('client_id') or '')).first()
        if client is None:
            return None, 'Unknown AI app (client_id). Remove the connector in your AI app and add it again.'
        if params.get('redirect_uri') not in (client.redirect_uris or []):
            return None, "redirect_uri doesn't match the one this AI app registered."
        return client, None

    def _consenting_user():
        """(user, tenant, error message) for the logged-in ServiceBills user."""
        user = appmod.User.query.filter_by(username=get_jwt_identity()).first()
        if user is None or user.tenant_id is None:
            return None, None, "Sign in with your business account to connect an AI app."
        tenant = db.session.get(appmod.Tenant, user.tenant_id)
        if tenant is None or tenant.status != 'active':
            return None, None, "This business's subscription is inactive."
        if not modules.is_enabled(tenant, 'ai_connector'):
            return None, None, ("The AI connector is not part of this business's plan. "
                                "Ask ServiceBills to enable it.")
        return user, tenant, None

    @app.route('/oauth/authorize', methods=['GET'])
    def mcp_oauth_authorize():
        params = {k: request.args.get(k) for k in AUTHORIZE_KEYS}
        client, problem = _client_and_redirect(params)
        if problem:
            return Response(problem, status=400, mimetype='text/plain')
        bad = validate_authorize_params(params, origin() + '/mcp')
        if bad:
            return redirect(with_query(params['redirect_uri'], {
                'error': bad[0], 'error_description': bad[1],
                'state': params.get('state'), 'iss': origin()}))
        return redirect('/oauth/consent?' + urlencode({k: v for k, v in params.items() if v is not None}))

    @app.route('/api/mcp/oauth/consent-info', methods=['GET'])
    @jwt_required()
    def mcp_oauth_consent_info():
        params = {k: request.args.get(k) for k in AUTHORIZE_KEYS}
        client, problem = _client_and_redirect(params)
        if not problem:
            bad = validate_authorize_params(params, origin() + '/mcp')
            problem = bad[1] if bad else None
        if problem:
            return jsonify(error=problem), 400
        user, tenant, problem = _consenting_user()
        if problem:
            return jsonify(error=problem), 403
        return jsonify(client_name=client.client_name, tenant_name=tenant.name,
                       username=user.username, role=user.role,
                       redirect_host=urlparse(params['redirect_uri']).netloc)

    @app.route('/api/mcp/oauth/approve', methods=['POST'])
    @jwt_required()
    def mcp_oauth_approve():
        data = request.get_json(silent=True) or {}
        params = {k: (str(data[k]) if data.get(k) is not None else None) for k in AUTHORIZE_KEYS}
        client, problem = _client_and_redirect(params)
        if not problem:
            bad = validate_authorize_params(params, origin() + '/mcp')
            problem = bad[1] if bad else None
        if problem:
            return jsonify(error=problem), 400
        if data.get('decision') != 'allow':
            return jsonify(redirect_url=with_query(params['redirect_uri'], {
                'error': 'access_denied', 'state': params.get('state'), 'iss': origin()}))
        user, tenant, problem = _consenting_user()
        if problem:
            return jsonify(error=problem), 403
        code = new_token()
        db.session.add(Grant(
            tenant_id=tenant.id, user_id=user.id, client_id=client.client_id, scope=SCOPE,
            resource=params.get('resource'), code_hash=hash_token(code),
            code_challenge=params['code_challenge'], redirect_uri=params['redirect_uri'],
            code_expires_at=datetime.utcnow() + CODE_TTL))
        db.session.commit()
        return jsonify(redirect_url=with_query(params['redirect_uri'], {
            'code': code, 'state': params.get('state'), 'iss': origin()}))
```

Extend the imports at the top of `mcp_oauth.py`:
```python
from datetime import datetime, timedelta
from flask import Response, jsonify, redirect, request
from flask_jwt_extended import get_jwt_identity, jwt_required

import modules
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_mcp_oauth_authorize.py tests/test_mcp_oauth_discovery.py -v`
Expected: all PASS. (The `/api/...` routes go through `_block_suspended_tenants`, which returns 402 for a suspended tenant before our own check. That's fine.)

- [ ] **Step 6: Commit**

```bash
git add mcp_oauth.py tests/test_mcp_oauth_authorize.py
git commit -m "feat(mcp): OAuth authorize endpoint and consent API

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Token exchange, refresh rotation, revocation

**Files:**
- Modify: `mcp_oauth.py`
- Test: `tests/test_mcp_oauth_token.py`

**Interfaces:**
- Produces:
  - `POST /oauth/token` (form or JSON) with `grant_type=authorization_code|refresh_token`, returning `{access_token, token_type:'Bearer', expires_in:3600, refresh_token, scope:'read'}`
  - `POST /oauth/revoke` with `token=…`, always returning 200
  - module function `revoke_grant(grant, now) -> None` (Task 10 reuses it)

- [ ] **Step 1: Write the failing tests** (`tests/test_mcp_oauth_token.py`)

```python
from datetime import datetime, timedelta

from tests.conftest import make_tenant
from tests.mcp_helpers import (approve, authorize_params, connect, enable_connector, exchange,
                               pkce_pair, query_of, register_client)


def _code(client, username="tk_admin"):
    hdr = make_tenant(client, "TK Biz", username)
    enable_connector(username)
    cid = register_client(client)
    verifier, challenge = pkce_pair()
    r = approve(client, hdr, authorize_params(cid, challenge))
    return hdr, cid, verifier, query_of(r.get_json()["redirect_url"])["code"]


def test_code_exchange_success(client):
    import app as appmod
    _, cid, verifier, code = _code(client)
    r = exchange(client, code, cid, verifier)
    assert r.status_code == 200
    j = r.get_json()
    assert j["token_type"] == "Bearer" and j["expires_in"] == 3600 and j["scope"] == "read"
    assert r.headers["Cache-Control"] == "no-store"
    g = appmod.McpGrant.query.one()
    assert g.code_used_at is not None and g.access_token_hash and g.access_token_hash != j["access_token"]


def test_wrong_verifier_rejected(client):
    _, cid, _, code = _code(client)
    other_verifier, _ = pkce_pair()
    r = exchange(client, code, cid, other_verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"


def test_code_is_single_use_and_replay_revokes(client):
    import app as appmod
    _, cid, verifier, code = _code(client)
    assert exchange(client, code, cid, verifier).status_code == 200
    r = exchange(client, code, cid, verifier)
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_grant"
    assert appmod.McpGrant.query.one().revoked_at is not None


def test_expired_code_rejected(client):
    import app as appmod
    _, cid, verifier, code = _code(client)
    g = appmod.McpGrant.query.one()
    g.code_expires_at = datetime.utcnow() - timedelta(seconds=1)
    appmod.db.session.commit()
    assert exchange(client, code, cid, verifier).status_code == 400


def test_wrong_client_or_redirect_rejected(client):
    _, cid, verifier, code = _code(client)
    assert exchange(client, code, "other-client", verifier).status_code == 400
    assert exchange(client, code, cid, verifier, redirect="https://claude.ai/other").status_code == 400


def test_refresh_rotates_and_reuse_revokes(client):
    import app as appmod
    hdr = make_tenant(client, "RF Biz", "rf_admin")
    enable_connector("rf_admin")
    access, refresh, cid = connect(client, hdr)
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
    assert r.status_code == 200
    new = r.get_json()
    assert new["refresh_token"] != refresh and new["access_token"] != access
    # replaying the rotated refresh token revokes the whole grant
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
    assert r.status_code == 400
    assert appmod.McpGrant.query.one().revoked_at is not None
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": new["refresh_token"], "client_id": cid})
    assert r.status_code == 400


def test_revoke_endpoint(client):
    import app as appmod
    hdr = make_tenant(client, "RV Biz", "rv_admin")
    enable_connector("rv_admin")
    access, _, _ = connect(client, hdr)
    assert client.post("/oauth/revoke", data={"token": access}).status_code == 200
    assert appmod.McpGrant.query.one().revoked_at is not None
    assert client.post("/oauth/revoke", data={"token": "garbage"}).status_code == 200


def test_unsupported_grant_type(client):
    r = client.post("/oauth/token", data={"grant_type": "password"})
    assert r.status_code == 400 and r.get_json()["error"] == "unsupported_grant_type"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_mcp_oauth_token.py -v`
Expected: FAIL (404 on `/oauth/token`).

- [ ] **Step 3: Implement**

Module-level, in `mcp_oauth.py` (add `import hmac` to the import block at the top):
```python
_VERIFIER_RE = re.compile(r'^[A-Za-z0-9\-._~]{43,128}$')


def revoke_grant(grant, now):
    """Kill a grant: clear live token hashes (so lookups fail even if a check
    is ever missed), keep code_hash/prev_refresh_token_hash for replay
    detection."""
    if grant.revoked_at is None:
        grant.revoked_at = now
    grant.access_token_hash = None
    grant.refresh_token_hash = None


def _issue_tokens(grant, now):
    access, refresh = new_token(), new_token()
    grant.access_token_hash = hash_token(access)
    grant.access_expires_at = now + ACCESS_TTL
    grant.prev_refresh_token_hash = grant.refresh_token_hash
    grant.refresh_token_hash = hash_token(refresh)
    grant.refresh_expires_at = now + REFRESH_TTL
    return access, refresh


def _token_response(access, refresh):
    resp = jsonify(access_token=access, token_type='Bearer',
                   expires_in=int(ACCESS_TTL.total_seconds()), refresh_token=refresh, scope=SCOPE)
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Pragma'] = 'no-cache'
    return resp
```

Inside `register_mcp_oauth_routes`:
```python
    def _exchange_code(f):
        now = datetime.utcnow()
        code = f.get('code') or ''
        grant = Grant.query.filter_by(code_hash=hash_token(code)).first() if code else None
        if grant is None:
            return oauth_error('invalid_grant', 'Unknown or expired authorization code')
        if grant.code_used_at is not None:
            # A replayed code revokes whatever it already issued (RFC 6749 4.1.2).
            revoke_grant(grant, now)
            db.session.commit()
            return oauth_error('invalid_grant', 'Authorization code already used')
        if grant.revoked_at is not None or grant.code_expires_at is None or grant.code_expires_at < now:
            return oauth_error('invalid_grant', 'Unknown or expired authorization code')
        if f.get('client_id') != grant.client_id:
            return oauth_error('invalid_grant', 'Code was issued to another client')
        if f.get('redirect_uri') and f.get('redirect_uri') != grant.redirect_uri:
            return oauth_error('invalid_grant', 'redirect_uri mismatch')
        verifier = f.get('code_verifier') or ''
        if not _VERIFIER_RE.match(verifier) or not hmac.compare_digest(
                pkce_s256(verifier), grant.code_challenge or ''):
            return oauth_error('invalid_grant', 'PKCE verification failed')
        grant.code_used_at = now
        access, refresh = _issue_tokens(grant, now)
        db.session.commit()
        return _token_response(access, refresh)

    def _refresh(f):
        now = datetime.utcnow()
        presented = f.get('refresh_token') or ''
        h = hash_token(presented) if presented else None
        grant = Grant.query.filter_by(refresh_token_hash=h).first() if h else None
        if grant is None:
            stale = Grant.query.filter_by(prev_refresh_token_hash=h).first() if h else None
            if stale is not None:
                # A rotated-out refresh token came back: treat as stolen.
                revoke_grant(stale, now)
                db.session.commit()
            return oauth_error('invalid_grant', 'Unknown refresh token')
        if (grant.revoked_at is not None or grant.refresh_expires_at is None
                or grant.refresh_expires_at < now
                or (f.get('client_id') and f.get('client_id') != grant.client_id)):
            return oauth_error('invalid_grant', 'Refresh token expired or revoked')
        user = db.session.get(appmod.User, grant.user_id)
        if user is None or user.tenant_id != grant.tenant_id:
            revoke_grant(grant, now)
            db.session.commit()
            return oauth_error('invalid_grant', 'This connection is no longer valid')
        access, refresh = _issue_tokens(grant, now)
        db.session.commit()
        return _token_response(access, refresh)

    @app.route('/oauth/token', methods=['POST'])
    @appmod.limiter.limit('60/minute')
    def mcp_oauth_token():
        f = request.form if request.form else (request.get_json(silent=True) or {})
        grant_type = f.get('grant_type')
        if grant_type == 'authorization_code':
            return _exchange_code(f)
        if grant_type == 'refresh_token':
            return _refresh(f)
        return oauth_error('unsupported_grant_type', 'Use authorization_code or refresh_token')

    @app.route('/oauth/revoke', methods=['POST'])
    def mcp_oauth_revoke():
        f = request.form if request.form else (request.get_json(silent=True) or {})
        token = f.get('token') or ''
        if token:
            h = hash_token(token)
            grant = Grant.query.filter(db.or_(Grant.access_token_hash == h,
                                              Grant.refresh_token_hash == h)).first()
            if grant is not None:
                revoke_grant(grant, datetime.utcnow())
                db.session.commit()
        return ('', 200)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_mcp_oauth_token.py tests/test_mcp_oauth_authorize.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_oauth.py tests/test_mcp_oauth_token.py
git commit -m "feat(mcp): token exchange with PKCE, refresh rotation, revocation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `/mcp` endpoint: auth, JSON-RPC, tool registry, gating, audit

**Files:**
- Create: `mcp_server.py`
- Modify: `app.py` registration block (after the `mcp_oauth` lines)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `mcp_oauth.hash_token`, `app.McpGrant`, `app.McpToolCall`, `modules.is_enabled`.
- Produces in `mcp_server.py`:
  - `class ToolError(Exception)`: a handler raises it with a user-facing sentence
  - `@dataclass Tool(name, title, description, input_schema, handler, roles=ALL_STAFF, module=None)` where `handler(ctx, args: dict) -> dict`
  - `TOOLS: dict[str, Tool]`, `register_tool(tool) -> Tool`
  - `@dataclass McpContext(appmod, tenant, user, grant, roles: list[str])`
  - role tuples `ALL_STAFF = ('admin','finance','employee','collector','cashier')`, `FINANCE = ('admin','finance')`, `PAYMENTS = ('admin','finance','cashier')`
  - `SUPPORTED_VERSIONS`, `register_mcp_routes(app, appmod)`

- [ ] **Step 1: Write the failing tests** (`tests/test_mcp_server.py`)

```python
import pytest

import mcp_server
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import call_tool, connect, enable_connector, mcp, tool_data, tool_error_text


@pytest.fixture
def echo_tool():
    def handler(ctx, args):
        if args.get("boom"):
            raise RuntimeError("kaboom")
        if args.get("bad"):
            raise mcp_server.ToolError("'bad' is not allowed.")
        return {"tenant": ctx.tenant.name, "echo": args.get("text")}
    t = mcp_server.register_tool(mcp_server.Tool(
        name="test_echo", title="Echo", description="test only",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=handler, roles=mcp_server.FINANCE))
    yield t
    mcp_server.TOOLS.pop("test_echo", None)


def _token(client, username="srv_admin", business="Srv Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    access, _, _ = connect(client, hdr)
    return access


def test_no_token_gives_401_with_resource_metadata(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401
    assert 'resource_metadata="http://localhost/.well-known/oauth-protected-resource"' in r.headers["WWW-Authenticate"]


def test_initialize_negotiates_version(client):
    tok = _token(client)
    r = mcp(client, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "1"}})
    res = r.get_json()["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["serverInfo"]["name"] == "servicebills" and "tools" in res["capabilities"]
    r = mcp(client, tok, "initialize", {"protocolVersion": "1999-01-01"})
    assert r.get_json()["result"]["protocolVersion"] == mcp_server.SUPPORTED_VERSIONS[0]


def test_notification_202_and_ping(client):
    tok = _token(client)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 202
    assert mcp(client, tok, "ping").get_json()["result"] == {}


def test_unknown_method_and_bad_version_header(client):
    tok = _token(client)
    assert mcp(client, tok, "resources/list").get_json()["error"]["code"] == -32601
    r = mcp(client, tok, "ping", headers={"MCP-Protocol-Version": "1999-01-01"})
    assert r.status_code == 400


def test_get_is_405(client):
    assert client.get("/mcp").status_code == 405


def test_tools_list_has_read_only_annotations(client, echo_tool):
    tok = _token(client)
    tools = {t["name"]: t for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert tools["test_echo"]["annotations"]["readOnlyHint"] is True
    assert tools["test_echo"]["inputSchema"]["type"] == "object"


def test_tool_call_success_is_audited(client, echo_tool):
    import app as appmod
    tok = _token(client)
    data = tool_data(call_tool(client, tok, "test_echo", {"text": "hi"}))
    assert data == {"tenant": "Srv Biz", "echo": "hi"}
    row = appmod.McpToolCall.query.one()
    assert row.tool == "test_echo" and row.ok is True and '"hi"' in row.args_summary
    assert appmod.McpGrant.query.one().last_used_at is not None


def test_tool_errors(client, echo_tool):
    import app as appmod
    tok = _token(client)
    assert tool_error_text(call_tool(client, tok, "test_echo", {"bad": True})) == "'bad' is not allowed."
    assert "went wrong" in tool_error_text(call_tool(client, tok, "test_echo", {"boom": True}))
    assert [r.ok for r in appmod.McpToolCall.query.order_by(appmod.McpToolCall.id)] == [False, False]
    r = mcp(client, tok, "tools/call", {"name": "nope", "arguments": {}})
    assert r.get_json()["error"]["code"] == -32602


def test_role_gate(client, echo_tool):
    make_tenant(client, "Role Biz", "role_admin")
    enable_connector("role_admin")
    cashier = auth_headers(client, username="role_cashier", role="cashier")
    tok, _, _ = connect(client, cashier)
    msg = tool_error_text(call_tool(client, tok, "test_echo", {"text": "x"}))
    assert "role (cashier)" in msg


def test_module_off_and_suspended_and_revoked(client, echo_tool):
    import app as appmod
    tok = _token(client)
    tenant = appmod.Tenant.query.filter_by(name="Srv Biz").one()
    tenant.module_overrides = {"ai_connector": False}
    appmod.db.session.commit()
    assert mcp(client, tok, "ping").status_code == 403
    tenant.module_overrides = {"ai_connector": True}
    tenant.status = "suspended"
    appmod.db.session.commit()
    assert mcp(client, tok, "ping").status_code == 403
    tenant.status = "active"
    appmod.db.session.commit()
    client.post("/oauth/revoke", data={"token": tok})
    assert mcp(client, tok, "ping").status_code == 401


def test_demotion_applies_immediately(client, echo_tool):
    import app as appmod
    tok = _token(client)
    assert tool_data(call_tool(client, tok, "test_echo", {"text": "a"}))["echo"] == "a"
    user = appmod.User.query.filter_by(username="srv_admin").one()
    user.role = "collector"
    appmod.db.session.commit()
    assert "role (collector)" in tool_error_text(call_tool(client, tok, "test_echo", {"text": "a"}))


def test_tenant_isolation_of_context(client, echo_tool):
    tok_a = _token(client, "iso_a", "Biz A")
    tok_b = _token(client, "iso_b", "Biz B")
    assert tool_data(call_tool(client, tok_a, "test_echo"))["tenant"] == "Biz A"
    assert tool_data(call_tool(client, tok_b, "test_echo"))["tenant"] == "Biz B"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server'`.

- [ ] **Step 3: Create `mcp_server.py`**

```python
"""MCP (Model Context Protocol) endpoint for the AI connector: stateless
Streamable HTTP, one JSON response per POST, no SSE stream, no sessions. See
docs/superpowers/specs/2026-10-21-mcp-connector-design.md. Registered from
app.py via register_mcp_routes(app, appmod); tools register themselves from
mcp_tools.py.

Auth is an OAuth access token issued by mcp_oauth.py. Every request re-reads
the user, tenant and module from the DB, so a revoke, demotion, suspension or
module switch-off applies on the very next call."""
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
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

ALL_STAFF = ('admin', 'finance', 'employee', 'collector', 'cashier')
FINANCE = ('admin', 'finance')
PAYMENTS = ('admin', 'finance', 'cashier')


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


def _result(msg_id, result):
    return {'jsonrpc': '2.0', 'id': msg_id, 'result': result}


def _error(msg_id, code, message):
    return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': code, 'message': message}}


def _tool_error(text):
    return {'content': [{'type': 'text', 'text': text}], 'isError': True}


def _describe(tool):
    return {'name': tool.name, 'title': tool.title, 'description': tool.description,
            'inputSchema': tool.input_schema,
            'annotations': {'title': tool.title, 'readOnlyHint': True,
                            'destructiveHint': False, 'openWorldHint': False}}


def _unauthorized(description='Missing or invalid access token'):
    meta = mcp_oauth.origin() + '/.well-known/oauth-protected-resource'
    resp = make_response(jsonify(error='invalid_token', error_description=description), 401)
    resp.headers['WWW-Authenticate'] = f'Bearer resource_metadata="{meta}", error="invalid_token"'
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
    return json.dumps(args, ensure_ascii=False, default=str, sort_keys=True)[:500]


def call_tool(ctx, tool, args):
    started = time.monotonic()
    ok, error = True, None
    if not any(r in tool.roles for r in ctx.roles):
        ok, error = False, f"Your role ({ctx.user.role}) can't use {tool.name}."
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
            ctx.appmod.db.session.rollback()
            ok, error = False, 'internal error'
            out = _tool_error('Something went wrong reading this data. Try again or narrow the request.')
    ctx.appmod.db.session.add(ctx.appmod.McpToolCall(
        tenant_id=ctx.tenant.id, user_id=ctx.user.id, grant_id=ctx.grant.id, tool=tool.name,
        args_summary=_summarize(args), ok=ok, error=(error or None) and error[:300],
        duration_ms=int((time.monotonic() - started) * 1000)))
    return out


def handle_message(ctx, msg):
    msg_id = msg.get('id')
    method = msg.get('method')
    params = msg.get('params') or {}
    if method == 'initialize':
        requested = params.get('protocolVersion')
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(msg_id, {'protocolVersion': version,
                                'capabilities': {'tools': {'listChanged': False}},
                                'serverInfo': SERVER_INFO, 'instructions': INSTRUCTIONS})
    if method == 'ping':
        return _result(msg_id, {})
    if method == 'tools/list':
        visible = [t for t in TOOLS.values() if not t.module or modules.is_enabled(ctx.tenant, t.module)]
        return _result(msg_id, {'tools': [_describe(t) for t in visible]})
    if method == 'tools/call':
        name = params.get('name')
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
    @appmod.limiter.limit('60/minute', key_func=_per_token_key)
    def mcp_endpoint():
        ctx, err = resolve_context(appmod)
        if err is not None:
            return err
        version = request.headers.get('MCP-Protocol-Version')
        if version and version not in SUPPORTED_VERSIONS:
            return jsonify(_error(None, -32600, f'Unsupported MCP-Protocol-Version: {version}')), 400
        msg = request.get_json(silent=True)
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
            return jsonify(_error(None, -32600, 'Invalid Request (batches are not supported)')), 400
        if 'id' not in msg:   # notification or client response: nothing to answer
            db.session.commit()
            return ('', 202)
        out = handle_message(ctx, msg)
        db.session.commit()
        return jsonify(out)

    @app.route('/mcp', methods=['GET', 'DELETE'])
    def mcp_no_stream():
        return jsonify(error='This server has no SSE stream or sessions; POST JSON-RPC to /mcp.'), 405
```

- [ ] **Step 4: Register in `app.py`** (after the `mcp_oauth` registration lines)

```python
import mcp_server
mcp_server.register_mcp_routes(app, sys.modules[__name__])
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py app.py tests/test_mcp_server.py
git commit -m "feat(mcp): /mcp JSON-RPC endpoint with token auth, gating and audit log

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Customer tools (`search_customers`, `get_customer`, `list_customers`)

**Files:**
- Create: `mcp_tools.py`
- Modify: `app.py`: add `import mcp_tools  # registers the MCP tools` right after `import mcp_server`
- Test: `tests/test_mcp_tools_customers.py`

**Interfaces:**
- Consumes: `mcp_server.Tool/register_tool/ToolError/ALL_STAFF/FINANCE/PAYMENTS`; `cs_agent_tools.normalize_lebanese_phone`; `app._zone_for`, `app._canonical_mac`, `app._normalize_mac`.
- Produces in `mcp_tools.py` (Tasks 8–9 reuse these):
  - `MAX_ROWS = 50`
  - `scoped(ctx, model)`: query filtered by `tenant_id`
  - `zone(ctx)`, `as_of(ctx) -> str`, `today(ctx) -> date`, `local(ctx, dt_utc) -> str | None`
  - arg helpers `str_arg`, `int_arg`, `bool_arg`, `date_arg`, `enum_arg`
  - `call_api(ctx, path, params=None) -> dict | list` (raises `ToolError` on a 4xx/5xx)

- [ ] **Step 1: Write the failing tests** (`tests/test_mcp_tools_customers.py`)

```python
from datetime import datetime, timedelta

from tests.conftest import make_tenant
from tests.mcp_helpers import call_tool, connect, enable_connector, mcp, tool_data


def _plan(client, hdr):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": "Basic", "price": 10, "billing_cycle": "monthly"})
    return r.get_json()["plan"]["id"]


def _customer(client, hdr, plan_id, name, phone, expiry_in_days, balance=0.0, **fields):
    import app as appmod
    r = client.post("/api/customers", headers=hdr, json={
        "name": name, "phone": phone, "address": "Main st", "subscription_plan_id": plan_id,
        "subscription_start_date": "2026-01-01"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    c = appmod.Customer.query.filter_by(name=name).one()
    # "Today" in the business timezone (Asia/Beirut by default), the same one
    # the tools use -- a UTC midnight would be off by a day after 21:00 UTC.
    biz_today = datetime.now(appmod._zone_for(None)).date()
    c.subscription_expiry_date = datetime.combine(biz_today + timedelta(days=expiry_in_days), datetime.min.time())
    c.balance = balance
    for k, v in fields.items():
        setattr(c, k, v)
    appmod.db.session.commit()
    return c.id


def _setup(client, username="cu_admin", business="CU Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    plan = _plan(client, hdr)
    tok, _, _ = connect(client, hdr)
    return hdr, plan, tok


def test_search_by_name_phone_and_mac(client):
    hdr, plan, tok = _setup(client)
    ali = _customer(client, hdr, plan, "Ali Haddad", "03123456", 5, cpe_mac_address="AA-BB-CC-DD-EE-FF")
    _customer(client, hdr, plan, "Rami Saad", "70111222", 20)
    by_name = tool_data(call_tool(client, tok, "search_customers", {"query": "hadd"}))
    assert [c["id"] for c in by_name["customers"]] == [ali]
    by_phone = tool_data(call_tool(client, tok, "search_customers", {"query": "+961 3 123 456"}))
    assert [c["id"] for c in by_phone["customers"]] == [ali]
    by_mac = tool_data(call_tool(client, tok, "search_customers", {"query": "aa:bb:cc:dd:ee:ff"}))
    assert [c["id"] for c in by_mac["customers"]] == [ali]
    none = tool_data(call_tool(client, tok, "search_customers", {"query": "zzz"}))
    assert none["count"] == 0 and "hint" in none and "as_of" in none


def test_search_never_crosses_tenants(client):
    hdr_a, plan_a, tok_a = _setup(client, "ta_admin", "Biz A")
    _customer(client, hdr_a, plan_a, "Shared Name A", "03999888", 3)
    _, _, tok_b = _setup(client, "tb_admin", "Biz B")
    assert tool_data(call_tool(client, tok_b, "search_customers", {"query": "Shared"}))["count"] == 0
    assert tool_data(call_tool(client, tok_b, "search_customers", {"query": "03999888"}))["count"] == 0


def test_get_customer_details_and_other_tenant_is_not_found(client):
    hdr, plan, tok = _setup(client)
    cid = _customer(client, hdr, plan, "Owes Money", "03222333", 2, balance=-25.0)
    d = tool_data(call_tool(client, tok, "get_customer", {"customer_id": cid}))
    assert d["found"] is True
    c = d["customer"]
    assert c["owes"] == 25.0 and c["balance"] == -25.0 and c["plan"] == "Basic"
    assert c["days_to_expiry"] == 2
    assert isinstance(d["recent_payments"], list) and isinstance(d["open_tickets"], list)
    _, _, tok_other = _setup(client, "other_admin", "Other Biz")
    assert tool_data(call_tool(client, tok_other, "get_customer", {"customer_id": cid}))["found"] is False


def test_list_customers_filters(client):
    hdr, plan, tok = _setup(client)
    soon = _customer(client, hdr, plan, "Soon", "03000001", 3)
    _customer(client, hdr, plan, "Later", "03000002", 40)
    owing = _customer(client, hdr, plan, "Owing", "03000003", 40, balance=-10.0)
    gone = _customer(client, hdr, plan, "Expired", "03000004", -2)
    d = tool_data(call_tool(client, tok, "list_customers", {"expiring_within_days": 7}))
    assert [c["id"] for c in d["customers"]] == [soon]
    d = tool_data(call_tool(client, tok, "list_customers", {"owing": True}))
    assert [c["id"] for c in d["customers"]] == [owing] and d["total_owed"] == 10.0
    d = tool_data(call_tool(client, tok, "list_customers", {"expired_within_days": 7}))
    assert [c["id"] for c in d["customers"]] == [gone]
    d = tool_data(call_tool(client, tok, "list_customers", {"plan": "basic"}))
    assert d["total"] == 4


def test_list_customers_caps_rows(client):
    hdr, plan, tok = _setup(client)
    for i in range(3):
        _customer(client, hdr, plan, f"Bulk {i}", f"0300010{i}", 10)
    d = tool_data(call_tool(client, tok, "list_customers", {"limit": 2}))
    assert d["total"] == 3 and d["returned"] == 2 and "note" in d


def test_bad_arguments_are_tool_errors(client):
    _, _, tok = _setup(client)
    r = call_tool(client, tok, "get_customer", {"customer_id": "abc"})
    assert r["isError"] is True and "whole number" in r["content"][0]["text"]
    r = call_tool(client, tok, "list_customers", {"status": "weird"})
    assert r["isError"] is True


def test_customer_tools_listed(client):
    _, _, tok = _setup(client)
    names = {t["name"] for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert {"search_customers", "get_customer", "list_customers"} <= names
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_mcp_tools_customers.py -v`
Expected: FAIL (`Unknown tool: search_customers` → `-32602`, so the `call_tool` helper's `result` lookup raises `KeyError`).

- [ ] **Step 3: Create `mcp_tools.py`** (shared helpers + customer tools)

```python
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
```

- [ ] **Step 4: Register the module in `app.py`**

Right after `mcp_server.register_mcp_routes(app, sys.modules[__name__])`:
```python
import mcp_tools  # noqa: F401 -- registers the MCP connector's tools
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_mcp_tools_customers.py tests/test_mcp_server.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_tools.py app.py tests/test_mcp_tools_customers.py
git commit -m "feat(mcp): customer search/detail/list tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Finance tools (`list_payments`, `get_cash_flow`, `get_financial_summary`)

**Files:**
- Modify: `mcp_tools.py` (append)
- Test: `tests/test_mcp_tools_finance.py`

**Interfaces:**
- Consumes: `call_api`, arg helpers, `as_of` from Task 7. Endpoints: `GET /api/payments`, `GET /api/reports/daily-cash`, `GET /api/reports/financial`, `GET /api/dashboard`.

- [ ] **Step 1: Write the failing tests** (`tests/test_mcp_tools_finance.py`)

```python
from datetime import datetime

from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import call_tool, connect, enable_connector, tool_data, tool_error_text


def _seed(client):
    """Tenant with one customer and one paid payment on 2026-10-05."""
    import app as appmod
    hdr = make_tenant(client, "Fin Biz", "fin_admin")
    enable_connector("fin_admin")
    plan = client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "Basic", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    client.post("/api/customers", headers=hdr, json={
        "name": "Payer", "phone": "03555666", "address": "x", "subscription_plan_id": plan,
        "subscription_start_date": "2026-10-01"})
    c = appmod.Customer.query.filter_by(name="Payer").one()
    appmod.db.session.add(appmod.Payment(
        tenant_id=c.tenant_id, customer_id=c.id, amount=42.0, currency="USD",
        date=datetime(2026, 10, 5), paid=True, paid_at=datetime(2026, 10, 5, 9, 0)))
    appmod.db.session.commit()
    return hdr


def test_list_payments_matches_payments_endpoint(client):
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_payments",
                            {"paid_from": "2026-10-05", "paid_to": "2026-10-05", "status": "paid"}))
    web = client.get("/api/payments", headers=hdr, query_string={
        "paid_date_start": "2026-10-05", "paid_date_end": "2026-10-05", "status": "paid"}).get_json()
    assert d["total"] == web["total"] >= 1
    assert d["totals"] == web["totals"]
    assert any(p["amount"] == 42.0 and p["customer_name"] == "Payer" for p in d["payments"])


def test_cashier_can_list_payments_but_not_cash_flow(client):
    _seed(client)
    cashier = auth_headers(client, username="fin_cashier", role="cashier")
    tok, _, _ = connect(client, cashier)
    assert "payments" in tool_data(call_tool(client, tok, "list_payments", {}))
    assert "role (cashier)" in tool_error_text(call_tool(client, tok, "get_cash_flow", {"day": "2026-10-05"}))


def test_collector_cannot_list_payments(client):
    _seed(client)
    collector = auth_headers(client, username="fin_collector", role="collector")
    tok, _, _ = connect(client, collector)
    assert "role (collector)" in tool_error_text(call_tool(client, tok, "list_payments", {}))


def test_cash_flow_matches_daily_cash_endpoint(client):
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "get_cash_flow", {"day": "2026-10-05"}))
    web = client.get("/api/reports/daily-cash", headers=hdr, query_string={"day": "2026-10-05"}).get_json()
    assert d["collected_total"] == web["grand_total"]
    assert d["currency"] == web["reporting_currency"]
    assert d["cash_flow"]["day"] == web["cash_flow"]["day"]
    assert d["cash_flow"]["total_in"] == web["cash_flow"]["total_in"]


def test_financial_summary_month(client):
    hdr = _seed(client)
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "get_financial_summary", {"month": "2026-10"}))
    web = client.get("/api/reports/financial", headers=hdr,
                     query_string={"start_date": "2026-10-01", "end_date": "2026-10-31"}).get_json()
    assert d["totals"] == web["totals"]
    assert d["period"] == {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    assert "active_customers" in d["snapshot"]
    r = call_tool(client, tok, "get_financial_summary", {"month": "October"})
    assert r["isError"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_mcp_tools_finance.py -v`
Expected: FAIL (unknown tools).

- [ ] **Step 3: Append to `mcp_tools.py`**

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_mcp_tools_finance.py -v`
Expected: all PASS. If `call_api` fails with a session or context error, check that Flask's `test_request_context` is reusing the active app context: `flask.globals._cv_app` must be the same before and inside the `with`. Fix it there; don't switch to `app.test_client()`, which would open a second context.

- [ ] **Step 5: Commit**

```bash
git add mcp_tools.py tests/test_mcp_tools_finance.py
git commit -m "feat(mcp): payments, cash flow and financial summary tools via existing reports

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Tickets, network, resellers, upstreams

**Files:**
- Modify: `mcp_tools.py` (append)
- Test: `tests/test_mcp_tools_other.py`

**Interfaces:**
- Consumes: Task 7 helpers; endpoints `GET /api/network-tree` (network-view roles + `network` module), `GET /api/resellers` (admin/finance), `GET /api/upstream-providers` (admin/finance + `upstream_sync` module).

- [ ] **Step 1: Write the failing tests** (`tests/test_mcp_tools_other.py`)

```python
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import call_tool, connect, enable_connector, mcp, tool_data, tool_error_text


def _admin(client, username="ot_admin", business="OT Biz"):
    hdr = make_tenant(client, business, username)
    enable_connector(username)
    return hdr


def test_list_tickets_default_open(client):
    import app as appmod
    hdr = _admin(client)
    tid = appmod.User.query.filter_by(username="ot_admin").one().tenant_id
    appmod.db.session.add_all([
        appmod.SupportTicket(tenant_id=tid, title="No internet", description="down since 9", status="open", priority="high"),
        appmod.SupportTicket(tenant_id=tid, title="Old", description="fixed", status="closed", priority="low")])
    appmod.db.session.commit()
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_tickets", {}))
    assert [t["title"] for t in d["tickets"]] == ["No internet"]
    d = tool_data(call_tool(client, tok, "list_tickets", {"status": "all"}))
    assert d["total"] == 2


def test_network_status_matches_tree_and_needs_module(client):
    import app as appmod
    hdr = _admin(client)
    tid = appmod.User.query.filter_by(username="ot_admin").one().tenant_id
    appmod.db.session.add(appmod.NetworkDevice(tenant_id=tid, name="CCR", host="10.0.0.1", username="u",
                                               password="pw", device_type="mikrotik_ccr", api_port=8728))
    appmod.db.session.commit()
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "get_network_status", {}))
    assert [x["name"] for x in d["devices"]] == ["CCR"]
    assert "username" not in d["devices"][0] and "host" not in d["devices"][0]
    tenant = appmod.db.session.get(appmod.Tenant, tid)
    tenant.module_overrides = {"ai_connector": True, "network": False}
    appmod.db.session.commit()
    names = {t["name"] for t in mcp(client, tok, "tools/list").get_json()["result"]["tools"]}
    assert "get_network_status" not in names


def test_resellers_admin_only(client):
    import app as appmod
    hdr = _admin(client)
    tid = appmod.User.query.filter_by(username="ot_admin").one().tenant_id
    appmod.db.session.add(appmod.Reseller(tenant_id=tid, name="Resell1", phone="1", type="type1", balance=-5))
    appmod.db.session.commit()
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_resellers", {}))
    assert d["resellers"][0]["name"] == "Resell1" and d["resellers"][0]["customer_count"] == 0
    emp = auth_headers(client, username="ot_employee", role="employee")
    tok_emp, _, _ = connect(client, emp)
    assert "role (employee)" in tool_error_text(call_tool(client, tok_emp, "list_resellers", {}))


def test_upstreams_hide_portal_credentials(client):
    import app as appmod
    hdr = _admin(client)
    tid = appmod.User.query.filter_by(username="ot_admin").one().tenant_id
    appmod.db.session.add(appmod.UpstreamProvider(tenant_id=tid, name="Terra", product="proradius",
                                                  portal_username="me", portal_password="secret", balance=100))
    appmod.db.session.commit()
    tok, _, _ = connect(client, hdr)
    d = tool_data(call_tool(client, tok, "list_upstreams", {}))
    assert d["upstreams"][0]["name"] == "Terra" and d["upstreams"][0]["balance"] == 100.0
    assert "portal_username" not in d["upstreams"][0] and "portal_url" not in d["upstreams"][0]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_mcp_tools_other.py -v`
Expected: FAIL (unknown tools).

- [ ] **Step 3: Append to `mcp_tools.py`**

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_mcp_tools_other.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_tools.py tests/test_mcp_tools_other.py
git commit -m "feat(mcp): ticket, network status, reseller and upstream tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Connection management API

**Files:**
- Modify: `mcp_oauth.py` (routes inside `register_mcp_oauth_routes`)
- Test: `tests/test_mcp_grants_api.py`

**Interfaces:**
- Consumes: `revoke_grant` (Task 5), `tenancy.tenant_query`.
- Produces:
  - `GET /api/mcp/grants` (JWT) returns `{mcp_url, enabled, is_admin, grants: [McpGrant.to_dict(client_name)]}`. Admins see every active grant in the tenant; other roles see only their own.
  - `DELETE /api/mcp/grants/<int:grant_id>` (JWT) returns `{ok: true}`, or 404 when the grant isn't visible to the caller.

- [ ] **Step 1: Write the failing tests** (`tests/test_mcp_grants_api.py`)

```python
from tests.conftest import auth_headers, make_tenant
from tests.mcp_helpers import connect, enable_connector, mcp


def test_admin_sees_all_staff_sees_own(client):
    admin = make_tenant(client, "GR Biz", "gr_admin")
    enable_connector("gr_admin")
    cashier = auth_headers(client, username="gr_cashier", role="cashier")
    connect(client, admin)
    connect(client, cashier)
    j = client.get("/api/mcp/grants", headers=admin).get_json()
    assert j["mcp_url"] == "http://localhost/mcp" and j["enabled"] is True and j["is_admin"] is True
    assert sorted(g["username"] for g in j["grants"]) == ["gr_admin", "gr_cashier"]
    j = client.get("/api/mcp/grants", headers=cashier).get_json()
    assert [g["username"] for g in j["grants"]] == ["gr_cashier"]


def test_revoke_own_and_admin_revokes_any(client):
    admin = make_tenant(client, "RV2 Biz", "rv2_admin")
    enable_connector("rv2_admin")
    cashier = auth_headers(client, username="rv2_cashier", role="cashier")
    admin_tok, _, _ = connect(client, admin)
    cashier_tok, _, _ = connect(client, cashier)
    grants = client.get("/api/mcp/grants", headers=admin).get_json()["grants"]
    admin_gid = next(g["id"] for g in grants if g["username"] == "rv2_admin")
    cashier_gid = next(g["id"] for g in grants if g["username"] == "rv2_cashier")
    assert client.delete(f"/api/mcp/grants/{admin_gid}", headers=cashier).status_code == 404
    assert client.delete(f"/api/mcp/grants/{cashier_gid}", headers=admin).status_code == 200
    assert mcp(client, cashier_tok, "ping").status_code == 401
    assert mcp(client, admin_tok, "ping").status_code == 200


def test_other_tenant_grant_is_404(client):
    a = make_tenant(client, "GA", "ga_admin")
    enable_connector("ga_admin")
    connect(client, a)
    gid = client.get("/api/mcp/grants", headers=a).get_json()["grants"][0]["id"]
    b = make_tenant(client, "GB", "gb_admin")
    assert client.delete(f"/api/mcp/grants/{gid}", headers=b).status_code == 404
    assert client.get("/api/mcp/grants", headers=b).get_json()["grants"] == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_mcp_grants_api.py -v`
Expected: FAIL (404 on `/api/mcp/grants`).

- [ ] **Step 3: Implement** (inside `register_mcp_oauth_routes`; add `from tenancy import current_tenant, tenant_query` to the imports at the top)

```python
    def _active_grants_query():
        now = datetime.utcnow()
        return tenant_query(Grant).filter(Grant.revoked_at.is_(None),
                                          Grant.refresh_token_hash.isnot(None),
                                          Grant.refresh_expires_at > now)

    def _me():
        return appmod.User.query.filter_by(username=get_jwt_identity()).first()

    @app.route('/api/mcp/grants', methods=['GET'])
    @jwt_required()
    def mcp_list_grants():
        me = _me()
        is_admin = 'admin' in appmod._jwt_roles()
        q = _active_grants_query()
        if not is_admin:
            q = q.filter(Grant.user_id == (me.id if me else -1))
        grants = q.order_by(Grant.created_at.desc()).all()
        names = {c.client_id: c.client_name for c in Client.query.filter(
            Client.client_id.in_({g.client_id for g in grants})).all()} if grants else {}
        return jsonify(mcp_url=origin() + '/mcp',
                       enabled=modules.is_enabled(current_tenant(), 'ai_connector'),
                       is_admin=is_admin,
                       grants=[g.to_dict(names.get(g.client_id)) for g in grants])

    @app.route('/api/mcp/grants/<int:grant_id>', methods=['DELETE'])
    @jwt_required()
    def mcp_revoke_grant(grant_id):
        me = _me()
        q = tenant_query(Grant).filter(Grant.id == grant_id, Grant.revoked_at.is_(None))
        if 'admin' not in appmod._jwt_roles():
            q = q.filter(Grant.user_id == (me.id if me else -1))
        grant = q.first()
        if grant is None:
            return jsonify(msg='Connection not found'), 404
        revoke_grant(grant, datetime.utcnow())
        db.session.commit()
        return jsonify(ok=True)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_mcp_grants_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_oauth.py tests/test_mcp_grants_api.py
git commit -m "feat(mcp): list and revoke connected AI apps

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Frontend consent page

**Files:**
- Create: `frontend/src/components/oauthConsent.js` (pure helpers)
- Create: `frontend/src/components/oauthConsent.test.js`
- Create: `frontend/src/components/OAuthConsentView.js`
- Modify: `frontend/src/App.js` (import + public route next to the other deep links ≈ line 600)

**Interfaces:**
- Consumes: `GET /api/mcp/oauth/consent-info`, `POST /api/mcp/oauth/approve` (Task 4); `useAppContext()` → `{ isAuthenticated, login, logout, user }`.
- Produces: `consentParams(search: string) -> object` (only the 8 authorize keys), `describeRole(role: string) -> string`.

- [ ] **Step 1: Write the failing test** (`frontend/src/components/oauthConsent.test.js`)

```js
import { consentParams, describeRole } from './oauthConsent';

test('keeps only the OAuth authorize parameters', () => {
    const p = consentParams('?client_id=c1&redirect_uri=https%3A%2F%2Fclaude.ai%2Fcb&response_type=code'
        + '&code_challenge=abc&code_challenge_method=S256&state=s&scope=read&resource=x&evil=1');
    expect(p).toEqual({
        client_id: 'c1', redirect_uri: 'https://claude.ai/cb', response_type: 'code',
        code_challenge: 'abc', code_challenge_method: 'S256', state: 's', scope: 'read', resource: 'x',
    });
});

test('omits missing parameters', () => {
    expect(consentParams('?client_id=c1')).toEqual({ client_id: 'c1' });
});

test('describes roles in plain words', () => {
    expect(describeRole('admin')).toBe('admin');
    expect(describeRole('finance,cashier')).toBe('finance, cashier');
    expect(describeRole('')).toBe('staff');
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx react-scripts test --watchAll=false src/components/oauthConsent.test.js`
Expected: FAIL (`Cannot find module './oauthConsent'`).

- [ ] **Step 3: Write `frontend/src/components/oauthConsent.js`**

```js
// Pure helpers for the MCP connector consent page (OAuthConsentView).
export const AUTHORIZE_KEYS = ['client_id', 'redirect_uri', 'response_type', 'code_challenge',
    'code_challenge_method', 'state', 'scope', 'resource'];

export function consentParams(search) {
    const q = new URLSearchParams(search);
    const out = {};
    AUTHORIZE_KEYS.forEach(k => { if (q.get(k) !== null) out[k] = q.get(k); });
    return out;
}

export function describeRole(role) {
    const parts = (role || '').split(',').map(s => s.trim()).filter(Boolean);
    return parts.length ? parts.join(', ') : 'staff';
}
```

- [ ] **Step 4: Write `frontend/src/components/OAuthConsentView.js`**

```js
import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Alert, Box, Button, CircularProgress, Paper, Stack, TextField, Typography } from '@mui/material';
import { SmartToy as SmartToyIcon } from '@mui/icons-material';
import { useAppContext } from '../context/AppContext';
import { consentParams, describeRole } from './oauthConsent';

// "Claude wants read-only access to <business> as <user>". Reached from
// GET /oauth/authorize (mcp_oauth.py) with the OAuth request in the query.
// Signs the user in first if needed, then approves or denies and sends the
// browser back to the AI app with a one-time code.
export default function OAuthConsentView() {
    const { isAuthenticated, login, logout } = useAppContext();
    const [params] = useState(() => consentParams(window.location.search));
    const [info, setInfo] = useState(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [creds, setCreds] = useState({ username: '', password: '' });

    useEffect(() => {
        if (!isAuthenticated) return;
        const token = localStorage.getItem('token');
        axios.get('/api/mcp/oauth/consent-info', { params, headers: { Authorization: `Bearer ${token}` } })
            .then(res => setInfo(res.data))
            .catch(err => setError(err.response?.data?.error || err.response?.data?.msg || 'This connection request is not valid.'));
    }, [isAuthenticated, params]);

    const signIn = async (e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        try {
            await login(creds);
        } catch (err) {
            setError(err.response?.data?.msg || 'Sign-in failed.');
        } finally {
            setBusy(false);
        }
    };

    const decide = async (decision) => {
        setBusy(true);
        try {
            const token = localStorage.getItem('token');
            const res = await axios.post('/api/mcp/oauth/approve', { ...params, decision },
                { headers: { Authorization: `Bearer ${token}` } });
            window.location.assign(res.data.redirect_url);
        } catch (err) {
            setError(err.response?.data?.error || 'Could not complete the connection.');
            setBusy(false);
        }
    };

    return (
        <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', p: 2, bgcolor: 'background.default' }}>
            <Paper variant="outlined" sx={{ p: 4, maxWidth: 440, width: '100%', borderRadius: '16px' }}>
                <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
                    <SmartToyIcon color="primary" />
                    <Typography variant="h6">Connect an AI app to ServiceBills</Typography>
                </Stack>
                {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
                {!isAuthenticated && (
                    <form onSubmit={signIn}>
                        <Typography variant="body2" sx={{ mb: 2 }}>Sign in with your ServiceBills account to continue.</Typography>
                        <TextField fullWidth label="Username" sx={{ mb: 2 }} value={creds.username}
                            onChange={e => setCreds(c => ({ ...c, username: e.target.value }))} />
                        <TextField fullWidth type="password" label="Password" sx={{ mb: 2 }} value={creds.password}
                            onChange={e => setCreds(c => ({ ...c, password: e.target.value }))} />
                        <Button fullWidth type="submit" variant="contained" disabled={busy}>Sign in</Button>
                    </form>
                )}
                {isAuthenticated && !info && !error && <Box sx={{ textAlign: 'center', py: 3 }}><CircularProgress /></Box>}
                {isAuthenticated && info && (
                    <>
                        <Typography sx={{ mb: 1 }}>
                            <b>{info.client_name}</b> wants <b>read-only</b> access to <b>{info.tenant_name}</b> as{' '}
                            <b>{info.username}</b> ({describeRole(info.role)}).
                        </Typography>
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                            It will be able to look up customers, payments, reports, tickets and network status that
                            your role can see. It cannot change anything, take payments or send messages.
                        </Typography>
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                            You will be sent back to {info.redirect_host}. You can disconnect it any time in
                            Settings → AI Connector.
                        </Typography>
                        <Stack direction="row" spacing={1.5}>
                            <Button fullWidth variant="contained" disabled={busy} onClick={() => decide('allow')}>Allow</Button>
                            <Button fullWidth variant="outlined" disabled={busy} onClick={() => decide('deny')}>Deny</Button>
                        </Stack>
                        <Button size="small" sx={{ mt: 2 }} onClick={logout}>Not {info.username}? Sign out</Button>
                    </>
                )}
            </Paper>
        </Box>
    );
}
```

- [ ] **Step 5: Wire the route in `App.js`**

Import next to the other view imports:
```js
import OAuthConsentView from './components/OAuthConsentView.js';
```
In the public deep-link block (after `if (location.pathname === '/pay-business') ...`):
```js
    if (location.pathname === '/oauth/consent') return <OAuthConsentView />;
```

- [ ] **Step 6: Run the frontend tests**

Run: `cd frontend && npx react-scripts test --watchAll=false src/components/oauthConsent.test.js`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/oauthConsent.js frontend/src/components/oauthConsent.test.js frontend/src/components/OAuthConsentView.js frontend/src/App.js
git commit -m "feat(mcp): consent page for connecting an AI app

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Settings → AI Connector tab

**Files:**
- Create: `frontend/src/components/AIConnectorCard.js`
- Create: `frontend/src/components/AIConnectorCard.test.js`
- Modify: `frontend/src/components/SettingsView.js` (`SETTINGS_TABS` ≈ line 201, and a render block next to `activeTab === 'sectors'` ≈ line 1526)

**Interfaces:**
- Consumes: `GET /api/mcp/grants`, `DELETE /api/mcp/grants/<id>` (Task 10).

- [ ] **Step 1: Write the failing test** (`frontend/src/components/AIConnectorCard.test.js`)

```js
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import axios from 'axios';
import AIConnectorCard from './AIConnectorCard';

// Factory mock: axios ships ESM that CRA's Jest won't transform.
jest.mock('axios', () => ({ get: jest.fn(), delete: jest.fn() }));

const LIST = {
    mcp_url: 'https://servicebills.example/mcp', enabled: true, is_admin: true,
    grants: [{ id: 7, client_name: 'Claude', username: 'ali', role: 'cashier',
               created_at: '2026-10-21T10:00:00Z', last_used_at: null }],
};

beforeEach(() => {
    axios.get.mockReset();
    axios.delete.mockReset();
    window.confirm = jest.fn(() => true);
});

test('shows the connector URL and the connected apps', async () => {
    axios.get.mockResolvedValue({ data: LIST });
    render(<AIConnectorCard />);
    expect(await screen.findByTestId('mcp-url')).toHaveTextContent('https://servicebills.example/mcp');
    expect(screen.getByText(/Claude/)).toBeInTheDocument();
    expect(screen.getByText(/ali \(cashier\)/)).toBeInTheDocument();
});

test('disconnect revokes and reloads the list', async () => {
    axios.get.mockResolvedValueOnce({ data: LIST }).mockResolvedValueOnce({ data: { ...LIST, grants: [] } });
    axios.delete.mockResolvedValue({ data: { ok: true } });
    render(<AIConnectorCard />);
    fireEvent.click(await screen.findByText('Disconnect'));
    await waitFor(() => expect(axios.delete).toHaveBeenCalledWith('/api/mcp/grants/7', expect.any(Object)));
    expect(await screen.findByText(/No AI apps connected yet/)).toBeInTheDocument();
});

test('explains when the module is off', async () => {
    axios.get.mockResolvedValue({ data: { ...LIST, enabled: false, grants: [] } });
    render(<AIConnectorCard />);
    expect(await screen.findByText(/not part of your plan/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx react-scripts test --watchAll=false src/components/AIConnectorCard.test.js`
Expected: FAIL (`Cannot find module './AIConnectorCard'`).

- [ ] **Step 3: Write `frontend/src/components/AIConnectorCard.js`**

```js
import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Alert, Box, Button, IconButton, List, ListItem, ListItemText, Paper, Tooltip, Typography } from '@mui/material';
import { ContentCopy as ContentCopyIcon } from '@mui/icons-material';

const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } });

// The MCP connector URL plus every AI app connected to this business
// (admins see everyone's; see /api/mcp/grants in mcp_oauth.py).
export default function AIConnectorCard() {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [copied, setCopied] = useState(false);

    const load = useCallback(() => {
        axios.get('/api/mcp/grants', auth())
            .then(res => setData(res.data))
            .catch(err => setError(err.response?.data?.msg || 'Could not load AI connections.'));
    }, []);
    useEffect(() => { load(); }, [load]);

    const disconnect = async (g) => {
        if (!window.confirm(`Disconnect ${g.client_name || 'this AI app'} for ${g.username}?`)) return;
        try {
            await axios.delete(`/api/mcp/grants/${g.id}`, auth());
            load();
        } catch (err) {
            setError(err.response?.data?.msg || 'Could not disconnect.');
        }
    };

    const copy = async () => {
        try { await navigator.clipboard.writeText(data.mcp_url); setCopied(true); } catch (e) { setCopied(false); }
    };

    if (!data) return error ? <Alert severity="error">{error}</Alert> : null;
    return (
        <Paper variant="outlined" sx={{ p: 3, borderRadius: '12px' }}>
            <Typography variant="h6" sx={{ mb: 1 }}>AI Connector</Typography>
            {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
            {!data.enabled && (
                <Alert severity="info" sx={{ mb: 2 }}>The AI connector is not part of your plan. Contact ServiceBills to enable it.</Alert>
            )}
            <Typography variant="body2" sx={{ mb: 1 }}>
                Let Claude, ChatGPT or another AI app answer questions about your business (read-only).
                Add this URL as a custom connector:
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2, p: 1.5, bgcolor: 'action.hover', borderRadius: '8px' }}>
                <Typography data-testid="mcp-url" sx={{ fontFamily: 'monospace', wordBreak: 'break-all', flex: 1 }}>{data.mcp_url}</Typography>
                <Tooltip title={copied ? 'Copied' : 'Copy'}><IconButton size="small" onClick={copy}><ContentCopyIcon fontSize="small" /></IconButton></Tooltip>
            </Box>
            <Typography variant="body2" color="text.secondary" component="div" sx={{ mb: 2 }}>
                <b>Claude:</b> Settings → Connectors → Add custom connector → paste the URL → Connect.<br />
                <b>ChatGPT:</b> Settings → Apps &amp; Connectors → Create (developer mode) → paste the URL → OAuth.<br />
                Each person signs in with their own ServiceBills account and sees only what their role allows.
            </Typography>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>{data.is_admin ? 'Connected AI apps' : 'Your connected AI apps'}</Typography>
            {data.grants.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No AI apps connected yet.</Typography>
            ) : (
                <List dense>
                    {data.grants.map(g => (
                        <ListItem key={g.id} divider secondaryAction={<Button size="small" color="error" onClick={() => disconnect(g)}>Disconnect</Button>}>
                            <ListItemText
                                primary={`${g.client_name || 'AI app'}: ${g.username} (${g.role})`}
                                secondary={`Connected ${(g.created_at || '').slice(0, 10)}${g.last_used_at ? ` · last used ${g.last_used_at.slice(0, 16).replace('T', ' ')} UTC` : ' · not used yet'}`} />
                        </ListItem>
                    ))}
                </List>
            )}
        </Paper>
    );
}
```

- [ ] **Step 4: Add the tab in `SettingsView.js`**

Import at the top with the other component imports:
```js
import AIConnectorCard from './AIConnectorCard';
```
Make sure `SmartToy as SmartToyIcon` is in the `@mui/icons-material` import (add it if missing).

In `SETTINGS_TABS`, after the `whish` entry:
```js
        { key: 'ai-connector', label: 'AI Connector', icon: <SmartToyIcon sx={{ fontSize: 18 }} />, module: 'ai_connector' },
```
Next to the `{activeTab === 'sectors' && (...)}` block:
```js
            {activeTab === 'ai-connector' && (
                <AIConnectorCard />
            )}
```

- [ ] **Step 5: Run the frontend tests**

Run: `cd frontend && npx react-scripts test --watchAll=false src/components/AIConnectorCard.test.js src/components/oauthConsent.test.js`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/AIConnectorCard.js frontend/src/components/AIConnectorCard.test.js frontend/src/components/SettingsView.js
git commit -m "feat(mcp): Settings AI Connector tab with connect URL and revocation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(Settings is admin-only in the app today, so only admins get this tab. Other staff can still revoke their own connections through the API, and an admin can revoke anyone's. Exposing a self-service page to non-admins is a possible follow-up.)

---

### Task 13: End-to-end test, full suite, live check

**Files:**
- Test: `tests/test_mcp_e2e.py`
- Modify: `DEPLOY.md` (short "AI connector" section)

- [ ] **Step 1: Write the end-to-end test** (`tests/test_mcp_e2e.py`)

```python
"""The whole connector path the way Claude.ai drives it: 401 -> discovery ->
registration -> authorize -> consent -> token -> initialize -> tools."""
from tests.conftest import make_tenant
from tests.mcp_helpers import (REDIRECT, approve, exchange, mcp, pkce_pair, query_of, tool_data,
                               call_tool, enable_connector)


def test_full_connector_flow(client):
    hdr = make_tenant(client, "E2E Biz", "e2e_admin")
    enable_connector("e2e_admin")

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    prm_url = r.headers["WWW-Authenticate"].split('resource_metadata="')[1].split('"')[0]
    prm = client.get(prm_url.replace("http://localhost", "")).get_json()
    asm = client.get("/.well-known/oauth-authorization-server").get_json()
    assert prm["authorization_servers"] == [asm["issuer"]]

    reg = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]}).get_json()
    verifier, challenge = pkce_pair()
    params = {"client_id": reg["client_id"], "redirect_uri": REDIRECT, "response_type": "code",
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
              "resource": prm["resource"]}
    consent_url = client.get("/oauth/authorize", query_string=params).headers["Location"]
    consent_params = query_of(consent_url)
    redirect_url = approve(client, hdr, consent_params).get_json()["redirect_url"]
    q = query_of(redirect_url)
    assert q["state"] == "xyz"
    tokens = exchange(client, q["code"], reg["client_id"], verifier).get_json()

    init = mcp(client, tokens["access_token"], "initialize",
               {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}})
    assert init.get_json()["result"]["protocolVersion"] == "2025-11-25"
    tools = {t["name"] for t in mcp(client, tokens["access_token"], "tools/list").get_json()["result"]["tools"]}
    assert {"search_customers", "get_customer", "list_customers", "list_payments", "get_cash_flow",
            "get_financial_summary", "list_tickets", "get_network_status", "list_resellers"} <= tools
    assert "list_upstreams" in tools   # the free bundle includes upstream_sync
    d = tool_data(call_tool(client, tokens["access_token"], "list_customers", {}))
    assert d["total"] == 0
```

- [ ] **Step 2: Run the whole MCP suite, then the full backend suite**

Run:
```bash
python -m pytest tests/test_mcp_*.py -v
python -m pytest -q
```
Expected: every MCP test PASSES; the full suite shows no new failures compared with `main`. (Get the baseline with `git stash && python -m pytest -q && git stash pop` if anything unrelated fails.)

- [ ] **Step 3: Run the frontend tests**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false`
Expected: all PASS.

- [ ] **Step 4: Live check with MCP Inspector against a local server**

Follow the local test-server isolation rule: run against a throwaway SQLite DB, never the real dev database.
```bash
DATABASE_URL=sqlite:///$TEMP/mcp_live.db APP_BASE_URL=http://localhost:5000 python app.py
```
In a second terminal:
```bash
npx @modelcontextprotocol/inspector
```
In the Inspector UI: transport "Streamable HTTP", URL `http://localhost:5000/mcp`, then Connect.
- The OAuth flow opens `/oauth/consent`. Sign in as a tenant admin whose tenant has `ai_connector` enabled (via super-admin module overrides), then click Allow.
- In Tools, run `list_customers` and `get_financial_summary`, and check the results against the web app.

Expected: the connection succeeds; the tools return the same data as the web pages.

- [ ] **Step 5: Add a short section to `DEPLOY.md`**

```markdown
## AI connector (MCP)

- Endpoint: `{APP_BASE_URL}/mcp`, OAuth discovery at `/.well-known/oauth-protected-resource`
  and `/.well-known/oauth-authorization-server`. Needs `TRUST_PROXY=1` (already set in
  render.yaml) so the advertised URLs are https.
- Enable per tenant with the `ai_connector` module (super-admin → modules), or the pro plan.
- Users add the URL as a custom connector in Claude.ai / ChatGPT; the consent page is
  `/oauth/consent`. Connections are listed and revoked under Settings → AI Connector.
- Rate limits are in-memory per worker (WEB_CONCURRENCY=4), so the effective /mcp limit is
  up to 4 × 60/minute per token.
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_mcp_e2e.py DEPLOY.md
git commit -m "test(mcp): end-to-end connector flow; deploy notes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: After deploy (owner-assisted, not automatable)**

1. Run the migration on Render (it runs on deploy via `flask db upgrade`) and confirm the 3 tables exist.
2. Enable `ai_connector` for DeltaNet in super-admin.
3. In Claude.ai: Settings → Connectors → Add custom connector → `https://servicebills.salloumservices.com/mcp`. Connect, then ask "which customers expire this week?" and "how much cash came in yesterday?".
4. Repeat in ChatGPT (developer mode connector).
5. If either client keeps losing the connection after about an hour, look for refresh-token reuse revocations (grants with `revoked_at` set shortly after a rotation). If clients are racing refreshes, relax `_refresh` to reject a stale token without revoking the grant.
