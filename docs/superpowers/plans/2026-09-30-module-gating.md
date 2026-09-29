# Module Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-tenant feature modules (core/office always on; whatsapp, ai_cs, network, upstream_sync, whish_payments paid) enforced on backend routes, scheduled jobs and the React UI, with super-admin overrides.

**Architecture:** One pure resolver `modules.enabled_for(tenant)` (plan bundle from `plans.py` + `Tenant.module_overrides` JSON + dependency pruning, with a `license_provider` hook for sub-project 2). A `tenancy.require_module(key)` decorator gates JWT routes; non-JWT routes call `modules.is_enabled` after resolving their tenant. `/api/tenant/me` returns `modules`; the frontend filters nav/tabs with `hasModule`.

**Tech Stack:** Flask + Flask-SQLAlchemy + Alembic, flask_jwt_extended, pytest (in-memory SQLite via `tests/conftest.py`); React (CRA, MUI, axios), Jest via `react-scripts test`.

**Spec:** `docs/superpowers/specs/2026-09-30-module-gating-design.md`

## Global Constraints

- Module keys exactly: `core`, `office`, `whatsapp`, `ai_cs`, `network`, `upstream_sync`, `whish_payments`.
- `ALWAYS_ON = {core, office}`; they can never be disabled by overrides.
- `ai_cs` requires `whatsapp`.
- Plan bundles: `free` → `whatsapp`, `network`, `upstream_sync`; `pro` → all paid modules.
- Disabled-module JSON response: `{"msg": "Module not enabled", "module": "<key>"}` with status **403**. Public (unauthenticated) customer pages return **404** `{"error": "not found"}` instead.
- Disabling a module never deletes/mutates data.
- Payment **completion callbacks** (`/api/pay-attempt/success|failure`, `/api/customer-whish/success|failure`) are **never gated**: money may already have moved.
- `max_customers` and `whatsapp_api` stay plan limits (unchanged, still 402). `whish_customer_payments` is removed from `PLANS`.
- Migrations must be guarded with `sa.inspect(op.get_bind())` (production schema drifts from history).
- Run backend tests with: `python -m pytest -q` from repo root. Frontend tests: `cd frontend && npx react-scripts test --watchAll=false <pattern>`.
- Match surrounding code style; don't reformat unrelated code. Do not touch `.claude/worktrees/` or `.worktrees/`.
- Commit after each task with message ending in a blank line then `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

---

### Task 1: Module resolver + plan bundles

**Files:**
- Create: `modules.py`
- Modify: `plans.py` (add `modules` key per plan; remove `whish_customer_payments`)
- Test: `tests/test_modules.py`

**Interfaces:**
- Produces: `modules.ALWAYS_ON`, `modules.PAID`, `modules.REQUIRES`, `modules.license_provider` (module-level variable, default `None`; callable `(tenant) -> dict | None`), `modules.enabled_for(tenant) -> set[str]`, `modules.is_enabled(tenant, key) -> bool`, `modules.disabled_response(key)` → Flask `(response, 403)` tuple.
- Note: removing `whish_customer_payments` from `PLANS` breaks 3 call sites in app.py until Task 5. To keep the suite green in this task, **leave the key in PLANS for now** (Task 5 removes it). Only add the `modules` key here.

- [ ] **Step 1: Write the failing tests** — `tests/test_modules.py`:

```python
from types import SimpleNamespace
import pytest
import modules


def T(plan="free", overrides=None):
    return SimpleNamespace(plan=plan, module_overrides=overrides)


@pytest.fixture(autouse=True)
def _no_license(monkeypatch):
    monkeypatch.setattr(modules, "license_provider", None)


def test_free_bundle():
    assert modules.enabled_for(T("free")) == {"core", "office", "whatsapp", "network", "upstream_sync"}


def test_pro_bundle_is_everything():
    assert modules.enabled_for(T("pro")) == modules.ALWAYS_ON | modules.PAID


def test_unknown_plan_falls_back_to_free():
    assert modules.enabled_for(T("weird")) == modules.enabled_for(T("free"))


def test_override_adds_and_removes():
    got = modules.enabled_for(T("free", {"whish_payments": True, "network": False}))
    assert "whish_payments" in got and "network" not in got


def test_override_cannot_remove_always_on():
    got = modules.enabled_for(T("free", {"core": False, "office": False}))
    assert {"core", "office"} <= got


def test_unknown_override_keys_ignored():
    assert modules.enabled_for(T("free", {"bogus": True})) == modules.enabled_for(T("free"))


def test_ai_cs_requires_whatsapp():
    assert "ai_cs" not in modules.enabled_for(T("pro", {"whatsapp": False}))
    assert "ai_cs" in modules.enabled_for(T("free", {"ai_cs": True}))


def test_none_tenant_gets_only_always_on():
    assert modules.enabled_for(None) == set(modules.ALWAYS_ON)


def test_license_provider_takes_precedence_and_honours_expiry(monkeypatch):
    lic = {"modules": {
        "network": {"term": "yearly", "expires_at": "2999-01-01"},
        "whatsapp": {"term": "monthly", "expires_at": "2000-01-01"},
        "bogus": {"term": "yearly", "expires_at": "2999-01-01"},
    }}
    monkeypatch.setattr(modules, "license_provider", lambda t: lic)
    # plan/overrides ignored when a license is present
    assert modules.enabled_for(T("pro", {"ai_cs": True})) == {"core", "office", "network"}


