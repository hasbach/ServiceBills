# Module Gating — Design (ServiceBills sub-project 1 of 3)

Date: 2026-09-30
Status: approved in brainstorming, pending spec review
Related: `2026-09-30-onprem-runtime-license-design.md` (sub-project 2),
`2026-09-30-onprem-installer-updater-design.md` (sub-project 3)

## Goal

Split ServiceBills into modules that can be enabled per tenant, enforced on
both backend and frontend, with one resolution function shared by the SaaS
(plan bundles + super-admin overrides) and the on-prem edition (signed license,
added in sub-project 2). Existing SaaS tenants must see **no change** in what
they can access on the day this ships.

## Modules

| Key | Contents | Rule |
|---|---|---|
| `core` | customers, subscription plans, payments, receipts, reports, resellers, sectors, service desk (statuses, tickets, outages, feedback), business settings, users, dashboard, the tenant's own ServiceBills billing (`/api/billing/*`, `/api/tenant/me`, `/api/plans`) | always on |
| `office` | cashier role, employees & payroll, expenses & expense categories, suppliers | always on |
| `whatsapp` | WhatsApp settings (incl. forwarding), templates, Inbox, payment reminders, bulk send, webhook | paid |
| `ai_cs` | CS AI agent (`/api/cs-agent/*`), ElevenLabs voice, agent memory | paid, requires `whatsapp` |
| `network` | network devices, health, topology tree, fibre map, CPE-MAC, network agent relay, customer network status/suspend | paid |
| `upstream_sync` | upstream providers, RADIUS portal status sync | paid |
| `whish_payments` | tenant Whish settings, public pay links/pages, customer Whish payments, whish-link resend/email, customer-whish-payments report | paid |

`core` and `office` are "always on" in the SaaS. On-prem their availability is
governed by the base license (sub-project 2), which can put the whole app in
read-only mode — that is a separate mechanism, not module gating.

Limits stay limits, not modules: `max_customers` and `whatsapp_api` remain in
`plans.PLANS`. The existing `whish_customer_payments` plan flag is replaced by
the `whish_payments` module.

## Resolution — `modules.py` (new)

```python
ALWAYS_ON = {"core", "office"}
PAID = {"whatsapp", "ai_cs", "network", "upstream_sync", "whish_payments"}
REQUIRES = {"ai_cs": {"whatsapp"}}

def enabled_for(tenant) -> set[str]
def is_enabled(tenant, key) -> bool
```

Algorithm:

1. If an on-prem license provider is active (hook filled by sub-project 2;
   in this sub-project it is `None`), candidate = `ALWAYS_ON | {m for m, v in
   license.modules.items() if v["expires_at"] >= today}`.
2. Otherwise (SaaS): candidate = `ALWAYS_ON | PLAN_MODULES[tenant.plan]`, then
   apply `tenant.module_overrides` (`{"network": true, "ai_cs": false}`) —
   `true` adds, `false` removes. Overrides never remove `ALWAYS_ON` keys.
3. Drop any module whose `REQUIRES` set is not fully in the candidate
   (iterate until stable).

Plan bundles live in `plans.py` as a `modules` key per plan:

- `free`: `whatsapp`, `network`, `upstream_sync`
- `pro`: all `PAID`

(These bundles reproduce today's access: free tenants currently reach
everything except WhatsApp API mode and Whish customer payments; `ai_cs` only
functions in API mode, so it is effectively Pro already.)

Unknown keys in overrides or licenses are ignored.

## Data model

- `Tenant.module_overrides` — JSON, nullable, default `NULL` (= no overrides).
  One Alembic migration. Per the schema-drift note, the migration must check
  `inspect(bind)` for the column before adding it.
- `Tenant.to_dict()` gains `modules: sorted(enabled_for(self))`.

## Backend enforcement

- `tenancy.require_module(key)` — decorator factory in the style of
  `network_view_required()` (app.py ~2114): `@wraps`, `verify_jwt_in_request()`,
  resolve `current_tenant()`, return
  `jsonify(msg="Module not enabled", module=key), 403` when off. Stack it
  under the existing role decorator on every JWT route in the module's group.
