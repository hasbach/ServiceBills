# servicesBills — Phase 2: Tenant Scoping of All Routes (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
>
> **Prerequisite:** Phase 1 complete (branch `phase-1-multitenancy`). Do Phase 2 on that same branch (or a `phase-2-scoping` branch off it). Base = `phase-1-multitenancy`, NOT `main` (main lacks the tenant schema).

**Goal:** Scope every data-touching route, helper, and background job in `app.py` to the authenticated tenant so no request can ever read or write another tenant's data — restoring write functionality that Phase 1 left broken.

**Architecture:** All reads go through `tenant_query(Model)`; all creates through `new_for_tenant(Model, ...)`; per-tenant singletons through a new `get_tenant_settings(Model)`. Routes keep their existing `@jwt_required()`; the scoping helpers read `tenant_id` from the JWT (Phase 1). Background jobs and the inbound WhatsApp webhook have no JWT, so they resolve the tenant explicitly and iterate/lookup.

**Tech Stack:** Flask · SQLAlchemy · Flask-JWT-Extended · pytest (in-memory SQLite via `tests/conftest.py`).

## Global Constraints

- **No cross-tenant access, ever.** A single unscoped `Model.query...` on a tenant-owned table is a P0 data-leak. The exit gate (Task 2.14) greps for unscoped queries and fails the phase if any remain outside the allowlist.
- **All file paths absolute** under `C:\Users\InfoCenter\source\repos\delta-net-saas\`. `delta-net` base folder is untouched.
- **Tests first.** Every route-group task adds an isolation test (tenant A cannot see/modify tenant B) that fails before scoping and passes after.
- **404 not 403 for cross-tenant ids.** Addressing another tenant's row id must return 404 (row "does not exist" for this tenant), never 403 or 200. Use `tenant_query(Model).filter_by(id=...).first_or_404()`.
- **Public/unscoped routes (allowlist — never tenant-scope these):** `/api/register`, `/api/login`, `/api/vapid-public-key`, `/api/whatsapp/webhook` (external; resolves tenant by `phone_number_id`), `/uploads/<filename>` (see Task 2.13 note), `/manifest.json`, `/<path:path>` (SPA static).
- **Migrations:** none in this phase — schema is unchanged. Only Python route logic changes.
- **Frequent commits:** one per task.

---

## The Scoping Playbook (apply uniformly — this is the DRY core)

Every task below is an application of these six rules. Read once, apply everywhere.

| # | Before | After |
|---|---|---|
| R1 | `X.query.all()` / `X.query.filter(...)` / `X.query.filter_by(...)` | `tenant_query(X).all()` / `tenant_query(X).filter(...)` / `tenant_query(X).filter_by(...)` |
| R2 | `X.query.get(id)` / `X.query.get_or_404(id)` | `tenant_query(X).filter_by(id=id).first_or_404()` |
| R3 | `X.query.get(id)` used then null-checked | `tenant_query(X).filter_by(id=id).first()` (keep existing null handling) |
| R4 | `x = X(field=..., ...)` (create) | `x = new_for_tenant(X, field=..., ...)` |
| R5 | `X.query.first()` (singleton settings) | `get_tenant_settings(X)` (Task 2.0) |
| R6 | Aggregates: `db.session.query(func.sum(...)).filter(...)` / `.join(...)` | add `.filter(X.tenant_id == current_tenant_id())` to the query |

**Imports:** add to the top of `app.py` (near other imports):
```python
from tenancy import current_tenant_id, tenant_query, new_for_tenant, get_tenant_settings, tenant_required
```

**Cross-model writes:** when a route creates a child row referencing a parent (e.g. a `Payment` for a `Customer`), first fetch the parent with `tenant_query(Customer).filter_by(id=...).first_or_404()` — this both authorizes the parent and gives you `tenant_id` implicitly via `new_for_tenant`.

**Relationship traversal is safe:** `customer.payments` is already tenant-correct once `customer` was fetched via `tenant_query`. Do not double-scope relationship collections.

---

## Task 2.0: Tenancy plumbing (settings helper, JSON 401, two-tenant test fixture)

**Files:**
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\tenancy.py`
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\app.py` (imports; JSON 401 handler)
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\conftest.py`