def test_is_enabled():
    assert modules.is_enabled(T("free"), "network") is True
    assert modules.is_enabled(T("free"), "ai_cs") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_modules.py -q`
Expected: FAIL / error `ModuleNotFoundError: No module named 'modules'`.

- [ ] **Step 3: Implement** — create `modules.py`:

```python
"""ServiceBills feature modules: which ones a tenant has.

Single resolution point for module gating (see
docs/superpowers/specs/2026-09-30-module-gating-design.md). SaaS tenants get
their plan's bundle plus super-admin overrides; an on-prem install's signed
license (sub-project 2 fills `license_provider`) replaces both.
"""
from datetime import date

ALWAYS_ON = frozenset({"core", "office"})
PAID = frozenset({"whatsapp", "ai_cs", "network", "upstream_sync", "whish_payments"})
REQUIRES = {"ai_cs": frozenset({"whatsapp"})}

# Callable (tenant) -> license payload dict, or None when no license layer is
# active. Set by the on-prem license code; None on the SaaS.
license_provider = None


def _prune_requirements(mods):
    changed = True
    while changed:
        changed = False
        for key, needs in REQUIRES.items():
            if key in mods and not needs <= mods:
                mods.discard(key)
                changed = True
    return mods


def enabled_for(tenant):
    """Set of module keys enabled for `tenant` (None -> ALWAYS_ON only)."""
    if tenant is None:
        return set(ALWAYS_ON)
    lic = license_provider(tenant) if license_provider else None
    if lic is not None:
        today = date.today().isoformat()
        mods = set(ALWAYS_ON) | {
            k for k, v in (lic.get("modules") or {}).items()
            if k in PAID and isinstance(v, dict) and str(v.get("expires_at", "")) >= today
        }
    else:
        import plans
        mods = set(ALWAYS_ON) | (set(plans.limits(tenant.plan).get("modules", ())) & PAID)
        for key, on in (getattr(tenant, "module_overrides", None) or {}).items():
            if key not in PAID:
                continue
            if on is True:
                mods.add(key)
            elif on is False:
                mods.discard(key)
    return _prune_requirements(mods)


def is_enabled(tenant, key):
    return key in enabled_for(tenant)


def disabled_response(key):
    from flask import jsonify
    return jsonify(msg="Module not enabled", module=key), 403
```

In `plans.py`, add to `PLANS["free"]`: `"modules": ("whatsapp", "network", "upstream_sync"),` and to `PLANS["pro"]`: `"modules": ("whatsapp", "ai_cs", "network", "upstream_sync", "whish_payments"),`. Update the module docstring to mention `modules` (the bundle of paid modules the plan includes, see modules.py).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_modules.py tests/test_gating.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add modules.py plans.py tests/test_modules.py
git commit -m "feat(modules): module resolver and plan bundles"
```

---

### Task 2: `Tenant.module_overrides`, `/api/tenant/me` modules, `require_module` decorator

**Files:**
- Modify: `app.py` — `Tenant` model (~line 196-224)
- Create: `migrations/versions/a1b2c3d4e5f6_add_tenant_module_overrides.py`
- Modify: `tenancy.py` (add `require_module`)
- Test: `tests/test_module_gating_api.py`

**Interfaces:**
- Consumes: `modules.enabled_for`, `modules.is_enabled`, `modules.disabled_response` (Task 1).
- Produces: `Tenant.module_overrides` (JSON, nullable); `Tenant.to_dict()["modules"]` (sorted list); `tenancy.require_module(key)` decorator factory, used as `@require_module('network')` placed **after** `@jwt_required()` and the role decorator (i.e. innermost, just above `def`). Test helper pattern `_set_overrides(app, slug, dict)` used by later tasks.

- [ ] **Step 1: Write failing tests** — `tests/test_module_gating_api.py`:

```python
import app as appmod
from tests.conftest import make_tenant


def _set_overrides(app, slug, overrides):
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug=slug).first()
        t.module_overrides = overrides
        appmod.db.session.commit()


def test_tenant_me_lists_modules(app, client):
    hdr = make_tenant(client, "Mod A", "moda_admin")
    mods = client.get("/api/tenant/me", headers=hdr).get_json()["modules"]
    assert mods == sorted(["core", "office", "whatsapp", "network", "upstream_sync"])


def test_tenant_me_reflects_overrides(app, client):
    hdr = make_tenant(client, "Mod B", "modb_admin")
    _set_overrides(app, "mod-b", {"network": False, "whish_payments": True})
    mods = client.get("/api/tenant/me", headers=hdr).get_json()["modules"]
    assert "network" not in mods and "whish_payments" in mods


def test_require_module_decorator(app, client):
    from flask_jwt_extended import jwt_required
    from tenancy import require_module

    @jwt_required()
    @require_module("network")
    def _probe():
        return "ok", 200

    appmod.app.add_url_rule("/api/_probe_network", "_probe_network", _probe)
    hdr = make_tenant(client, "Mod C", "modc_admin")
    assert client.get("/api/_probe_network", headers=hdr).status_code == 200
    _set_overrides(app, "mod-c", {"network": False})
    r = client.get("/api/_probe_network", headers=hdr)
    assert r.status_code == 403
    assert r.get_json() == {"msg": "Module not enabled", "module": "network"}
```

If `add_url_rule` fails because the app has already handled a request ("setup method called after first request"), instead register the probe route at import time inside the test module (define it at module top-level before any fixture runs) — keep the assertions identical.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_module_gating_api.py -q`
Expected: FAIL (`KeyError: 'modules'` / `ImportError: require_module`).

- [ ] **Step 3: Implement**

In `app.py` `Tenant` model, after `public_pay_slug`:

```python
    # Super-admin per-tenant module overrides on top of the plan bundle,
    # e.g. {"network": true, "ai_cs": false}. NULL = none. See modules.py.
    module_overrides = db.Column(db.JSON, nullable=True)