- Non-JWT routes call `modules.is_enabled(tenant, key)` inside the handler
  after resolving the tenant and return the same 403 body (public pages: 404
  instead, so a disabled tenant's pay link looks like any dead link):
  - `/api/cs-agent/*` (ElevenLabs token) → `ai_cs`
  - `/api/agent/jobs*` (`@agent_token_required`) → `network`
  - `/api/whatsapp/webhook` → `whatsapp` (still return 200 to Meta, just
    don't process; AI dispatch additionally checks `ai_cs`)
  - `/api/pay/*`, `/api/pay-attempt/*`, `/api/customer-whish/*` → `whish_payments`
- Cross-module tools: `/api/cs-agent/tools/network-diagnostic` also requires
  `network`; `/api/cs-agent/tools/send-payment-link` also requires
  `whish_payments`. When missing, the tool returns a polite "not available"
  result instead of a 403, so the AI agent can tell the customer.
- Route → module map (app.py line numbers as of main d7f1639, for orientation):
  - `whatsapp`: 6436-6491, 7080, 7110-7304, 8233, 8247, 8270, 8465,
    `whatsapp_inbox_routes.py`
  - `ai_cs`: 13513-13834
  - `network`: 10457-10633, 11247-11459, 11909-11956, 11972-12295,
    12468-12774, 12809-12907
  - `upstream_sync`: 10210-10334, 10380
  - `whish_payments`: 5136, 6534-6597, 6620-6995, 7007-7049
- Replace the three `plans.limits(...)["whish_customer_payments"]` call sites
  (6556, 6610, 7025) with the module check; remove the key from `PLANS`.
- Scheduled jobs: inside each `*_for_tenant` loop, skip tenants lacking the
  module: `send_daily_whatsapp_keepalive` → `whatsapp`,
  `auto_sync_upstream_status` → `upstream_sync`,
  `refresh_agent_mode_network_status` → `network`. Core/office jobs unchanged.
- Disabling a module never deletes or mutates data.

## Frontend

- `AppContext` loads `tenantMe()` once after login and exposes
  `modules` and `hasModule(key)`.
- `NAV_ITEMS` (App.js 70-103) entries gain an optional `module` key; the
  existing filter (195-198) additionally requires `hasModule(item.module)`:
  `network-devices`, `network-tree`, `network-map` → `network`;
  `upstream-providers` → `upstream_sync`; `messaging` → `whatsapp`;
  `cs-agent-voice` → `ai_cs`.
- Sub-tabs gated the same way: `SettingsView` WhatsApp Notifications and
  WhatsApp Templates → `whatsapp`, Whish Payments → `whish_payments`;
  `MessagingView` all tabs → `whatsapp`. Customer-row actions that call gated
  endpoints (network status chips, suspend/unsuspend, upstream sync, Whish
  link resend) are hidden when the module is off.
- The API client intercepts 403 responses carrying `module` and shows a
  snackbar: "This feature isn't included in your plan."
- `SettingsView`'s local `isPro` check for Whish is replaced by
  `hasModule('whish_payments')`.

## Super-admin

- `GET /api/admin/tenants` rows include `modules` and `module_overrides`.
- `POST /api/admin/tenants/<tid>/modules` body `{"overrides": {key: bool|null}}`
  (`null` clears an override). `@superadmin_required`. Rejects unknown keys and
  `ALWAYS_ON` keys with 400.
- `SuperAdminView` tenant row gets a **Modules** button → dialog with one
  toggle per paid module, each labelled "from plan" or "override", plus a
  "reset to plan" action.

## Testing

- `tests/test_modules.py`: bundles per plan; overrides add/remove; overrides
  can't remove `ALWAYS_ON`; `ai_cs` dropped without `whatsapp`; unknown keys
  ignored; license provider hook (stubbed) takes precedence and honours
  per-module expiry.
- One 403 test per paid module on a representative JWT route, plus the
  webhook (returns 200, does not process) and a public pay page (404).
- Scheduled-job skip tests for the three gated jobs.
- Super-admin endpoint validation tests.
- Frontend: nav items hidden when `hasModule` is false (Jest).

## Out of scope

License files, trial, read-only mode, deployment mode (sub-project 2);
installer (sub-project 3); à-la-carte self-serve module purchase on the Whish
billing page (possible later, on top of this).