**Interfaces:**
- Produces `get_tenant_settings(model, **defaults)` — returns the current tenant's single settings row for `model`, creating it (with `defaults`) if missing. Used for `BusinessSettings`, `WhatsAppSettings`, `SystemUpdateSettings`.
- Produces test helper `make_tenant(client, business_name, username, password="pw") -> dict` (auth headers) in `conftest.py`.

- [ ] **Step 1: Add `get_tenant_settings` to `tenancy.py`:**
```python
def get_tenant_settings(model, **defaults):
    """Return the current tenant's singleton settings row for `model`, creating it if absent."""
    from app import db
    row = tenant_query(model).first()
    if row is None:
        row = new_for_tenant(model, **defaults)
        db.session.add(row)
        db.session.commit()
    return row
```

- [ ] **Step 2: Add a JSON 401 handler** so `current_tenant_id()`'s `abort(401)` returns JSON, not HTML. In `app.py` after `jwt = JWTManager(app)`:
```python
from werkzeug.exceptions import Unauthorized
@app.errorhandler(Unauthorized)
def _unauthorized(e):
    return jsonify(msg=getattr(e, "description", "Unauthorized")), 401
```

- [ ] **Step 3: Add the two-tenant fixture helper to `conftest.py`:**
```python
def make_tenant(client, business_name, username, password="pw"):
    client.post("/api/register", json={"username": username, "password": password,
                                       "business_name": business_name})
    r = client.post("/api/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}
```

- [ ] **Step 4: Add the top-level import** line from the Playbook to `app.py`.
- [ ] **Step 5: Run** `python -m pytest -q`. Expected: 9 pass (no behavior change yet).
- [ ] **Step 6: Commit.** `git commit -am "feat(tenancy): add get_tenant_settings, JSON 401 handler, two-tenant test fixture"`

---

## Route-group tasks

Each task: apply the Playbook to the listed routes, then add the isolation test. **Isolation test template** (adapt `PATH`, payload, and the create step per resource):

```python
def test_<resource>_isolation(client):
    from tests.conftest import make_tenant
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")
    # Tenant A creates a resource
    created = client.post("<COLLECTION_PATH>", headers=a, json=<VALID_PAYLOAD>)
    assert created.status_code in (200, 201)
    rid = created.get_json()["id"]
    # Tenant B cannot see it in the list
    listed_b = client.get("<COLLECTION_PATH>", headers=b).get_json()
    assert all(item["id"] != rid for item in (listed_b if isinstance(listed_b, list) else listed_b.get("items", [])))
    # Tenant B cannot fetch/modify/delete it by id -> 404
    assert client.get(f"<COLLECTION_PATH>/{rid}", headers=b).status_code == 404
```

### Task 2.1: Customers
**Routes:** `GET/POST /api/customers` (984, 1057); `PUT/DELETE /api/customers/<id>` (1180, 1328); `PUT /api/customers/<id>` activate (1975) & cancel (2057); `GET /api/customers/<id>` balance (2087) & unpaid_receipt (2152); `POST /api/customers/<id>` feedback/reminder/reset (3449, 3933).
**Gotchas:** `POST /api/customers` sets `subscription_plan_id` — fetch the plan via `tenant_query(SubscriptionPlan).filter_by(id=...).first_or_404()` so you can't attach another tenant's plan. Balance-reconciliation helper `apply_customer_balance_to_unpaid_payments(customer)` operates on an already-scoped `customer` — no change needed inside it.
- [ ] Apply R1–R4 to all listed routes. Create test `tests/test_iso_customers.py`. Commit.

