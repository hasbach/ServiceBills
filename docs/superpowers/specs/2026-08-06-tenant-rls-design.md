# Tenant-ID Row-Level Security — Design

## Problem

Every tenant-owned table has a `tenant_id` column, and cross-tenant isolation is enforced entirely at the application layer: every read goes through `tenant_query(Model)`, every insert through `new_for_tenant(Model, ...)` (`tenancy.py`), scoping by `current_tenant_id()` (read from the request's JWT). This works, but it's a single layer — a missed `tenant_query()` call anywhere in a ~6,000-line `app.py`, in a future endpoint, or in a raw SQL/script path, leaks another tenant's data with nothing at the database layer to stop it. Postgres Row-Level Security (RLS) can enforce the same boundary as a second, independent layer, so an application-layer bug alone isn't enough to leak data.

Confirmed on the live Supabase project: RLS is currently disabled (`rowsecurity = false`) on every table. The app's `DATABASE_URL` connects as the same role that owns the tables — Postgres exempts table owners from RLS by default regardless of policies, so enabling RLS alone would do nothing for this app's own traffic without also forcing it.

## Scope

All 26 `TENANT_OWNED_MODELS` tables (`app.py`):

```
reseller, reseller_payment, customer, subscription_plan, sector, supplier,
supplier_payment, expense_category, expense, payment, generated_receipt,
addon_purchase, business_settings, whats_app_settings, service_status,
support_ticket, ticket_log, push_subscription, service_outage,
customer_feedback, payment_reminder, upgrade_request, employee,
salary_charge, salary_payment, monthly_profit_estimate
```

**Explicitly excluded: `user`.** It has a `tenant_id` column but was deliberately left out of `TENANT_OWNED_MODELS` — `/api/login` and `/api/register` look up a `User` row *before* any JWT/tenant context exists. RLS on `user` would break login itself. Not touched by this change.

## Mechanism

Per table:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;  -- binds even for the owning role
CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.tenant_id', true)::integer)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::integer);
```

`current_setting('app.tenant_id', true)` reads a Postgres session variable (the `true` argument means "return NULL instead of erroring if unset"). `USING` gates SELECT/UPDATE/DELETE visibility; `WITH CHECK` additionally gates what INSERT/UPDATE is allowed to write, so a row can't be created or reassigned into a tenant that isn't current.

**Fails closed.** If the session variable is never set, `current_setting(...)` returns NULL, and `tenant_id = NULL` is never true in SQL — Postgres returns zero rows for reads and rejects writes, rather than exposing everything. A wiring bug shows up as a feature silently returning nothing, not as an error and not as a leak.

**Postgres-only.** This entire mechanism (session GUCs, `ENABLE`/`FORCE ROW LEVEL SECURITY`, `CREATE POLICY`) doesn't exist in SQLite. The migration and the app wiring below are both written to be no-ops on SQLite, so local dev and the existing test suite (100% SQLite) are unaffected and continue running exactly as before.

## App wiring

### Setting the session variable

`SET LOCAL app.tenant_id = '<id>'`, issued at the start of every Postgres transaction via a SQLAlchemy `"begin"` engine event (skipped entirely when `engine.dialect.name != 'postgresql'`). `SET LOCAL` (not plain `SET`) is required: it's scoped to the current transaction only and auto-clears on commit/rollback, which matters because `DATABASE_URL` is a pooled connection shared across requests — a plain `SET` would risk one request's tenant id leaking into the next request that reuses the same underlying connection.

The event handler needs to know "what tenant is this transaction for." Two sources, added to `tenancy.py`:

- **Request-scoped (the common case):** a `before_request` hook in `app.py` best-effort parses the JWT (`verify_jwt_in_request(optional=True)`, so it doesn't reject public routes) and stores `g.tenant_id` if present. The event handler reads `flask.g.tenant_id` when `has_request_context()`.
- **No-request-context (scheduler jobs):** a small context-manager helper, e.g. `tenancy.use_tenant(tenant_id)`, backed by a `contextvars.ContextVar`. The event handler falls back to this when there's no Flask request context.

If neither source has a value, the handler still issues `SET LOCAL app.tenant_id = ''` explicitly (rather than skipping the `SET` entirely) — an empty string fails `::integer` cast comparisons the same way NULL does, but this way a stale value from connection reuse can never survive into a transaction where nobody claimed a tenant.

### Call sites needing the explicit (non-JWT) form

- **The 3 daily scheduler jobs** (`generate_missing_payments_with_context`, `generate_missing_salary_charges_with_context`, `recalculate_all_estimated_profits_with_context`) already loop tenant-by-tenant (`for t in Tenant.query.filter_by(status="active").all(): generate_x(t.id)`). Each per-tenant call gets wrapped in `with tenancy.use_tenant(t.id):`.
- **`scripts/import_tenant_from_sqlite.py`** — imports one tenant's data into Postgres inside a single transaction. Add one `SET LOCAL app.tenant_id = '<new_tenant_id>'` (raw SQL via the same connection) right after the new tenant row is created and its id is known, before any domain-table inserts.
- **`scripts/migrate_sqlite_to_postgres.py`** is a *whole-database*, all-tenants-at-once lift-and-shift (it copies the `tenant` table itself, bulk, in one transaction) — there's no single tenant id to scope it to. This script is not made RLS-compatible; it's documented as a one-time bootstrap tool that must run **before** RLS is enabled (or with RLS temporarily disabled if it's ever needed again).
- **Audit needed, not yet done:** any webhook handler (WhatsApp, Stripe) or CLI command that touches a `TENANT_OWNED_MODELS` table outside a normal JWT-bearing request. This becomes an explicit task list in the implementation plan — every such call site found gets `tenancy.use_tenant(...)` wrapped around it before the migration is applied, not after.

## Migration

One Alembic migration, guarded to only run its RLS statements on Postgres (`if op.get_bind().dialect.name == 'postgresql':`), so `flask db upgrade` against the SQLite test/dev database is unaffected. `upgrade()` issues the three statements above for all 26 tables; `downgrade()` drops each policy and runs `ALTER TABLE ... DISABLE ROW LEVEL SECURITY` — a complete, tested-by-symmetry rollback path.

## Rollout

Because this fails closed, a wiring mistake breaks features rather than leaking data — but a full-app "everything returns empty" incident is still a bad time to discover a bug. There is no local Postgres available to rehearse this against (no Docker in this environment), so:

1. Ship the wiring (event hook, `use_tenant`, `before_request` change) in a deploy **without** the RLS migration — this is inert until some table actually has RLS+FORCE enabled, so it can be verified safe on its own first (app behaves identically).
2. Apply the migration during low-traffic hours, with the `downgrade()` path confirmed to work, ready as an immediate rollback if anything comes back empty that shouldn't.
3. After deploying, spot-check a handful of ordinary requests (login, view customers, view payments) across more than one tenant to confirm each still sees only its own data — this is also the manual confirmation that isolation is actually working, not just "not broken."

## Out of scope (explicitly deferred)

- The `user` table (breaks login if touched — see Scope).
- Making `scripts/migrate_sqlite_to_postgres.py` RLS-compatible (doesn't fit its all-tenants-at-once design; documented as pre-RLS-only instead).
- A dedicated non-owner Postgres role for the app (the stronger alternative considered and declined in favor of `FORCE ROW LEVEL SECURITY` on the existing role, to avoid provisioning a new role and rotating `DATABASE_URL`). Can be revisited later without redoing this work — the policies themselves don't change, only which role they bind to.
- Any webhook/CLI audit findings beyond what's listed above — to be enumerated as concrete tasks in the implementation plan, not guessed at here.

## Testing

RLS itself can't be exercised by the existing test suite (SQLite has no RLS, and there's no Postgres available in this environment to stand up an integration test against). What's testable and will be:

1. **The wiring is inert on SQLite** — the full existing test suite (91 tests) passes unchanged, proving the `"begin"` event hook and `use_tenant` context manager don't alter behavior when the dialect isn't Postgres.
2. **`use_tenant` context manager unit behavior** — sets and correctly restores/clears the context var across nested/sequential use (a plain Python test, no DB needed).
3. **Manual verification against the real Supabase project post-deploy** (not automated): the `pg_class.relrowsecurity`/`pg_policies` query from earlier confirms `ENABLE`+`FORCE`+policy landed on all 26 tables, plus the spot-check in Rollout step 3.
