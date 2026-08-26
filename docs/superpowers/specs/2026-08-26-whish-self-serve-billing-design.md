# Self-Serve Pro Plan via Whish Payment Gateway — Design

**Status (2026-08-26): design only, nothing built yet.** Phase 4 of the post-audit roadmap (see the `project-security-hotfix-roadmap` memory) originally scoped "self-serve Pro plan with a visible price — Stripe checkout already built, just not surfaced." Mid-design, the actual requirement changed: **Stripe will never be used for this market.** ServiceBills targets Lebanese ISP/reseller tenants, and Stripe isn't a viable local payment path — [Whish](https://whish.money) is the working gateway for Lebanese businesses. This spec covers building a new, independent Whish integration; the existing Stripe integration (`billing.py`, `/api/stripe/webhook`, `/api/billing/checkout`, `/api/billing/portal`) is left fully in place but dormant, per an explicit decision to keep it in case of future non-Lebanon expansion rather than delete it.

## Background: the Whish API, reverse-engineered from their own WooCommerce plugin

No official Whish API documentation was available. What we have instead is the actual PHP source of Whish's own official WooCommerce gateway plugin (`woocommerce-whish-balance-gateway`, provided directly by the Whish team for a different business's WooCommerce site) — this is a more reliable source than most vendor docs, since it's the real integration code Whish themselves ship and support.

Confirmed from `wc-whish.php`:

