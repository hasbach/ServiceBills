# servicesBills — Phase 4: Billing, Signup & Platform Admin (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **Prerequisite:** Phases 1–3 merged to `main` (multi-tenant, isolated, Postgres-ready, 25 tests green). Do Phase 4 on a `phase-4-billing` branch off `main`.

**Goal:** Turn the multi-tenant app into a sellable product: self-serve signup with email verification + password reset, Stripe subscription billing driving each tenant's plan/status, plan-gating with limits, a platform super-admin console, and tenant lifecycle (suspend / export / delete).

**Architecture:** Billing is backend/API-first here; the customer-facing billing/signup **UI is Phase 5**. Stripe is the source of truth for subscription state, synced to `Tenant.plan`/`Tenant.status` via a signed webhook. Auth stays JWT + username; users gain an `email` (verification + reset). Platform operators are users with `tenant_id IS NULL` and role `superadmin`; their routes never use `tenant_query` and are guarded by `@superadmin_required`. A suspended tenant is blocked at login and in `tenant_required`.

**Tech Stack:** Flask · SQLAlchemy · Flask-JWT-Extended · Stripe (Python SDK) · itsdangerous (signed tokens) · SMTP/provider email · pytest.

## Global Constraints

- **All paths absolute** under `C:\Users\InfoCenter\source\repos\delta-net-saas\`; `delta-net` base folder untouched.
- **Tenant isolation from Phases 1–2 must not regress** — the existing isolation suite stays green. New tenant-owned data continues through `tenant_query`/auto-stamp.
- **Secrets from env only:** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_*`, email creds, `APP_BASE_URL` (for links in emails), plus the Phase 0–3 secrets.
- **Stripe is source of truth** for subscription state; never set `Tenant.plan`/`status` from the client — only from verified webhook events (or super-admin action).
- **Webhook endpoint is public** (Stripe calls it) and verified by signature — the second legitimate public write path after the WhatsApp webhook; add it to the exit-gate allowlist.
- **Schema changes via Alembic**; migrations run on SQLite (tests) and Postgres (prod).
- **Tests:** Stripe calls are stubbed/mocked (no live API in CI); email uses a capture backend; test the webhook with a synthetic event object (bypass signature in tests via a seam).
- **Frequent commits**, one per task.

---

## Task 4.0: Dependencies, config, and the plan catalog

**Files:** `requirements.txt`, `config.py`, new `plans.py`.

- [ ] **Step 1:** Add to `requirements.txt`: `stripe`, `itsdangerous` (bundled with Flask, but pin it), and an email lib if using a provider (SMTP needs none).
- [ ] **Step 2:** `config.py` gains: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `APP_BASE_URL` (e.g. `https://app.servicesbills.net`), and email settings (`MAIL_BACKEND` = `console`|`smtp`, `SMTP_HOST/PORT/USER/PASSWORD/FROM`).
- [ ] **Step 3:** Create `plans.py` — the single source of plan definitions:
```python
# Maps servicesBills plans to Stripe Price IDs and enforced limits.
import os
PLANS = {
    "free": {"stripe_price": None,                       "max_customers": 50,  "whatsapp_api": False},
    "pro":  {"stripe_price": os.environ.get("STRIPE_PRICE_PRO"),  "max_customers": None, "whatsapp_api": True},
}
def plan_for_price(price_id):
    return next((name for name, p in PLANS.items() if p["stripe_price"] == price_id), "free")
```
- [ ] **Step 4:** `pip install -r requirements.txt`; `python -m pytest -q` (19→25 still green). Commit.

## Task 4.3: Email abstraction (do before verification/reset)

**Files:** new `email_util.py`, `tests/test_email.py`.

**Interfaces:** `email_util.send(to, subject, body)`; a `console` backend (dev/CI, records to a module list) and an `smtp` backend.

- [ ] **Step 1: Failing test:** the console backend records a sent message; `send` routes by `MAIL_BACKEND`.
- [ ] **Step 2:** Implement `ConsoleBackend` (append to `SENT` list + log) and `SmtpBackend` (`smtplib`), selected by `Config.MAIL_BACKEND`. In tests, `MAIL_BACKEND=console` and assert on `email_util.SENT`.
- [ ] **Step 3:** Run test; commit.

