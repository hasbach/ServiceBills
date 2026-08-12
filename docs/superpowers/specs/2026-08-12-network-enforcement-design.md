# Network Enforcement — Design

**Status (2026-08-12): models, migrations, CRUD endpoints, the `mikrotik.py` RouterOS adapter, the network-account link-conflict guard, and the renewal side effect are all implemented and tested.** Not yet built: any frontend UI. This spec exists to lock in the plan before writing code, matching the rigor used for [tenant-RLS](2026-08-06-tenant-rls-design.md) and [gratis-payment](2026-08-02-gratis-payment-design.md), because this touches both money and a real customer's live internet access.

## Problem

ServiceBills is used by ISP resellers, and "how does this reseller's network actually work" turns out to have three genuinely different answers depending on the tenant:

1. **No network integration at all.** The tenant tracks subscriptions and payments in ServiceBills and manages the network entirely outside it (or has no network dependency ServiceBills needs to know about). This already works today with zero changes — it's the default.
2. **Bridged reseller-of-a-reseller.** The tenant doesn't own the network edge — their Mikrotik/switches run in pure bridge mode, and the real subscriber session, IP allocation, and enforcement live on an **upstream company's RADIUS system** (a third-party subreseller web portal, no API). This is the case originally scoped in this spec.
3. **Self-hosted local network.** The tenant *does* own the edge — a Mikrotik running its own local PPPoE server, where the tenant's customers authenticate directly against hardware the tenant controls. Unlike case 2, MikroTik RouterOS exposes a real, documented, first-party API — this is the tenant's own hardware, not a third party's web portal, so there's no ToS ambiguity and no CAPTCHA/scraping involved.

ServiceBills needs to support all three without forcing any tenant into a shape that doesn't match their business. Cases 2 and 3 are modeled as two independent, parallel concepts — a tenant picks the one that matches how they actually run their network (or picks neither and stays in case 1).

### Terminology note: this is not the app's existing `Reseller`

ServiceBills already has a `Reseller`/`ResellerPayment` model, used today. Do not confuse it with anything in this spec:

- The existing `Reseller` is the **tenant's own downstream** — a smaller party the tenant sells service through or manages customers on behalf of. Money flows customer → tenant → that reseller's ledger; it is untouched by this spec.
- `UpstreamProvider` (Concept A, below) is a completely different, unrelated relationship: the tenant's **upstream** RADIUS operator. The tenant is only called "a reseller" from the *upstream's* point of view (a subreseller on the upstream's own portal) — that's a description of the business relationship, not a reference to the app's `Reseller` model. No code, table, or balance is shared between the two. `UpstreamProvider.balance` even moves in the opposite direction from `Reseller.balance` (see Concept A below).

## Tenant network mode

One new field, `BusinessSettings.network_mode`, String(20), default `'none'`:

- `'none'` — case 1. No upstream-provider or Mikrotik-server UI is shown anywhere. This is every existing tenant today, unchanged.
- `'upstream_bridge'` — case 2. The Upstream Providers section (below) is available; customers can be linked to an `UpstreamProvider`.
- `'local_mikrotik'` — case 3. The Mikrotik Servers section (below) is available; customers can be linked to a `MikrotikServer` + PPPoE username.

A single mode per tenant, chosen deliberately over letting a tenant run both at once: it keeps the settings UI and the customer-edit form simple (one optional network-link field, not two, and no risk of a customer accidentally getting linked to both). A tenant whose real network is genuinely mixed can still be served — pick whichever mode covers the majority of their customers, and the minority stay tracked with no network link at all (case 1 behavior for those specific customers), same as any tenant that ignores the feature. Nothing stops revisiting this later if a real mixed-mode need shows up; it isn't a one-way decision, just today's simpler default.

`BusinessSettings` is already a `TENANT_OWNED_MODELS` table with per-tenant settings (e.g. WhatsApp config) — `network_mode` is one more column on it, no new table needed for the mode itself.

## Concept A: Upstream Provider (mode: `upstream_bridge`)

*(Carried over from the original version of this spec — the design below is unchanged, just relocated under the three-mode framing.)*

### Confirmed constraints

- **No upstream offers an API.** All 8 upstreams checked so far (login page only, no credentials entered) are closed web portals. Any automation is browser automation (RPA) against their web UI, not a clean API call. The specific upstream URLs and which product each runs are tracked outside this repo, in the agent's session memory, not duplicated here.
- **Two shared white-label products cover all 8 upstreams checked:** **PROradius** (5 of 8, no CAPTCHA) and an unbranded **"radiusnew"** family (3 of 8, CAPTCHA on all three logins). One adapter per *product*, not per upstream company, is the right architecture.
- **Renew needs zero input on both products.** Clicking Renew/Refill shows a confirm dialog with username, plan, and a fixed price determined by whatever plan the customer is already assigned upstream (e.g. "Refill for 18 USD?") — no date picker, no duration, no manual amount. It deducts from the tenant's own prepaid balance with that upstream. Low-risk automation target: find row → click Renew → click confirm.
- **Both products expose the same actions:** Renew/Refill, Block/Unblock (In/active), Disconnect, Reset Mac, Expire/Delete.
- **Both products show the tenant's own prepaid balance with the upstream**, plus (PROradius) a commission figure, and nav sections for Resellers/Money/Invoices/Payments/Services/Switches/**Collectors** — structurally the same shape as this app's own `Reseller` ledger, one level further up the chain.
- **ToS risk not yet resolved.** Scripted automation against a third-party portal, even using the tenant's own legitimate credentials for actions they're entitled to do manually, may violate that platform's terms of service — separate from technical feasibility. Flagged, not answered. **Must be checked before building the PROradius adapter**, not assumed fine.

### Scope: data model + manual tracking only (no automation yet)

This is deliberately the zero-risk slice: it makes the upstream relationship visible and trackable in ServiceBills by hand, and lays schema groundwork so a later automation phase is additive. Automation against PROradius/radiusnew is **explicitly deferred** — see [Later phases](#later-phases-upstream-automation-not-designed-here).

### `UpstreamProvider`

Mirrors `Reseller`, but the money direction is flipped: the tenant owes *this* provider, not the other way around.

```python
class UpstreamProvider(db.Model):
    id
    tenant_id            # FK -> tenant.id, NOT NULL, indexed
    name                 # String(100), NOT NULL — e.g. "Terra", "IDM"
    product              # String(20), NOT NULL — 'proradius' | 'radiusnew' | 'manual'
    portal_url           # String(300), nullable — the login URL, for later automation phases
    portal_username      # String(100), nullable
    portal_password      # db.Column(EncryptedString, nullable=True) — same pattern as WhatsAppSettings.app_secret/access_token (crypto.py)
    balance              # Float, default 0.0 — STORED, mutated in place, same convention as Reseller.balance
    status               # String(20), default 'active'
    payments = relationship('UpstreamProviderPayment', backref='upstream_provider', cascade="all, delete-orphan")
```

`product = 'manual'` covers any upstream not yet classified into PROradius/radiusnew, or one the tenant tracks purely for bookkeeping with no intent to ever automate — the default for every provider created while no automation exists.

`portal_username`/`portal_password` are captured now (nullable, optional) so the tenant can record them per-provider immediately without a later schema change — but **nothing reads or uses them yet**. No login is attempted.

### `UpstreamProviderPayment`

Mirrors `ResellerPayment` — pure history/audit log, `UpstreamProvider.balance` remains the authoritative live number, both updated together in the same commit on every write (same discipline as the existing `Reseller`/`ResellerPayment` write paths, e.g. `app.py:5801-5810`).

```python
class UpstreamProviderPayment(db.Model):
    id
    tenant_id            # FK -> tenant.id, NOT NULL, indexed
    upstream_provider_id # FK -> upstream_provider.id, NOT NULL
    customer_id          # FK -> customer.id, nullable — set when the entry is for a specific customer's renewal; null for manual top-ups
    amount                # Float, NOT NULL
    type                  # String(50) -- 'balance_topup' | 'renewal_cost' | 'manual_adjustment'
    date                  # DateTime, default utcnow
    description           # String(200)
```

Sign convention (matches the *prepaid credit* framing the actual PROradius/radiusnew UIs use): `'balance_topup'` **decreases** `UpstreamProvider.balance` (tenant paid the provider, topping up prepaid credit). `'renewal_cost'` also **decreases** balance, the same way a renewal consumes prepaid credit upstream. `'manual_adjustment'` can move it either direction and requires a `description`.

### Link from `Customer`

```python
upstream_provider_id  # FK -> upstream_provider.id, nullable
upstream_username     # String(100), nullable — the customer's login/account name on that upstream's portal
```

Nullable because most existing customers won't have this set immediately after migration, and it's only ever populated when `network_mode == 'upstream_bridge'`.

### UI

- "Upstream Providers" section (shown only when `network_mode == 'upstream_bridge'`), listing providers with name, product, balance, status.
- Create/edit provider: name, product (dropdown: PROradius / radiusnew / Manual), portal URL/username/password (optional, masked password field), status.
- Manual balance top-up action (mirrors `add_reseller_credit`): amount + description → balance decreases, `UpstreamProviderPayment(type='balance_topup')` row created.
- Manual renewal-cost entry (closest analog is `collect_reseller_payment`): pick a customer linked to this provider, enter amount + description → balance decreases, `UpstreamProviderPayment(type='renewal_cost', customer_id=...)` row created. This is how the tenant records "I renewed this customer upstream and it cost me $X" until automation exists.
- On the Customer edit form: optional "Upstream Provider" + "Upstream Username" fields, same style as the existing `reseller_id` assignment field.
- No automation, no scraping, no login button anywhere yet — `portal_url`/`portal_username`/`portal_password` are stored but inert.

### Profit-estimation hook (deferred, noted for later)

The fixed price shown in the upstream's Renew dialog is literally the tenant's **cost** for that customer — the same quantity `SubscriptionPlan.cost` / `Customer.cost_override` already model (feeding `recalculate_estimated_profit`, `app.py:1470-1516`), currently populated by hand. A future phase could read `UpstreamProviderPayment` (`type='renewal_cost'`) history and use the actual paid amount instead of the hand-entered `cost_override`. Not built now — no automation means no real `renewal_cost` data flowing in yet except manual entries, and hooking the estimator to manual-only entries isn't worth the complexity yet.

### Later phases (upstream automation, not designed here)

Recorded as direction only, to be spec'd in full when picked up:

1. PROradius `renew` automation only (5 of 8 known upstreams, no CAPTCHA, confirmed 2-click flow) — needs the ToS question answered first, and an async-worker decision (see below) resolved before writing the adapter.
2. The CAPTCHA'd "radiusnew" family — likely semi-automated (human solves the CAPTCHA, script does the rest).
3. `suspend`/`unsuspend` automation, staff-confirmed action only, never silently triggered by a billing rule — cutting a real customer's internet has real consequences.

Anticipated adapter shape (not built now): one interface (`renew`, `suspend`/`unsuspend`, `read_status`), concrete implementations per *product* (PROradius, radiusnew), selected by `UpstreamProvider.product`.

**Async-worker gap, applies only to this upstream track:** the current APScheduler setup is a single in-process threadpool worker (`app.py:1520`) sized for cheap daily DB catch-up jobs, not long-lived browser sessions sharing a process with the web app. A long Playwright/Selenium scrape would block that worker slot. Needs a dedicated executor or a real out-of-process queue before any upstream browser automation ships. **This gap does not apply to Concept B below** — RouterOS API calls to a LAN-local (or VPN-reachable) router are simple, fast request/response calls, not long-lived browser sessions, so they run fine synchronously inside a normal request handler.

## Concept B: Local Mikrotik PPPoE (mode: `local_mikrotik`)

Unlike Concept A, this targets the tenant's **own hardware**. MikroTik RouterOS exposes a real, documented binary API (port 8728, or 8729 with TLS) that can list/add/enable/disable PPP secrets and read active PPPoE sessions — no ToS ambiguity, no CAPTCHA, no browser automation. That changes the risk profile enough that live automation is in scope now, not deferred to a later phase the way Concept A's automation is.

### `MikrotikServer`

```python
class MikrotikServer(db.Model):
    id
    tenant_id       # FK -> tenant.id, NOT NULL, indexed
    name            # String(100), NOT NULL — e.g. "Main Office Router"
    host            # String(255), NOT NULL — IP or hostname
    api_port        # Integer, NOT NULL, default 8728 (8729 when use_tls)
    use_tls         # Boolean, default False — selects RouterOS's TLS API variant
    username        # String(100), NOT NULL
    password        # db.Column(EncryptedString, nullable=False) — same pattern as WhatsAppSettings/UpstreamProvider
    service_name    # String(100), nullable — see "Username collisions" below
    status          # String(20), default 'active'
    last_checked_at # DateTime, nullable
    last_status     # String(20), nullable — 'online' | 'unreachable' | 'auth_failed', set by the connection test / any live call
```

`last_checked_at`/`last_status` are set opportunistically by whatever the most recent live call was (a manual "Test Connection" click, or an enable/disable action) — there is no dedicated background health-check job in this scope (see [Out of scope](#out-of-scope-explicitly-deferred)).

**Username collisions across ISPs sharing infrastructure.** RouterOS's `/ppp/secret` table allows duplicate `name` values as long as `service` differs — this happens for real: shared last-mile infrastructure can carry more than one ISP's PPPoE traffic, so an unrelated ISP can have their own subscriber also named e.g. `user1`, distinguished only by the PPPoE `service` name their server issues under. Looking up or modifying a secret by `name` alone on such a router is ambiguous, and in the worst case could suspend a *different ISP's* customer by accident — a serious correctness issue given this integration is live and touches real connectivity. `service_name` captures "the service name that's mine on this router" (nullable — blank means no such sharing, matching RouterOS's own `any` default). Every RouterOS call in [RouterOS API integration](#routeros-api-integration) below filters by `(name, service)` together whenever `service_name` is set, never by `name` alone. A tenant whose Mikrotik has no sharing concern can leave it blank and never think about this again.

### Link from `Customer`

```python
mikrotik_server_id  # FK -> mikrotik_server.id, nullable
pppoe_username       # String(100), nullable — the "name" field of the /ppp/secret on that router
```

Only ever populated when `network_mode == 'local_mikrotik'`. Not DB-constrained against also having `upstream_provider_id` set (both columns are just nullable), but the UI only ever exposes the one link field matching the tenant's current `network_mode`, so in practice a given tenant's customers only ever use one or the other.

**Network account reuse across customers.** A common real practice: a customer churns, and rather than provisioning a fresh account, staff hand their now-freed network username to a brand-new ServiceBills customer. This is expected and fine — but it means a `(mikrotik_server_id, pppoe_username)` pair (and equally `(upstream_provider_id, upstream_username)`) is not necessarily permanently owned by one `Customer` row forever. Two hazards follow, both handled:

- **Two customer rows silently sharing one live secret.** If the old customer's record isn't unlinked before the new one claims the same pair, any later action on either record (suspend, status check) can land on the other customer's actual connection without anyone noticing. `add_customer`/`update_customer` reject a save whose *effective* new state collides with a different customer's existing link, naming the conflicting customer in the error — staff must unlink the old record first, which is the correct sequencing anyway.
- **`subscription_expiry_date` mismatch with the real account's remaining validity.** Not a bug — deliberately never synced. ServiceBills' date is this customer's own billing cycle; whatever the account itself still has (e.g. leftover prepaid days on an upstream account) is a property of the physical account/slot, accumulated under a *previous* customer's history. Forcing these to match would be wrong, not a fix.

### RouterOS API integration

**Library:** [`librouteros`](https://librouteros.readthedocs.io/) — pure-Python, no external dependencies, actively maintained, packaged in Debian, implements RouterOS's native binary API (including the TLS variant), used directly against `/ppp/secret` and `/ppp/active`. Added to `requirements.txt`.

**Implemented** in [`mikrotik.py`](../../../mikrotik.py) — plain functions, not a class hierarchy, since there's exactly one implementation (RouterOS), unlike Concept A's two-product adapter interface. Every function returns `(ok: bool, value)` rather than raising:

- `test_connection(server) -> (ok, message: str)` — opens a connection, runs a trivial read (`/system/identity`), closes it. Side effect: sets `server.last_checked_at`/`server.last_status` (`'online'` / `'unreachable'` / `'auth_failed'` — the last two distinguished by whether the failure was socket-level (`OSError`, never reached the router) or protocol-level (`LibRouterosError`, reached it but it rejected us), not by string-matching RouterOS's error text). Wired to `POST /api/mikrotik-servers/<id>/test-connection`.
- `get_secret_status(server, pppoe_username) -> (ok, 'enabled'|'disabled'|'not_found')` — reads `/ppp/secret` filtered by `name=pppoe_username` **and, when `server.service_name` is set, `service=server.service_name`** — never by `name` alone once a service name is configured, so this can never resolve to a different ISP's identically-named subscriber (see "Username collisions" above; verified by `tests/test_mikrotik.py`, which asserts on the literal wire query words sent).
- `set_secret_enabled(server, pppoe_username, enabled) -> (ok, message: str)` — same `(name, service)` filtered lookup, sets `disabled` on the matching `/ppp/secret` entry. This is the live action behind the UI's Suspend/Unsuspend buttons. Never called automatically by this module — see "Suspend stays staff-confirmed" below.
- `get_active_session(server, pppoe_username) -> (ok, dict|None)` — `/ppp/active` filtered by `name` **only** (not `service` — RouterOS's active-session table doesn't carry the same per-secret service semantics, confirmed by web search 2026-08-12 to be genuinely unclear/undocumented rather than guessed). This is a read-only "currently connected" display, not a mutating action, so the blast radius of an ambiguous match here is a wrong tooltip, never a wrongly suspended connection — `get_secret_status`/`set_secret_enabled` remain the authoritative, correctly-scoped operations.

Wired into the app as `GET/POST /api/customers/<id>/mikrotik-status`, `POST /api/customers/<id>/mikrotik-suspend`, `POST /api/customers/<id>/mikrotik-unsuspend` — all guarded by `_customer_mikrotik_context()` (404/400 if the customer isn't actually linked to a Mikrotik server), and a failed live call returns HTTP 502 (distinguishable from a validation 400 or a real 200 success) without ever raising.

**Error handling — connectivity must never block billing.** A router can be offline (power outage at the tenant's site, network blip) independent of whether a payment is valid. Every function above catches connection/timeout/protocol errors and returns a failure tuple rather than raising (verified by `tests/test_mikrotik.py::test_unreachable_router_never_raises`); callers must proceed with any billing-side effect regardless of whether the live API call succeeded, and surface the API result to staff as a separate, clearly-labeled status. The billing transaction and the network side-effect must never share a single commit-or-fail boundary.

**Renewal side effect — implemented as `_maybe_restore_mikrotik_access(customer)`.** Guarded by "only act if `get_secret_status` currently returns `disabled`" — a no-op (safe, idempotent) for the common case of a customer who was never suspended. Runs strictly *after* the caller's own `db.session.commit()`, never inside the same transaction, and never raises.

The precise scoping matters and was the subject of a real near-miss: ServiceBills has two unrelated things both loosely describable as "renewing a subscription," and only one of them means money actually changed hands.

- `_renew_subscription_core` (`renew_subscription` / `bulk_renew_subscription`) mechanically advances `subscription_expiry_date` and generates the *next* pending `Payment(paid=False)` — unconditionally, even force-setting `is_subscription_active = True`, regardless of whether the customer has paid anything. This is what finance runs in a batch early in the billing cycle (e.g. day 1–10) to get ahead of collections for everyone at once. **`_maybe_restore_mikrotik_access` is never called from here.** Wiring it here would have meant the routine monthly batch job silently restoring internet access to every currently-suspended customer the moment their next cycle gets mechanically generated — before they've paid a cent of what they actually owe.
- `_mark_payment_fully_paid` (used by `mark_payment_as_paid`'s `'pay'` action and `bulk_mark_payments_paid`) and `mark_payment_gratis` (forgiving a charge — still a deliberate staff decision that settles the debt, treated the same) are the only places actual debt settlement happens. **`_maybe_restore_mikrotik_access` is called from all three, right after their commit.**

This scoping is what makes an early/late payment within a billing window behave correctly with zero extra date logic: a normal customer due on day 10 whose pending payment was bulk-generated on day 1 was never suspended in the first place (suspension is staff-confirmed only, never automatic — see below), so nothing on the network side is touched regardless of which day within the window they actually pay. Only a customer who *was* already suspended for a prior unpaid cycle gets re-enabled, and only the instant they actually pay — whatever day that is.

`bulk_mark_payments_paid` dedupes per customer, not per payment row, within one batch — a customer with several old unpaid rows settled together only gets one Mikrotik check (the first settlement already restores them if needed; re-checking per row would just be redundant round-trips to the same router).

**Deliberately not wired:** `apply_customer_balance_to_unpaid_payments` also settles debt (auto-applies a customer's existing credit balance to outstanding bills), but one of its call sites is inside `generate_missing_payments_with_context` — the unattended daily scheduler job. Wiring the side effect there would fire a live network action with no human in the loop, which is exactly the failure mode "staff-confirmed only" (below) exists to prevent. Left untouched on purpose.

**Suspend stays staff-confirmed, not automatic.** Even though RouterOS makes disabling a secret trivial and low-risk to call, nothing in this scope wires suspension to fire automatically off an overdue balance or a scheduled job — same principle already agreed for Concept A's `suspend`/`unsuspend`. Suspension only happens from an explicit "Suspend" click on the customer page. If a future need for scheduled auto-suspend on overdue accounts comes up, that's a deliberate, separately-discussed feature addition, not an implicit consequence of building this API integration.

### UI

- "Mikrotik Servers" section (shown only when `network_mode == 'local_mikrotik'`): list servers with name, host, status, last checked. Create/edit form: name, host, port, use_tls, username, password (masked), service name (optional, with inline help explaining it's only needed when this router's network is shared with another ISP), "Test Connection" button (calls `test_connection`, shows result inline, updates `last_status`).
- On the Customer edit form: optional "Mikrotik Server" + "PPPoE Username" fields, same style as the existing `reseller_id` field.
- On the Customer detail/view page (only when linked): current secret status (Enabled/Disabled, from `get_secret_status`), optional "currently connected" indicator (from `get_active_session`), and Suspend/Unsuspend buttons that call `set_secret_enabled` and show the live result.
- Business Settings: a `network_mode` selector (None / Upstream Bridge / Local Mikrotik), gating which of the two sections above appear.

## Migration

One Alembic migration (`flask db migrate`, following the existing hash+description naming convention in `migrations/versions/`):

- `business_settings.network_mode` column (default `'none'`).
- `upstream_provider` and `upstream_provider_payment` tables; `customer.upstream_provider_id` / `customer.upstream_username` columns.
- `mikrotik_server` table (including `service_name`); `customer.mikrotik_server_id` / `customer.pppoe_username` columns.

`render_as_batch=True` is already configured app-wide, so this is SQLite-safe by default — ordinary tables and columns, no dialect guard needed (unlike the tenant-RLS migration, which needed a Postgres-only guard for RLS-specific SQL that doesn't exist in SQLite).

Both `UpstreamProvider` and `MikrotikServer` get a plain `tenant_id` FK column (no base mixin exists in this codebase — see `tenancy.py`) and get added to the `TENANT_OWNED_MODELS` tuple in `app.py`. Read paths in request handlers use `tenant_query(...)`.

## Out of scope (explicitly deferred)

- Everything under [Later phases (upstream automation)](#later-phases-upstream-automation-not-designed-here) for Concept A.
- A dedicated scheduled health-check job for `MikrotikServer` reachability (e.g. a daily/hourly APScheduler job pinging every server and updating `last_status` proactively) — `last_status` in this scope only updates opportunistically from user-triggered actions. Worth adding later if "is my router even reachable" becomes a recurring support question, but not needed for the core Suspend/Unsuspend/Renew flows to work.
- Any scheduled/automatic suspension of overdue accounts (Mikrotik or upstream) — see "Suspend stays staff-confirmed" above.
- Wiring `UpstreamProviderPayment` into `recalculate_estimated_profit` (see [Profit-estimation hook](#profit-estimation-hook-deferred-noted-for-later)).
- Allowing a single tenant to run both `upstream_bridge` and `local_mikrotik` simultaneously — see [Tenant network mode](#tenant-network-mode) for the reasoning and how a genuinely mixed tenant is still served today.

## Testing

- Model-level + endpoint smoke coverage for `UpstreamProvider`/`MikrotikServer` CRUD, ledger actions, and customer links — run ad hoc during implementation (created, exercised, then deleted; not kept as permanent test files) against the existing 103-test suite to confirm no regressions.
- Tenant isolation: relies on the same `tenant_query(...)` pattern as every other tenant-owned model; not given a dedicated new test, consistent with how other models in this codebase are covered.
- `mikrotik.py` adapter: `tests/test_mikrotik.py` (15 tests, permanent) — `_connect` monkeypatched to a small fake `Api` double, no real router involved. Covers: the collision-safety property directly, by asserting on the *literal RouterOS wire query words* sent, with and without `service_name` set; found/not-found/enabled/disabled outcomes for `get_secret_status`/`set_secret_enabled`/`get_active_session`; and that every public function returns `(False, message)` rather than raising when the router is unreachable (`OSError`) or rejects the connection (`TrapError`), including the `'unreachable'` vs `'auth_failed'` classification on `test_connection`.
- Endpoint wiring (`test-connection`, `mikrotik-status`, `mikrotik-suspend`, `mikrotik-unsuspend`) verified with an ad hoc smoke test (mocking the `mikrotik` module functions, not `librouteros` itself) confirming: happy path, the 400 guard for an unlinked customer, and that a failed live call surfaces as HTTP 502, not a silent 200 or an unhandled 500.
- Renewal-side-effect scoping verified with an ad hoc smoke test (mocking `mikrotik.get_secret_status`/`set_secret_enabled`, run then deleted): confirmed `renew_subscription` never calls into `mikrotik.py` even when the secret is disabled, and `mark_paid` on the resulting pending payment does — i.e. the exact "bulk-generate early, pay late" scenario behaves correctly with no date-based logic needed.
- Link-conflict guard verified with the same ad hoc smoke test: a second customer claiming an already-linked `(mikrotik_server_id, pppoe_username)` pair is rejected naming the existing holder; after unlinking the original customer, the same claim succeeds.
- No automation to test for Concept A in this scope — nothing to mock, no browser driver in CI.

Sources:
- [Librouteros documentation](https://librouteros.readthedocs.io/)
- [RouterOS-api on PyPI](https://pypi.org/project/RouterOS-api/)
- [Python3 Example — MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/47579209/Python3+Example)