### Task 2.2: Payments
**Routes:** `POST/GET /api/payments` (1610, 1679); `DELETE /api/payments/<id>` (1755); `PUT /api/payments/<id>/mark_paid` (1836); `POST /api/payments/generate_future` (1353).
**Gotchas:** `generate_future` and `mark_paid` mutate customer balance — fetch customer via `tenant_query`. Any `Payment.query` aggregate uses R6.
- [ ] Apply Playbook. Test `tests/test_iso_payments.py` (create customer+plan as A, create payment, B can't see/delete). Commit.

### Task 2.3: Subscription plans
**Routes:** `GET/POST /api/subscription_plans` (1524, 1530); `PUT/DELETE /api/subscription_plans/<id>` (1572, 1593).
- [ ] Apply R1–R4. Test `tests/test_iso_plans.py`. Commit.

### Task 2.4: Resellers & reseller payments
**Routes:** `GET /api/resellers/<id>` (4596); `GET/POST /api/resellers` (4607, 4618); `PUT /api/resellers/<id>` (4636); `POST /api/resellers/<id>/...` credit/payment/discount (4655, 4696, 4737).
- [ ] Apply Playbook (reseller payments created via `new_for_tenant(ResellerPayment, ...)` after fetching the reseller via `tenant_query`). Test `tests/test_iso_resellers.py`. Commit.

### Task 2.5: Suppliers & supplier payments
**Routes:** `GET/POST /api/suppliers` (4786, 4792); `PUT/DELETE /api/suppliers/<id>` (4810, 4828); `GET/POST /api/suppliers/<id>` details/payment (4850, 4856); `GET/PUT /api/suppliers/<id>/...` (4890, 4929).
- [ ] Apply Playbook. Test `tests/test_iso_suppliers.py`. Commit.

### Task 2.6: Expenses, categories, sectors
**Routes:** `GET/POST /api/sectors` (4037, 4043); `PUT/DELETE /api/sectors/<id>` (4059, 4079); `GET/POST /api/expense_categories` (4092, 4098); `PUT/DELETE /api/expense_categories/<id>` (4116, 4136); `GET/POST /api/expenses` (4155, 4172); `PUT/DELETE /api/expenses/<id>` (4204, 4246).
**Gotchas:** expenses may reference a `supplier_id` and `category` — fetch each via `tenant_query`.
- [ ] Apply Playbook. Test `tests/test_iso_expenses.py`. Commit.

### Task 2.7: Receipts
**Routes:** `GET /api/receipt/<id>` (2180); `GET /api/receipts/with-current-balance` (2248); `DELETE /api/receipts/<id>` (2285); `GET /api/receipts` (4265); `POST /api/receipts/generate` (4287); `POST /api/receipts/log_print` (4337).
**Gotchas:** receipt generation joins Customer/Payment — scope all. Business logo pulled from `get_tenant_settings(BusinessSettings)`.
- [ ] Apply Playbook. Test `tests/test_iso_receipts.py`. Commit.

### Task 2.8: Reports & dashboard (aggregates — R6 everywhere)
**Routes:** `/api/reports/total-sales` (1792), `unpaid-payments` (1808), `customer-numbers` (1823), `expenses-total` (2302), `monthly-revenue` (2325), `revenue` (3882), `overdue` (3912), `active-subscriptions-by-plan` (4355), `collector-progress` (4400), `financial` (4438); `GET /api/dashboard` (3084).
**Gotchas:** these are the highest-risk for leaks because they use `db.session.query(func...)` and joins. Every aggregate must filter by `tenant_id`. `financial` and `dashboard` touch many tables — scope each sub-query.
- [ ] Apply R6 to every query in each route. Test `tests/test_iso_reports.py`: seed A with revenue, B with none; assert B's totals are zero and A's exclude B. Commit.

### Task 2.9: Settings (per-tenant singletons)
**Routes:** `POST/GET /api/business-settings` (2367, 2413); `GET/POST /api/whatsapp-settings` (2432, 2461); `POST /api/whatsapp/subscribe-waba` (2497); `GET /api/whatsapp/templates` (2515).
**Gotchas:** replace all `BusinessSettings.query.first()` / `WhatsAppSettings.query.first()` with `get_tenant_settings(...)`. WhatsApp templates call Meta using the tenant's own credentials.
- [ ] Apply R5. Test `tests/test_iso_settings.py`: A sets business_name "AAA", B sets "BBB"; each GET returns only its own. Commit.

### Task 2.10: Support tickets, ticket logs, outages, service status, feedback, push
**Routes:** `POST/GET /api/support-tickets` (3202, 3243); `PUT/DELETE /api/support-tickets/<id>` (3319, 3369); `POST/GET /api/service-outages` (3286, 3301); `PUT /api/service-outages/<id>` (3379); `GET /api/service-statuses` (3116); `GET/POST /api/service-status/<id>` (3130, 3143); `PUT /api/service-statuses/<id>` (3403); `POST /api/customer-feedback` (3422); `POST /api/payment-reminders` (3435); `POST /api/push-subscribe` (3182).
**Gotchas:** `ServiceStatus.query.join(Customer)` (3119) needs `.filter(Customer.tenant_id == current_tenant_id())` (R6). Push subscriptions are per-user but must also carry tenant_id via `new_for_tenant`.
- [ ] Apply Playbook. Test `tests/test_iso_tickets.py`. Commit.

### Task 2.11: Users (tenant-scope the admin user management)
**Routes:** `GET/POST /api/users` (781, 795); `PUT/DELETE /api/users/<id>` (817, 832).
**Gotchas:** `User` is scoped by `tenant_id` too, BUT super-admins have `tenant_id IS NULL`. For tenant admins, `get_users` must list only same-tenant users: `User.query.filter_by(tenant_id=current_tenant_id())`. `update_user`/`delete_user` must 404 on users outside the caller's tenant. The "last admin" check counts admins *within the tenant*.
- [ ] Apply R1/R2 (scoped by tenant_id). Test `tests/test_iso_users.py`: A's admin cannot GET/PUT/DELETE B's user (404); A's user list excludes B's users. Commit.

### Task 2.12: WhatsApp send helpers (non-route, called by routes & jobs)
**Files:** `send_whatsapp_message(customer, event_type, context)` (~2947), `trigger_whatsapp_reminder(customer_id)` (3451), `bulk_send` (3472).
**Gotchas:** `send_whatsapp_message` reads `WhatsAppSettings.query.first()` — but it may be called from a request (use `get_tenant_settings`) OR from the scheduler (no request context). Refactor its signature to accept an explicit `settings` (or `tenant_id`) argument so both callers pass the right tenant. `trigger_whatsapp_reminder(customer_id)` must fetch the customer via `tenant_query`. `bulk_send` iterates customers — scope the customer query.
- [ ] Refactor `send_whatsapp_message` to take explicit tenant settings; update all callers (routes pass `get_tenant_settings`, job passes per-tenant). Test: unit-test that a request-context call uses the caller tenant's settings. Commit.

### Task 2.13: Inbound WhatsApp webhook (external — resolve tenant by phone_number_id)
**Routes:** `GET/POST /api/whatsapp/webhook` (3667). **Stays public (no JWT).**
**Gotchas:** Meta calls this with no JWT. The `GET` verification uses a `verify_token`; the `POST` payload contains the recipient business `phone_number_id`. Resolve the tenant by matching `WhatsAppSettings.phone_number_id` across ALL tenants: `WhatsAppSettings.query.filter_by(phone_number_id=<incoming>).first()` → gives `tenant_id`. All downstream customer lookups (forwarding, auto-reply) then filter by that resolved `tenant_id`. Verify-token check must match the resolved tenant's token. **This is the one place a cross-tenant `.query` is legitimate — document it inline and add to the exit-gate allowlist.**
- [ ] Implement tenant resolution by `phone_number_id`; scope all downstream lookups to the resolved tenant. Test `tests/test_iso_webhook.py`: two tenants with different `phone_number_id`; a webhook for A's number only touches A's data. Commit.

### Task 2.14: Scheduler jobs (tenant-iterating)
**Files:** `generate_missing_payments` (872) / `generate_missing_payments_with_context` (958); `scheduled_auto_update_check` (962); `create_payment_reminder` (3437) if scheduled.
**Gotchas:** these run outside any request → `current_tenant_id()` would abort. Rewrite to iterate active tenants and filter each query by that tenant explicitly:
```python
def generate_missing_payments_with_context():
    with app.app_context():
        for t in Tenant.query.filter_by(status="active").all():
            _generate_missing_payments_for_tenant(t.id)  # all queries filter tenant_id == t.id
```
Extract the per-tenant body into a helper that takes `tenant_id`. Any WhatsApp sends inside use that tenant's `get_tenant_settings`-equivalent (fetch `WhatsAppSettings` by `tenant_id`).
- [ ] Refactor both jobs to iterate tenants. Test: seed two tenants with overdue payments; run the job body for tenant A only; assert B unaffected. Commit.

---

## Task 2.15: Exit gate — sweep for unscoped queries + full isolation suite

**Files:** none (verification only), plus fixing anything the sweep finds.

- [ ] **Step 1: Grep for tenant-owned models still queried unscoped.** Run:
```bash
cd "C:\Users\InfoCenter\source\repos\delta-net-saas"
git grep -nE "(Customer|Payment|SubscriptionPlan|Reseller|ResellerPayment|Supplier|SupplierPayment|Expense|ExpenseCategory|Sector|GeneratedReceipt|AddonPurchase|BusinessSettings|WhatsAppSettings|SystemUpdateSettings|ServiceStatus|SupportTicket|TicketLog|PushSubscription|ServiceOutage|CustomerFeedback|PaymentReminder)\.query" -- app.py
```
Expected: the ONLY surviving hits are the documented webhook lookup (Task 2.13) and, if kept, super-admin platform routes. Every other hit is a bug — fix it.
- [ ] **Step 2:** Confirm `.query.first()` singleton count is 0 for settings models (all replaced by `get_tenant_settings`): `git grep -nE "(BusinessSettings|WhatsAppSettings|SystemUpdateSettings)\.query\.first" -- app.py` → empty.
- [ ] **Step 3: Run the full isolation suite.** `python -m pytest -q`. Expected: all tests pass, including every `test_iso_*.py`.
- [ ] **Step 4: Manual smoke** (writes work again): with `JWT_SECRET_KEY` set, register a tenant, create a plan + customer + payment via the API, confirm 201s. (Uses `verify` skill or `run` skill.)
- [ ] **Step 5: Commit** `git commit -am "test: tenant-isolation exit gate green; all routes scoped"`.

---

## Notes / deferred

- **`/uploads/<filename>`** is not tenant-scoped (any authed user could guess another tenant's filename). Real isolation comes with per-tenant object-storage prefixes in **Phase 3**. Flag, don't fix here.
- **`system-update/*` routes + `SystemUpdateSettings`** are a desktop-updater relic. Scoped per-tenant here for consistency, but should become platform-super-admin-only or be removed when Electron is retired in **Phase 3**.
- **FK constraints** on the 22 tenant_id columns remain deferred to Phase 3 (Postgres). Autogenerate will keep reporting them until then.

---

## Self-Review

**Coverage:** All 99 API routes are assigned to a task (2.1–2.11 domain/settings/users, 2.8 reports/dashboard, 2.10 support/status, 2.12–2.13 WhatsApp, 2.14 jobs). Public allowlist enumerated and excluded. The exit gate (2.15) mechanically proves no tenant-owned model is queried unscoped.

**Placeholder scan:** The scoping transformations are specified as exact rules (R1–R6) rather than per-line code because the change is uniform across ~99 routes; the concrete deliverables (isolation tests, the exit-gate greps, `get_tenant_settings`, JSON-401 handler, scheduler/webhook refactors) are given as real code. Route line numbers are current as of Phase 1 head `c297867b50b2`.

**Type consistency:** `tenant_query`, `new_for_tenant`, `current_tenant_id`, `tenant_required` match `tenancy.py` (Phase 1). New `get_tenant_settings(model, **defaults)` is defined once (Task 2.0) and referenced with that signature in Tasks 2.9/2.12/2.14.

## Execution Handoff

Execute in order 2.0 → 2.14, committing per task; 2.15 is the gate that closes the phase. Each route-group task is independently reviewable. When green, merge `phase-1-multitenancy` (now including Phase 2) to `main` — writes work and isolation is proven.