## Task 4.1: User email + email verification

**Files:** `app.py` (`User` model, `register`, new verify routes), new migration.

**Interfaces:** `User` gains `email` (unique, nullable for legacy), `email_verified` (bool, default False). Signed tokens via `itsdangerous.URLSafeTimedSerializer(JWT_SECRET_KEY, salt="email-verify")`.

- [ ] **Step 1: Failing test:** register with an email creates an unverified user + sends a verification email (captured); hitting `/api/verify-email?token=…` flips `email_verified` True; a bad/expired token 400s.
- [ ] **Step 2:** Add `email = db.Column(db.String(200), unique=True)`, `email_verified = db.Column(db.Boolean, default=False)` to `User`; autogenerate + review migration (nullable email so existing users are unaffected; backfill `email_verified=True` for pre-existing users so they aren't locked out).
- [ ] **Step 3:** Update `register` (`app.py:683`) to require+store `email`, send a verification link (`{APP_BASE_URL}/verify?token=…`). Add `POST /api/verify-email` (public) that validates the token and sets `email_verified`.
- [ ] **Step 4:** Decide enforcement: unverified users may log in but see a "verify your email" state (don't hard-block, to avoid lockout); or block — pick and document. Run tests; commit.

## Task 4.2: Password reset

**Files:** `app.py` (two public routes), tests.

- [ ] **Step 1: Failing test:** `POST /api/forgot-password {email}` always 200 (no user enumeration) and, if the email exists, sends a reset link (captured); `POST /api/reset-password {token,new_password}` sets the new password; bad/expired token 400.
- [ ] **Step 2:** Implement both routes using a signed timed token (salt `"password-reset"`, ~1h expiry). Never reveal whether the email existed. Run tests; commit.

## Task 4.4: Stripe billing (billing.py + webhook)

**Files:** new `billing.py`, `app.py` (checkout/portal/webhook routes), tests.

**Interfaces:** `billing.create_checkout_session(tenant, price_id)`, `billing.create_portal_session(tenant)`, `billing.handle_event(event)` (updates `Tenant.plan`/`status`).

- [ ] **Step 1: Failing test (webhook sync):** feed a synthetic `checkout.session.completed` / `customer.subscription.updated` / `customer.subscription.deleted` event into `billing.handle_event` and assert `Tenant.plan`/`status`/`stripe_*` update correctly (map price→plan via `plans.plan_for_price`; `deleted`→`plan="free"`, on past_due/unpaid→`status="suspended"`).
- [ ] **Step 2:** `billing.py`: set `stripe.api_key`; `create_checkout_session` (mode=subscription, `client_reference_id=tenant.id`, stores `stripe_customer_id`), `create_portal_session`, and `handle_event` (the pure state-mapping function, unit-tested without network).
- [ ] **Step 3:** Routes: `POST /api/billing/checkout` (`@jwt_required`, tenant admin → returns Stripe checkout URL), `POST /api/billing/portal` (→ portal URL), `POST /api/stripe/webhook` (**public**; verify signature with `STRIPE_WEBHOOK_SECRET` via `stripe.Webhook.construct_event`, then `billing.handle_event`). In tests, inject the event object directly (seam around signature verification).
- [ ] **Step 4:** Add `/api/stripe/webhook` to the exit-gate public allowlist. Run tests; commit.

## Task 4.5: Plan-gating and limit enforcement

**Files:** `app.py` (`add_customer` and any gated route), `tenancy.py` or new `gating.py`.

**Interfaces:** `requires_plan("pro")` decorator; `enforce_limit(model, key)` helper reading `plans.PLANS[tenant.plan]`.

- [ ] **Step 1: Failing test:** a `free` tenant at `max_customers` gets 402/403 on `POST /api/customers`; a `pro` tenant is unlimited; WhatsApp API mode is refused on `free`.
- [ ] **Step 2:** Implement the gate: in `add_customer`, before creating, check `tenant_query(Customer).count()` against `PLANS[current_tenant().plan]["max_customers"]` (None = unlimited) → 402 with an upgrade message. Add `requires_plan` for WhatsApp-API settings. Fetch the current `Tenant` via a small `current_tenant()` helper in `tenancy.py`.
- [ ] **Step 3:** Run tests; commit.

## Task 4.6: Platform super-admin console

**Files:** `app.py` (super-admin routes), `tenancy.py` (`superadmin_required`), migration if seeding.

**Interfaces:** `superadmin_required` decorator: `verify_jwt_in_request()` + claim `role == "superadmin"` + `tenant_id is None`. Super-admin routes use explicit cross-tenant queries (never `tenant_query`).

- [ ] **Step 1: Failing test:** a normal tenant admin gets 403 on `/api/admin/tenants`; a super-admin (seeded user with `tenant_id=None`, role `superadmin`) gets the full tenant list; `login` embeds `tenant_id=None` for them.
- [ ] **Step 2:** Add `superadmin_required`; routes: `GET /api/admin/tenants` (list all: id, name, plan, status, customer counts, MRR proxy), `POST /api/admin/tenants/<id>/suspend` and `/reactivate` (set `Tenant.status`). Add a CLI/seed path to create the first super-admin (a `flask` command or a guarded one-off script — never a public route).
- [ ] **Step 3:** Run tests; commit.

## Task 4.7: Tenant lifecycle — suspend enforcement, export, delete

**Files:** `app.py` (login + `tenant_required`), `tenancy.py`, super-admin routes.

- [ ] **Step 1: Failing test:** a suspended tenant's user is blocked at `login` (402/403 with a billing message) and `tenant_required` rejects an existing token; export returns the tenant's data; delete removes all of the tenant's rows across the 22 tables and the tenant itself, leaving other tenants intact.
- [ ] **Step 2:** Enforce suspension: in `login`, if the user's tenant `status != "active"`, return 402 with an upgrade/billing message (super-admins exempt); add the same guard in `tenant_required`. 
- [ ] **Step 3:** `GET /api/tenant/export` (tenant admin) — dump the tenant's rows (JSON) across all owned tables via `tenant_query`. `DELETE /api/admin/tenants/<id>` (super-admin) — hard-delete: for each tenant-owned model `Model.query.filter_by(tenant_id=id).delete()` in FK-safe order, then the users and the tenant; wrap in one transaction. 
- [ ] **Step 4:** Run tests; commit.

---

## Notes / deferred to Phase 5 (frontend)

- Customer-facing signup form, email-verify landing, reset-password screen, plan-selection + Stripe checkout redirect, "manage subscription" (portal) button, upgrade prompts on limit-hit, and the super-admin dashboard UI are all **Phase 5**. Phase 4 delivers the APIs they call.
- Dunning emails / trial periods / proration are Stripe-config concerns to tune post-launch.

## Self-Review

**Coverage:** Maps every "SaaS business layer" item from the master plan — signup + email verification (4.1) and password reset (4.2) on an email abstraction (4.3); Stripe subscriptions + webhook sync (4.4); plan-gating/limits (4.5); platform super-admin (4.6); tenant suspend/export/delete lifecycle (4.7). Stripe fields already exist on `Tenant` (Phase 1).

**Placeholder scan:** Stripe/email are integration-heavy; their network calls are explicitly stubbed in tests and driven by env config, not hardcoded. `plans.py`, the email backend, `handle_event` mapping, the gate check, and `superadmin_required` carry concrete code. Route line refs are current as of `main` after Phase 3.

**Type consistency:** `plans.PLANS`/`plan_for_price`, `email_util.send`/`SENT`, `billing.create_checkout_session`/`create_portal_session`/`handle_event`, `superadmin_required`, and `current_tenant()` are each defined once and referenced consistently. New migration chains from the Phase-3 head (`56a5ae0f8bb0`).

## Execution Handoff

Order: 4.0 → 4.3 → 4.1 → 4.2 → 4.4 → 4.5 → 4.6 → 4.7. Each task commits independently; the isolation suite stays green throughout. When done, merge `phase-4-billing` → `main`, then Phase 5 builds the UI on these APIs.