```

In `Tenant.to_dict()` add `"modules": sorted(modules.enabled_for(self)),` — import `modules` at the top of `app.py` next to `import plans` (find it with `grep -n "^import plans" app.py`).

Migration file (check the current head first with `flask db heads`; at plan time it was `f6c8a3d1b2e4` — use whatever `heads` prints as `down_revision`):

```python
"""add tenant.module_overrides

Revision ID: a1b2c3d4e5f6
Revises: f6c8a3d1b2e4
Create Date: 2026-09-30 00:00:00.000000

Guarded because production's real schema drifts from migration history.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'f6c8a3d1b2e4'
branch_labels = None
depends_on = None


def _cols(table):
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if 'module_overrides' not in _cols('tenant'):
        with op.batch_alter_table('tenant', schema=None) as batch_op:
            batch_op.add_column(sa.Column('module_overrides', sa.JSON(), nullable=True))


def downgrade():
    if 'module_overrides' in _cols('tenant'):
        with op.batch_alter_table('tenant', schema=None) as batch_op:
            batch_op.drop_column('module_overrides')
```

In `tenancy.py`, append:

```python
def require_module(key):
    """Decorator factory: 403 unless the current tenant has module `key`.

    Stack it innermost (just above `def`), after @jwt_required() and any role
    decorator, mirroring app.network_view_required()."""
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            verify_jwt_in_request()
            import modules
            if not modules.is_enabled(current_tenant(), key):
                return modules.disabled_response(key)
            return fn(*args, **kwargs)
        return decorator
    return wrapper
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_module_gating_api.py tests/test_modules.py tests/test_billing.py -q`
Expected: PASS. Then run the migration check: `python -m pytest tests/test_inbox_migration.py -q` (existing migration test style) still passes.

- [ ] **Step 5: Commit**

```bash
git add app.py tenancy.py migrations/versions/a1b2c3d4e5f6_add_tenant_module_overrides.py tests/test_module_gating_api.py
git commit -m "feat(modules): Tenant.module_overrides, modules in /api/tenant/me, require_module decorator"
```

---

### Task 3: Gate `network` and `upstream_sync` routes

**Files:**
- Modify: `app.py` (routes listed below; add `from tenancy import require_module` to the existing tenancy import line)
- Test: `tests/test_module_gating_routes.py`

**Interfaces:**
- Consumes: `require_module` (Task 2), `modules.is_enabled`, `modules.disabled_response`.

Routes — add `@require_module('network')` innermost to every one of these (locate with `grep -n "@app.route('/api/network-" app.py` etc.):
`get_network_devices`, `create_network_device`, `update_network_device`, `delete_network_device`, `check_network_device_now`, `test_network_device_connection`, `set_network_device_interface_label`, `get_network_job`, `list_network_agents`, `create_network_agent`, `regenerate_network_agent_token`, `get_network_tree`, `refresh_olt_onus`, `get_onu_label_matches`, `apply_onu_label_matches`, `locate_customers`, `apply_customer_locations`, `get_network_map_olts`, `get_network_map`, `get_unplaced_onus`, `create_network_node`, `update_network_node`, `delete_network_node`, `get_customer_network_status`, `suspend_customer_network`, `unsuspend_customer_network`.

Agent routes (`agent_poll_job`, `agent_post_result`) have no JWT; they set `g.network_agent`. At the top of each handler body add:

```python
    if not modules.is_enabled(db.session.get(Tenant, g.network_agent.tenant_id), 'network'):
        return modules.disabled_response('network')
```

(Verify the attribute name on the agent model is `tenant_id` with `grep -n "class NetworkAgent" -A 15 app.py`.)

`@require_module('upstream_sync')` on: `get_upstream_providers`, `create_upstream_provider`, `update_upstream_provider`, `delete_upstream_provider`, `get_upstream_provider_history`, `topup_upstream_provider`, `record_upstream_renewal_cost`, `sync_customer_upstream_status`.

- [ ] **Step 1: Write failing tests** — `tests/test_module_gating_routes.py`:

```python
import app as appmod
from tests.conftest import make_tenant
from tests.test_module_gating_api import _set_overrides


def _off(app, client, name, slug, module):
    hdr = make_tenant(client, name, slug.replace("-", "_") + "_admin")
    _set_overrides(app, slug, {module: False})
    return hdr


def _assert_blocked(r, module):
    assert r.status_code == 403, r.get_data(as_text=True)
    assert r.get_json()["module"] == module


def test_network_routes_blocked(app, client):
    hdr = _off(app, client, "Net Off", "net-off", "network")
    for path in ("/api/network-devices", "/api/network-tree", "/api/network-map", "/api/network-agents"):
        _assert_blocked(client.get(path, headers=hdr), "network")


def test_network_routes_open_when_enabled(app, client):
    hdr = make_tenant(client, "Net On", "neton_admin")
    assert client.get("/api/network-devices", headers=hdr).status_code == 200


def test_upstream_routes_blocked(app, client):
    hdr = _off(app, client, "Up Off", "up-off", "upstream_sync")
    _assert_blocked(client.get("/api/upstream-providers", headers=hdr), "upstream_sync")


def test_upstream_routes_open_when_enabled(app, client):
    hdr = make_tenant(client, "Up On", "upon_admin")
    assert client.get("/api/upstream-providers", headers=hdr).status_code == 200


def test_agent_poll_blocked_when_network_off(app, client):
    # Build an agent the same way tests/test_network_agent_api.py does
    # (copy its helper for creating an agent + token), then disable network
    # for that tenant and assert GET /api/agent/jobs returns 403 module=network.
    hdr = make_tenant(client, "Agent Off", "agentoff_admin")
    r = client.post("/api/network-agents", headers=hdr, json={"name": "a1"})
    token = r.get_json().get("token") or r.get_json().get("agent", {}).get("token")
    assert token, r.get_json()
    _set_overrides(app, "agent-off", {"network": False})
    r = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"})
    _assert_blocked(r, "network")
```

Before finalising `test_agent_poll_blocked_when_network_off`, read `tests/test_network_agent_api.py` for the exact agent-create response shape and auth header the agent uses (it may be `X-Agent-Token` rather than `Authorization`); adjust the two marked lines to match — keep the final assertion.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_module_gating_routes.py -q`
Expected: the `_blocked` tests FAIL (200 instead of 403).

- [ ] **Step 3: Implement** — add the decorators/checks listed above.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_module_gating_routes.py -q && python -m pytest -q -k "network or upstream or mikrotik or topology or onu or cpe"`
Expected: PASS (existing network tests use free-plan tenants, which include `network` and `upstream_sync`).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_module_gating_routes.py
git commit -m "feat(modules): gate network and upstream_sync routes"
```

---

### Task 4: Gate `whatsapp` and `ai_cs`

**Files:**
- Modify: `app.py`, `whatsapp_inbox_routes.py`
- Test: `tests/test_module_gating_whatsapp_ai.py`

**Interfaces:**
- Consumes: `require_module`, `modules.is_enabled`, `modules.disabled_response`.

`@require_module('whatsapp')` innermost on: `get_whatsapp_settings`, `get_whatsapp_deeplink_settings`, `save_whatsapp_settings`, `subscribe_waba`, `get_whatsapp_templates`, `sync_whatsapp_templates`, `create_whatsapp_template`, `update_whatsapp_template`, `delete_whatsapp_template`, `upload_whatsapp_template_sample`, `create_payment_reminder`, `trigger_whatsapp_reminder`, `send_bulk_messages`, and every route registered in `whatsapp_inbox_routes.register_inbox_routes` (import `require_module` from `tenancy` there and stack it under `@inbox_admin` or equivalent on each).

**Careful:** `get_whatsapp_deeplink_settings` may be used by core payment screens to build a wa.me link (cashier collect flow). Check its frontend callers with `grep -rn "deeplink" frontend/src`. If a core view calls it, do NOT gate it; instead make it return `{"enabled": false}` (same shape as "not configured") when the module is off. Note which you did in the commit message.

Webhook `whatsapp_webhook` (public, Meta): find where it resolves the tenant from the payload (search inside the function for `tenant_id`). Right after resolution, if `not modules.is_enabled(tenant, 'whatsapp')`, skip processing for that entry and continue/return `200` (Meta must get 200 or it retries). Where the webhook hands a message to the AI agent (search the function for the call into the CS agent / Gemini brain), additionally require `modules.is_enabled(tenant, 'ai_cs')`; if off, the message is still stored in the Inbox as usual but no AI reply is generated.

`ai_cs` routes (no decorators — they call `_require_authenticated_tenant_id(appmod)` or `cs_agent_tools.resolve_tenant_id(appmod)`). In each of `cs_agent_config`, `cs_tool_lookup_customer`, `cs_tool_customer_status`, `cs_tool_network_diagnostic`, `cs_tool_send_payment_link`, `cs_tool_escalate`, `cs_get_recent_tickets`, `cs_agent_memory`, `cs_agent_memory_recent_logs`, `cs_agent_memory_update`, `cs_agent_memory_delete`, immediately after the `if not tenant_id: return ... 401` guard add:

```python
    if not modules.is_enabled(db.session.get(Tenant, tenant_id), 'ai_cs'):
        return modules.disabled_response('ai_cs')
```

Cross-module tools — after the `ai_cs` check:
- `cs_tool_network_diagnostic`: if `network` is off, return `jsonify({"available": False, "message": "Network diagnostics are not available for this business. Offer to open a support ticket instead."}), 200`.
- `cs_tool_send_payment_link`: if `whish_payments` is off, return `jsonify({"available": False, "message": "Online payment links are not available for this business. Tell the customer how to pay at the office instead."}), 200`.

- [ ] **Step 1: Write failing tests** — `tests/test_module_gating_whatsapp_ai.py`:

```python
import app as appmod
from tests.conftest import make_tenant
from tests.test_module_gating_api import _set_overrides


def test_whatsapp_templates_blocked(app, client):
    hdr = make_tenant(client, "Wa Off", "waoff_admin")
    _set_overrides(app, "wa-off", {"whatsapp": False})
    r = client.get("/api/whatsapp/templates", headers=hdr)
    assert r.status_code == 403 and r.get_json()["module"] == "whatsapp"


def test_whatsapp_templates_open_on_free(app, client):
    hdr = make_tenant(client, "Wa On", "waon_admin")
    assert client.get("/api/whatsapp/templates", headers=hdr).status_code == 200


def test_inbox_blocked(app, client):
    hdr = make_tenant(client, "Inbox Off", "inboxoff_admin")
    _set_overrides(app, "inbox-off", {"whatsapp": False})
    r = client.get("/api/whatsapp/inbox/summary", headers=hdr)
    assert r.status_code == 403


def test_cs_agent_config_needs_ai_cs(app, client):
    hdr = make_tenant(client, "Ai Off", "aioff_admin")   # free: no ai_cs
    r = client.get("/api/cs-agent/config", headers=hdr)
    assert r.status_code == 403 and r.get_json()["module"] == "ai_cs"
    _set_overrides(app, "ai-off", {"ai_cs": True})
    assert client.get("/api/cs-agent/config", headers=hdr).status_code == 200


def test_network_diagnostic_tool_unavailable_without_network(app, client):
    hdr = make_tenant(client, "Ai Net", "ainet_admin")
    _set_overrides(app, "ai-net", {"ai_cs": True, "network": False})
    r = client.post("/api/cs-agent/tools/network-diagnostic", headers=hdr, json={"phone": "1"})
    assert r.status_code == 200 and r.get_json()["available"] is False
```

Confirm the inbox summary path with `grep -n "inbox/summary" whatsapp_inbox_routes.py`; if different, use any inbox GET route. For the webhook, add one test mirroring the setup in `tests/test_iso_webhook.py` (copy its payload-building helper): with `whatsapp` disabled for the tenant, POST the webhook payload and assert status 200 and that no `WhatsApp` inbox message/conversation row was created for that tenant (use the same model/assertion that `test_iso_webhook.py` uses to prove processing).

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_module_gating_whatsapp_ai.py -q`
Expected: blocked tests FAIL.

- [ ] **Step 3: Implement** as described above.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_module_gating_whatsapp_ai.py -q && python -m pytest -q -k "whatsapp or inbox or webhook or cs_agent or template"`
Expected: PASS. Existing CS-agent tests use free tenants → they will now 403. Fix them by enabling `ai_cs` for their tenant (set `plan = "pro"` or `module_overrides = {"ai_cs": True}` in their setup helpers), not by weakening the gate. List the fixed test files in the commit message.

- [ ] **Step 5: Commit**

```bash
git add app.py whatsapp_inbox_routes.py tests/
git commit -m "feat(modules): gate whatsapp and ai_cs routes, webhook and AI tools"
```

---

### Task 5: `whish_payments` module replaces the plan flag

**Files:**
- Modify: `app.py`, `plans.py`
- Modify: `tests/test_tenant_whish_customer_payments.py` (and any other test asserting 402 for whish gating — find with `grep -rn "whish_customer_payments\|== 402" tests/`)
- Test: `tests/test_module_gating_whish.py`

**Interfaces:**
- Consumes: `require_module`, `modules.is_enabled`.

Changes:
1. Replace the three `plans.limits(...)["whish_customer_payments"]` checks (in `save_tenant_whish_settings`, `regenerate_public_pay_link`, `email_customer_payment_link` — find with `grep -n "whish_customer_payments" app.py`) with `@require_module('whish_payments')` on the route and delete the inline check.
2. `@require_module('whish_payments')` on: `customer_whish_payments_report`, `get_tenant_whish_settings`, `save_tenant_whish_settings`, `get_public_pay_link`, `regenerate_public_pay_link`, `resend_customer_payment_link`, `email_customer_payment_link`.
3. Public entry pages return 404 when the owning tenant lacks the module: in `public_tenant_pay_branding`, `public_tenant_pay_lookup`, `public_tenant_pay_checkout` after `tenant = ...` lookup: `if not tenant or not modules.is_enabled(tenant, 'whish_payments'): return jsonify({"error": "not found"}), 404` (merge with the existing `if not tenant` guard). In `public_pay_view` and `public_pay_checkout`, after loading `link`, resolve its tenant (`db.session.get(Tenant, link.tenant_id)`) and return the function's existing invalid-link response when the module is off (keep the "no enumeration" property — same shape/status as an invalid token).
4. Do **not** touch `customer_whish_attempt_success/failure`, `customer_whish_success/failure`.
5. Remove `"whish_customer_payments"` from both plans in `plans.py`; update its docstring.

- [ ] **Step 1: Write failing tests** — `tests/test_module_gating_whish.py`:

```python
import app as appmod
from tests.conftest import make_tenant
from tests.test_module_gating_api import _set_overrides


def test_whish_settings_blocked_on_free(app, client):
    hdr = make_tenant(client, "Wh Free", "whfree_admin")
    r = client.get("/api/tenant-whish-settings", headers=hdr)
    assert r.status_code == 403 and r.get_json()["module"] == "whish_payments"


def test_whish_settings_open_with_override(app, client):
    hdr = make_tenant(client, "Wh On", "whon_admin")
    _set_overrides(app, "wh-on", {"whish_payments": True})
    assert client.get("/api/tenant-whish-settings", headers=hdr).status_code == 200


def test_public_pay_page_404_when_module_off(app, client):
    make_tenant(client, "Wh Pub", "whpub_admin")
    with app.app_context():
        t = appmod.Tenant.query.filter_by(slug="wh-pub").first()
        t.public_pay_slug = "pubslug1"
        t.module_overrides = {"whish_payments": True}
        appmod.db.session.commit()
    assert client.get("/api/pay/t/pubslug1").status_code == 200
    _set_overrides(app, "wh-pub", {"whish_payments": False})
    assert client.get("/api/pay/t/pubslug1").status_code == 404


def test_plans_no_longer_carry_whish_flag():
    import plans
    assert all("whish_customer_payments" not in p for p in plans.PLANS.values())
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_module_gating_whish.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** as above. Update existing tests that expected `402` for whish gating to expect `403` with `module == "whish_payments"`; tests that make a tenant `pro` to use whish keep working (pro bundle includes it).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_module_gating_whish.py -q && python -m pytest -q -k "whish or pay"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py plans.py tests/
git commit -m "feat(modules): whish_payments module replaces plan flag; public pages 404 when off"
```

---

### Task 6: Scheduled jobs skip tenants without the module

**Files:**
- Modify: `app.py` — `send_daily_whatsapp_keepalive(tenant_id)` (~3255), `auto_sync_upstream_status_for_tenant(tenant_id)` (~3421), `refresh_agent_mode_network_status_for_tenant(tenant_id)` (~3538)
- Test: `tests/test_module_gating_jobs.py`

**Interfaces:**
- Consumes: `modules.is_enabled`.

At the top of each per-tenant function add (with the matching key):

```python
    if not modules.is_enabled(db.session.get(Tenant, tenant_id), 'whatsapp'):
        return
```

(`'upstream_sync'` for `auto_sync_upstream_status_for_tenant`, `'network'` for `refresh_agent_mode_network_status_for_tenant`.) If a function's normal return value is a dict/summary rather than `None`, return the same "nothing to do" value its existing early-exit paths return — read the function first.

- [ ] **Step 1: Write failing tests** — `tests/test_module_gating_jobs.py`:

```python
import app as appmod
from tests.conftest import make_tenant
from tests.test_module_gating_api import _set_overrides


def _tid(app, slug):
    with app.app_context():
        return appmod.Tenant.query.filter_by(slug=slug).first().id


def test_keepalive_skips_without_whatsapp(app, client, monkeypatch):
    make_tenant(client, "Job Wa", "jobwa_admin")
    _set_overrides(app, "job-wa", {"whatsapp": False})
    calls = []
    # SEND_FN: the function send_daily_whatsapp_keepalive calls to actually
    # send (read the function; e.g. a send_whatsapp_* helper). Replace the
    # name below with it.
    monkeypatch.setattr(appmod, "SEND_FN", lambda *a, **k: calls.append(a), raising=True)
    with app.app_context():
        appmod.send_daily_whatsapp_keepalive(_tid(app, "job-wa"))
    assert calls == []
```

Replace `SEND_FN` with the real name before running (the test must fail before the fix only because the send happens — so also set up whatever WhatsApp settings the function needs to reach that send, copying from `tests/test_iso_scheduler.py`). Write the equivalent test for `auto_sync_upstream_status_for_tenant` (monkeypatch the upstream sync call it makes) and `refresh_agent_mode_network_status_for_tenant` (monkeypatch the job-enqueue it makes), each with its module disabled. Look at `tests/test_iso_scheduler.py` for existing patterns and reuse them.

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_module_gating_jobs.py -q` → FAIL.

- [ ] **Step 3: Implement** the three early returns.

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_module_gating_jobs.py tests/test_iso_scheduler.py tests/test_iso_scheduler_payroll.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_module_gating_jobs.py
git commit -m "feat(modules): scheduled jobs skip tenants without the module"
```

---

### Task 7: Super-admin module overrides API

**Files:**
- Modify: `app.py` — near `admin_set_plan` (~2609) and the tenant list route `GET /api/admin/tenants` (~2568)
- Test: `tests/test_admin_modules.py`

**Interfaces:**
- Produces: `POST /api/admin/tenants/<int:tid>/modules` body `{"overrides": {key: true|false|null}}` → 200 `{"tenant": Tenant.to_dict() + "module_overrides"}`; 400 `{"msg": ...}` for unknown or always-on keys or non-bool/null values; 404 unknown tenant. `GET /api/admin/tenants` rows include `modules` (via to_dict) and `module_overrides` (`{}` when NULL).

Implementation:

```python
@app.route('/api/admin/tenants/<int:tid>/modules', methods=['POST'])
@superadmin_required
def admin_set_modules(tid):
    t = db.session.get(Tenant, tid)
    if not t:
        return jsonify({"msg": "Tenant not found"}), 404
    incoming = (request.get_json(silent=True) or {}).get("overrides")
    if not isinstance(incoming, dict):
        return jsonify({"msg": "overrides must be an object"}), 400
    current = dict(t.module_overrides or {})
    for key, val in incoming.items():
        if key in modules.ALWAYS_ON:
            return jsonify({"msg": f"{key} is always on"}), 400
        if key not in modules.PAID:
            return jsonify({"msg": f"Unknown module: {key}"}), 400
        if val is None:
            current.pop(key, None)
        elif isinstance(val, bool):
            current[key] = val
        else:
            return jsonify({"msg": f"{key}: expected true, false or null"}), 400
    t.module_overrides = current or None
    db.session.commit()
    return jsonify({"tenant": {**t.to_dict(), "module_overrides": t.module_overrides or {}}}), 200
```

In the `GET /api/admin/tenants` handler, add `"module_overrides": t.module_overrides or {}` to each row dict (read how rows are built there first).

- [ ] **Step 1: Write failing tests** — `tests/test_admin_modules.py`. Reuse the super-admin login helper from `tests/test_admin_plan_grant.py` (read it and copy its fixture/helper, e.g. creating the superadmin user with `role="superadmin", tenant_id=None`):

```python
import app as appmod
from tests.conftest import make_tenant
# from tests.test_admin_plan_grant import <superadmin headers helper>  -- use its real name


def _tid(app, slug):
    with app.app_context():
        return appmod.Tenant.query.filter_by(slug=slug).first().id


def test_set_and_clear_override(app, client, sa_headers):
    make_tenant(client, "Adm M", "admm_admin")
    tid = _tid(app, "adm-m")
    r = client.post(f"/api/admin/tenants/{tid}/modules", headers=sa_headers,
                    json={"overrides": {"ai_cs": True, "network": False}})
    assert r.status_code == 200
    body = r.get_json()["tenant"]
    assert "ai_cs" in body["modules"] and "network" not in body["modules"]
    assert body["module_overrides"] == {"ai_cs": True, "network": False}
    r = client.post(f"/api/admin/tenants/{tid}/modules", headers=sa_headers,
                    json={"overrides": {"network": None}})
    assert r.get_json()["tenant"]["module_overrides"] == {"ai_cs": True}


def test_rejects_always_on_and_unknown(app, client, sa_headers):
    make_tenant(client, "Adm N", "admn_admin")
    tid = _tid(app, "adm-n")
    for bad in ({"core": False}, {"bogus": True}, {"network": "yes"}):
        assert client.post(f"/api/admin/tenants/{tid}/modules", headers=sa_headers,
                           json={"overrides": bad}).status_code == 400


def test_tenant_admin_cannot_call(app, client):
    hdr = make_tenant(client, "Adm O", "admo_admin")
    tid = _tid(app, "adm-o")
    assert client.post(f"/api/admin/tenants/{tid}/modules", headers=hdr,
                       json={"overrides": {"ai_cs": True}}).status_code == 403


def test_admin_tenant_list_has_overrides(app, client, sa_headers):
    make_tenant(client, "Adm P", "admp_admin")
    rows = client.get("/api/admin/tenants", headers=sa_headers).get_json()
    rows = rows if isinstance(rows, list) else rows.get("tenants", [])
    row = next(r for r in rows if r["slug"] == "adm-p")
    assert row["module_overrides"] == {} and "modules" in row
```

Define `sa_headers` as a pytest fixture in this file wrapping the helper from `test_admin_plan_grant.py`.

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_admin_modules.py -q` → FAIL (404/405).

- [ ] **Step 3: Implement** as above.

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_admin_modules.py tests/test_admin_plan_grant.py -q` → PASS. Then full suite: `python -m pytest -q` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_admin_modules.py
git commit -m "feat(modules): super-admin module overrides API"
```

---

### Task 8: Frontend — `hasModule`, nav/tab filtering, module-403 snackbar

**Files:**
- Create: `frontend/src/utils/navFilter.js`, `frontend/src/utils/navFilter.test.js`
- Modify: `frontend/src/context/AppContext.js`, `frontend/src/App.js`, `frontend/src/components/SettingsView.js`, `frontend/src/components/MessagingView.js`, `frontend/src/components/SubscriptionsView.js` (customer-row actions)

**Interfaces:**
- Consumes: `/api/tenant/me` → `{..., modules: string[]}`; 403 body `{msg, module}`.
- Produces: context values `modules` (`null` while loading, else array) and `hasModule(key) -> bool` (always `true` for `core`/`office`; `false` while loading for paid ones). `filterNavItems(items, { hasRole, businessSettings, hasModule }) -> items`.

- [ ] **Step 1: Write failing test** — `frontend/src/utils/navFilter.test.js`:

```javascript
import { filterNavItems } from './navFilter';

const items = [
    { key: 'dashboard', allowedRoles: ['admin'] },
    { key: 'network-tree', allowedRoles: ['admin'], module: 'network' },
    { key: 'upstream-providers', allowedRoles: ['admin'], module: 'upstream_sync', visibleWhen: (bs) => bs?.network_mode === 'upstream_bridge' },
    { key: 'employees', allowedRoles: ['finance'] },
];
const hasRole = (r) => r === 'admin';

test('hides items whose module is off', () => {
    const got = filterNavItems(items, { hasRole, businessSettings: { network_mode: 'upstream_bridge' }, hasModule: (m) => m !== 'network' });
    expect(got.map(i => i.key)).toEqual(['dashboard', 'upstream-providers']);
});

test('still applies roles and visibleWhen', () => {
    const got = filterNavItems(items, { hasRole, businessSettings: {}, hasModule: () => true });
    expect(got.map(i => i.key)).toEqual(['dashboard', 'network-tree']);
});
```

- [ ] **Step 2: Run to verify failure** — `cd frontend && npx react-scripts test --watchAll=false navFilter` → FAIL (module not found).

- [ ] **Step 3: Implement**

`frontend/src/utils/navFilter.js`:

```javascript
// Nav visibility: role, optional per-tenant visibleWhen(businessSettings),
// and optional feature module (see modules.py on the backend).
export const filterNavItems = (items, { hasRole, businessSettings, hasModule }) =>
    items.filter(item =>
        (!item.allowedRoles || item.allowedRoles.some(r => hasRole(r))) &&
        (!item.visibleWhen || item.visibleWhen(businessSettings)) &&
        (!item.module || hasModule(item.module))
    );
```

`AppContext.js`:
- In the axios response interceptor, next to the 402 branch add:

```javascript
        // Feature module not enabled for this tenant (modules.py).
        if (status === 403 && error.response?.data?.module) {
            window.dispatchEvent(new CustomEvent('sb:module-disabled', {
                detail: { module: error.response.data.module }
            }));
        }
```

- In `AppContextProvider`: add `const [modules, setModules] = useState(null);`, an effect that on `token` change calls `apiService.tenantMe().then(r => setModules(r.data.modules || [])).catch(() => setModules([]))` (and `setModules(null)` when token is null), a listener for `sb:module-disabled` that shows `setSnackbar({ open: true, message: "This feature isn't included in your plan.", severity: 'warning' })`, and:

```javascript
    const hasModule = (key) => key === 'core' || key === 'office' || (Array.isArray(modules) && modules.includes(key));
```

  Add `modules`, `hasModule`, and `refreshModules` (re-runs the tenantMe fetch) to `value`.

`App.js`:
- Import `filterNavItems`; add `module:` to NAV_ITEMS: `upstream-providers` → `'upstream_sync'`, `network-devices`/`network-tree`/`network-map` → `'network'`, `messaging` → `'whatsapp'`, `cs-agent-voice` → `'ai_cs'`.
- Replace the inline `NAV_ITEMS.filter(...)` with `filterNavItems(NAV_ITEMS, { hasRole, businessSettings, hasModule })`, taking `hasModule` from `useAppContext()`.
- If `currentView` is not among visible `navItems` once `modules !== null`, fall back to `getDefaultView()` (prevents a deep link `?view=network-tree` rendering a gated page).

`SettingsView.js`: convert index-based tabs to key-based:

```javascript
    const { hasModule } = useAppContext();   // add to the existing destructure
    const SETTINGS_TABS = [
        { key: 'business', label: 'Business Details', icon: <BusinessIcon sx={{ fontSize: 18 }} /> },
        { key: 'wa-notifications', label: 'WhatsApp Notifications', icon: <WhatsAppIcon sx={{ fontSize: 18 }} />, module: 'whatsapp' },
        { key: 'wa-templates', label: 'WhatsApp Templates', icon: <WhatsAppIcon sx={{ fontSize: 18 }} />, module: 'whatsapp' },
        { key: 'whish', label: 'Whish Payments', icon: <PaymentsIcon sx={{ fontSize: 18 }} />, module: 'whish_payments' },
        { key: 'expense-categories', label: 'Expense Categories', icon: <MessageIcon sx={{ fontSize: 18 }} /> },
        { key: 'users', label: 'User Management', icon: <PeopleIcon sx={{ fontSize: 18 }} /> },
        { key: 'sectors', label: 'Sectors', icon: <LocationOnIcon sx={{ fontSize: 18 }} /> },
    ].filter(t => !t.module || hasModule(t.module));
```

  `tab` state holds a key (default `'business'`); `<Tabs value={SETTINGS_TABS.some(t => t.key === tab) ? tab : 'business'}>` renders `SETTINGS_TABS.map(t => <Tab key={t.key} value={t.key} icon={t.icon} iconPosition="start" label={t.label} />)`; replace each `tab === N` with the matching key (0 business, 1 wa-notifications, 2 wa-templates, 3 whish, 4 expense-categories, 5 users, 6 sectors). Check for any other code that sets `setTab(<number>)` and convert it. Replace `const isPro = tenant?.plan === 'pro';` with `const isPro = hasModule('whish_payments');` and remove the now-unused local `tenant` fetch only if nothing else uses `tenant`.

`MessagingView.js`: the whole view is `whatsapp`-gated by nav; no tab change needed.

`SubscriptionsView.js`: hide the network status chips and suspend/unsuspend actions when `!hasModule('network')`, the upstream-sync action when `!hasModule('upstream_sync')`, Whish link resend/email when `!hasModule('whish_payments')`, and WhatsApp reminder send when `!hasModule('whatsapp')` — find these with `grep -n "network-status\|networkStatus\|suspend\|upstream\|whish\|reminder" frontend/src/components/SubscriptionsView.js` and wrap their render conditions. The existing `shouldShowNetworkStatusChips` helper (tested in `SubscriptionsView.shouldShowNetworkStatusChips.test.js`) should gain a `hasNetworkModule` argument; update that test with one case asserting `false` when the module is off.

- [ ] **Step 4: Run tests**

Run: `cd frontend && npx react-scripts test --watchAll=false` → all PASS. Then `npm run build` (from `frontend/`) → compiles without new warnings-as-errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(modules): frontend hasModule, gated nav/tabs/actions, module-disabled snackbar"
```

---

### Task 9: Super-admin Modules dialog

**Files:**
- Modify: `frontend/src/context/AppContext.js` (add `adminSetModules`), `frontend/src/components/SuperAdminView.js`

**Interfaces:**
- Consumes: `POST /api/admin/tenants/<id>/modules`, `GET /api/admin/tenants` rows with `modules`, `module_overrides`, `plan`.
- Produces: `apiService.adminSetModules(id, overrides)`.

- [ ] **Step 1: Add API method** in `rawApiService` next to `adminSetPlan`:

```javascript
    adminSetModules: (id, overrides) => api.post(`/admin/tenants/${id}/modules`, { overrides }),
```

- [ ] **Step 2: Add the dialog** in `SuperAdminView.js`: a "Modules" button on each tenant row (next to the plan buttons) opens a MUI `Dialog` listing the paid modules:

```javascript
const PAID_MODULES = [
    { key: 'whatsapp', label: 'WhatsApp' },
    { key: 'ai_cs', label: 'AI customer service (needs WhatsApp)' },
    { key: 'network', label: 'Network' },
    { key: 'upstream_sync', label: 'Upstream sync' },
    { key: 'whish_payments', label: 'Whish customer payments' },
];
```

  Each row: a `Switch` checked when `row.modules.includes(key)`; a caption `"override"` if `key in row.module_overrides`, else `"from plan"`; a small "reset" link (sends `{[key]: null}`) shown only for overridden keys. Toggling sends `{[key]: newValue}` via `adminSetModules`, then calls `load()` and updates the dialog's tenant from the response. Show errors with the existing snackbar/alert pattern in this view. Core and Office are shown as disabled, always-checked switches labelled "always on".

- [ ] **Step 3: Verify manually in the browser**

Start the dev server with preview tools (see memory: pin cwd + DATABASE_URL to a scratch DB, not the user's dev DB), log in as super-admin, toggle `network` off for a tenant, log in as that tenant: Network items gone from nav; toggle back: they return.

- [ ] **Step 4: Run frontend tests + build** — `cd frontend && npx react-scripts test --watchAll=false && npm run build` → PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(modules): super-admin Modules dialog"
```

---

## Final verification (orchestrator)

- `python -m pytest -q` full suite green.
- `cd frontend && npx react-scripts test --watchAll=false` green.
- Grep: `grep -n "whish_customer_payments" app.py plans.py` → no matches.
- Spot-check that every route in the spec's route map carries the gate: `grep -n "require_module" app.py whatsapp_inbox_routes.py | wc -l` ≥ 55.