- **Base URL / endpoints**: `https://whish.money/itel-service/api/payment/account/balance` (verify credentials + get balance), `https://whish.money/itel-service/api/payment/whish` (create a payment), `https://whish.money/itel-service/api/payment/collect/rate` (referenced but unused by the plugin's actual flow).
- **Auth**: static HTTP headers on every request — `channel`, `secret`, `pluginversion`, `websiteurl` — not a signed request, not OAuth. One `channel`/`secret` pair per merchant account (i.e. one pair for ServiceBills/Salloum Services as a whole, not per-tenant — tenants are our customers, not Whish's).
- **Create-payment request** (`POST /payment/whish`, `Content-Type: application/json`):
  ```json
  {
    "externalId": "<our own unique id for this payment attempt>",
    "successCallbackUrl": "<our domain>/api/billing/whish/success?order=<externalId>&token=<uuid>",
    "failureCallbackUrl": "<our domain>/api/billing/whish/failure?order=<externalId>&token=<uuid>",
    "successRedirectUrl": "<our domain>/billing?token=<a second uuid>",
    "failureRedirectUrl": "<our domain>/billing?token=<a second uuid>",
    "amount": 120,
    "invoice": "ServiceBills Pro subscription",
    "currency": "USD",
    "requestee": "<BusinessSettings.business_name>",
    "target": "<BusinessSettings.mobile>",
    "email": "<BusinessSettings.email>"
  }
  ```
  Response: `{"status": true, "data": {"collectUrl": "https://www.whish.money/invoice/pay/..."}}` on success, or `{"status": false, "code": "currency.not_supported", ...}` / other failure codes.
- **Payment completion**: the customer is redirected to `collectUrl` (Whish's own hosted payment page), pays, and Whish redirects the browser back to whichever of `successCallbackUrl`/`failureCallbackUrl` applies — **this is a browser redirect with query-string params, not a server-to-server signed webhook.** The `token` in the URL is a UUID *we* generate and hand to Whish at creation time; Whish's only "proof" of authenticity is echoing it back unchanged. There is no HMAC signature, no shared-secret request signing, unlike Stripe's webhook model.
- **Supported currencies**: `LBP` and `USD` only.
- **No subscription/recurring-billing concept anywhere in this API.** Every endpoint is for a single one-time payment collection. This is the single biggest architectural difference from the existing Stripe integration.

## Explicit non-goals

- **No recurring/auto-charge billing.** Whish cannot auto-renew a subscription the way Stripe Checkout's subscription mode does. Renewal is always a fresh manual payment, initiated by the tenant (see Renewal & Expiry below). Building any kind of "store a card/token and auto-charge later" mechanism is out of scope — Whish's API doesn't support it, and there's no reason to build a parallel mechanism ourselves.
- **No attempt to strengthen Whish's callback security beyond what their own official plugin does.** The token-match-on-redirect model is Whish's blessed integration pattern; this spec adds only single-use/expiry protections on our side (see Security), not a fundamentally different verification scheme they don't support.
- **No removal of Stripe code.** `billing.py` and its routes stay as-is, unused. Not in scope: any cleanup, deprecation warning, or feature-flagging of the dormant Stripe path.
- **No multi-currency accounting integration.** This is USD-only pricing for the SaaS subscription itself. The separately-scoped multi-currency *accounting* work (currency field + FX-rate table for tenants' own customer-facing billing) is a distinct, later piece of Phase 4 — not touched here.
- **No live end-to-end test against the real Whish API.** `WHISH_CHANNEL`/`WHISH_SECRET` credentials have not been issued yet (per the account this plugin was originally configured for — ServiceBills' own account is a separate, not-yet-requested merchant account). Everything here is built and unit-tested against the documented request/response shapes; the first real payment is a supervised manual smoke test once credentials exist.

## Architecture

### New module: `whish_billing.py`

A sibling to `billing.py`, not a modification of it — mirrors this codebase's existing pattern of parallel provider-specific modules (`upstream_portal.py` / `upstream_portal_krypton.py`). Contains: the Whish HTTP client (create-payment call, header construction), payload building, and the callback-processing logic. `app.py` gets new routes that call into this module, matching the existing `billing.py` route-in-app.py-body-in-module split.

### Routes (all new, none touch existing Stripe routes)

- `POST /api/billing/whish/checkout` — JWT-required, tenant-scoped. Body: `{"cycle": "monthly" | "yearly"}`. Creates a `BillingPaymentAttempt` row, calls Whish's create-payment endpoint, returns `{"redirect": "<collectUrl>"}`.
- `GET /api/billing/whish/success` — public (no auth; Whish's redirect target). Query params `order`, `token`. Looks up the pending attempt, validates the token, applies the plan change, redirects browser to `/billing?status=success`.
- `GET /api/billing/whish/failure` — public, same shape, marks the attempt `failed`, redirects to `/billing?status=failed`.
- `GET /api/billing/config` (existing route, extended) — adds `"whish_enabled": bool` (true once `WHISH_CHANNEL`/`WHISH_SECRET` env vars are both set), alongside the existing `stripe_enabled`.
- `GET /api/billing/status` (new, small) — JWT-required. Returns `{"plan": ..., "plan_expires_at": ...}` for the frontend to compute the in-app expiry banner client-side.

## Data model

**`Tenant`** gains two nullable columns:
- `plan_expires_at` (`DateTime`, nullable) — null for Free; the paid-through timestamp for Pro.
- `plan_expiry_reminder_sent_at` (`DateTime`, nullable) — set once the pre-expiry reminder email has gone out, so it fires once per expiry cycle, not once per scheduler tick.

**New table `BillingPaymentAttempt`**:
| column | type | notes |
|---|---|---|
| `id` | PK | |
| `tenant_id` | FK → tenant | |
| `billing_cycle` | String | `'monthly'` or `'yearly'` |
| `amount` | Float | `120.0` or `1000.0` — matches this codebase's existing money-column convention (Float), not pre-empting the separate Float→Numeric migration scoped elsewhere |
| `currency` | String | `'USD'` |
| `whish_external_id` | String, unique | what we send Whish as `externalId`; also our own lookup key on callback |
| `callback_token` | String (UUID) | generated at creation, echoed back by Whish, single-use |
| `status` | String | `'pending'` \| `'succeeded'` \| `'failed'` \| `'expired'` |
| `created_at` | DateTime | |
| `completed_at` | DateTime, nullable | |

This table exists because Whish has no subscription object to query later — the callback only gives us `order` (our `externalId`) and `token`; everything else about "what was this payment for" has to already be on our side, looked up by that id.

One Alembic migration for both, following this repo's established defensive existence-check pattern (see `aa91943943d4`'s docstring) given the documented production-schema-drift history.

## Payment flow

1. Tenant clicks "Upgrade to Pro" (monthly or yearly) on `BillingView.js`.
2. `POST /api/billing/whish/checkout` creates a `pending` `BillingPaymentAttempt`, calls Whish, returns the `collectUrl`.
3. Frontend does a full-page redirect to `collectUrl` (Whish's hosted payment page — same UX pattern as the existing Stripe Checkout redirect).
4. Customer pays on Whish's page. Whish redirects the browser back to `successCallbackUrl` or `failureCallbackUrl`.
5. **Success**: look up the attempt by `whish_external_id` (the `order` query param). Reject (log + redirect to a generic error) if: no matching attempt, attempt not `pending`, or `token` doesn't match. Otherwise: mark `succeeded`, set `completed_at`, set `Tenant.plan = 'pro'`, and set `Tenant.plan_expires_at` — extending from the *current* `plan_expires_at` if it's still in the future (an early renewal), otherwise from `now()` — by 1 month or 1 year per `billing_cycle`. Clear `plan_expiry_reminder_sent_at`. Redirect to `/billing?status=success`.
6. **Failure**: mark `failed`, redirect to `/billing?status=failed`.

## Renewal & expiry automation

A new daily scheduled job, registered in `app.py` alongside the other `_with_context` jobs (in the same pre-`scheduler.add_job()` location the other automation functions live in, per the lesson from the 2026-08-26 crash-loop incident — see the roadmap memory's "Post-Phase-3 incident" note. **This is a hard requirement, not a style preference**: any new scheduler-referenced function must be defined before the `scheduler.add_job()` block, and verified with an explicit `RUN_SCHEDULER=1` import smoke-test before shipping, exactly because normal test runs never exercise that code path).

For each tenant with `plan == 'pro'`:
- **Reminder**: if `now()` is within 5 days of `plan_expires_at` and `plan_expiry_reminder_sent_at` is unset, send a renewal-reminder email via the existing `email_util.send()` (to the tenant's Business Settings email — the same address the manual upgrade-request flow already uses) and set `plan_expiry_reminder_sent_at`.
- **Grace + revert**: if `now() > plan_expires_at + 3 days`, revert `plan = 'free'`, clear `plan_expires_at` and `plan_expiry_reminder_sent_at`. Between the expiry date and the 3-day mark, the tenant keeps full Pro access unconditionally — no separate "grace" flag, the revert simply doesn't fire yet.

The in-app banner needs no scheduler involvement — `BillingView.js` (and optionally a global banner) computes it client-side from `GET /api/billing/status`'s `plan_expires_at`: within-5-days shows a renew reminder, past-expiry-within-grace shows a stronger warning, both with a direct link to the checkout flow.

## UI changes

- **`LandingView.js`**: Pro pricing card shows real numbers — "$120/mo" or "$1000/yr" (with the yearly card calling out its ~30% saving over paying monthly) — with a "Get Started" call-to-action replacing the current "Contact" text. Routes to signup, then the Billing page to actually pay. The existing "Contact us to upgrade" path stays available on the Billing page itself for anyone who'd rather not self-serve.
- **`BillingView.js`**: replace the Stripe-specific "Upgrade to Pro"/"Manage subscription" block with a Whish-driven one: current plan, expiry countdown (once Pro), a monthly/yearly toggle, and an "Upgrade"/"Renew" button that calls the checkout endpoint and redirects. Gated on the new `whish_enabled` flag from `/api/billing/config` — until real credentials are configured, this button simply doesn't render, same pattern as the existing `stripe_enabled` gate.

## Security

- Whish's redirect-plus-shared-token model is materially weaker than a signed webhook, but it's Whish's own official integration pattern (this is literally what their WooCommerce plugin does) — not a gap unique to our implementation.
- Mitigations layered on top, beyond what the reference plugin itself does:
  - **Single-use tokens**: an attempt can only transition `pending → succeeded`/`failed` once; a replayed callback with an already-consumed token is rejected and logged.
  - **Expiry**: a `pending` attempt older than 24h is treated as `expired` and can no longer be completed by a late callback — closes the window on a stale/leaked callback URL being replayed long after the fact.
  - `WHISH_CHANNEL`/`WHISH_SECRET` are server-side env vars only, never returned to the frontend — same handling as `STRIPE_SECRET_KEY` today.
- The worst case of a forged callback (guessing/replaying a token) is a tenant granting *themselves* Pro access without paying — not a cross-tenant or financial-exfiltration risk, since `BillingPaymentAttempt` is tenant-scoped and the only effect is flipping that tenant's own plan.

## Testing

No real Whish credentials exist yet, so all tests mock the outbound HTTP call to Whish (a fake HTTP client/response double, mirroring how `upstream_portal.py`'s tests mock Playwright). Coverage:
- Checkout creates a correctly-shaped `BillingPaymentAttempt` and sends the correct payload/headers to Whish.
- Success callback: correct token → plan updated correctly (both fresh-Pro and early-renewal-extends-from-current-expiry cases); wrong token → rejected, attempt untouched; already-`succeeded` attempt replayed → rejected.
- Failure callback → attempt marked `failed`, plan untouched.
- Expired (>24h) pending attempt → success callback rejected even with a correct token.
- Scheduler job: reminder fires once within the 5-day window and not again; grace period keeps Pro active for exactly 3 days past expiry; revert happens after that, clearing the right fields.
- Explicit `RUN_SCHEDULER=1` import smoke-test, per the crash-loop lesson.

## Rollout & data safety

Additive-only migration (two new nullable `Tenant` columns, one new table) — fully inert for every existing tenant until they actually attempt a Whish checkout. The Whish button itself stays hidden (via `whish_enabled`) until real credentials are configured, so this can be merged and deployed well before Whish issues the merchant account, with zero user-visible change until that flag flips on. First real payment is a supervised manual smoke test once credentials exist, before considering this fully done — same discipline as the Krypton adapter's rollout.

## Later phases (not designed here)

- The separate multi-currency *accounting* work (tenant-facing customer billing in LBP/other currencies + FX-rate table), originally bundled into "Phase 4" at the roadmap level but explicitly out of scope for this spec.
- Any future non-Lebanon market re-activating the dormant Stripe path — not designed or scoped here; the code is simply left in place, untouched.
