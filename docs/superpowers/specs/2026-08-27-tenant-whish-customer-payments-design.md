# Tenant-Facing Whish Customer Payments — Design (Brainstorm)

**Status (2026-08-27): design-only brainstorm, per explicit instruction ("start brainstorming it"). No implementation plan, no implementation code. Not ready to move to an implementation plan until the open product questions at the end are answered by the business owner.**

## What this is, and how it differs from the 2026-08-26 Whish spec

The [2026-08-26 spec](2026-08-26-whish-self-serve-billing-design.md) covers **ServiceBills charging its own tenants** for their Pro-plan subscription, using ServiceBills' single platform-wide Whish merchant account (`WHISH_CHANNEL`/`WHISH_SECRET` as global env vars). Money flows tenant → ServiceBills.

This spec covers a fundamentally different flow: **each tenant accepting Whish payments from their own customers** (the end subscribers paying for internet/service access), with the money going to **that tenant's own** bank/wallet via **that tenant's own** Whish merchant account. Money flows end-customer → tenant, and ServiceBills is not a party to the transaction at all (beyond hosting the software that requests it).

This also introduces something ServiceBills has never had: a genuinely **customer-facing** surface. Every existing route in this app is staff-facing (JWT-authenticated tenant users) or platform-admin-facing (superadmin). This spec's payment page is the first page in the product that an end customer — someone who has never logged in, never will, and shouldn't need to — will ever see.

## Explicit non-goals (keeping this a reviewable first pass)

- **No customer login/account system.** A customer never creates credentials, never has a session, never sees a dashboard of their own history. Every interaction is a single-use link scoped to exactly one `Payment`.
- **No recurring/auto-charge for customer subscriptions.** Whish itself has no subscription concept (per the 2026-08-26 spec's findings) — every payment here is for one specific `Payment` row, exactly as platform billing is for one `BillingPaymentAttempt`.
- **No cart / multi-invoice payment.** One link pays exactly one `Payment`. A customer with three unpaid payments gets three separate links (or, per an open question below, possibly none of that is even in scope for v1 — see Open Questions).
- **No in-app "customer payments" analytics/report screen in this pass.** The data model captures everything needed for one to be built later; building the staff-facing report UI itself is follow-up work.
- **No changes to `Payment.amount` from the customer side.** The customer cannot negotiate, partially pay, or override the amount — the link is generated for a specific `Payment` at its current `amount`, full stop. A staff member wanting to accept a different amount edits the `Payment` first (existing functionality) and the old link is invalidated (see Data model).
- **No platform fee / revenue-share logic.** Whether ServiceBills takes a cut of a tenant's customer's Whish payment is a business decision, not designed here — see Open Questions.
- **No changes to the existing WhatsApp Cloud API template-management flow.** This spec identifies that sending a payment link via WhatsApp needs its own approved message template (see Delivery below) but does not design the template-registration UX.
- **No SMS delivery channel.** Only WhatsApp (via existing infra, best-effort), email, and manual copy/paste are designed here. SMS was mentioned as a hypothetical in the task brief but this app has no SMS integration today and adding one is out of scope.

## Architecture

Three new pieces, deliberately parallel to (not sharing implementation with) the platform-billing equivalents, per the explicit instruction that the target of payment (Tenant's subscription vs. Customer's invoice) and the credentials used (platform-wide vs. per-tenant) are fundamentally different:

1. **Per-tenant Whish credential storage** — a new settings table, encrypted at rest, mirroring `WhatsAppSettings`.
2. **A parallel attempt-tracking table**, scoped to `Payment`/`Customer` rather than `Tenant` — mirrors what `BillingPaymentAttempt` does for platform billing, but the "what was this payment for" pointer is a `Payment` row, not a billing cycle.
3. **A new public, unauthenticated Flask blueprint area** (`/pay/<token>`-shaped routes) — the first genuinely customer-facing surface in the app. Reuses `whish_billing.py`'s HTTP client function (`create_payment`, header construction) since Whish's actual API shape doesn't change based on who's paying whom — but the higher-level flow (what happens on success, what emails/messages fire, how the token maps to state) is new, parallel code, not a shared abstraction with `_apply_whish_payment_success` (platform billing's success handler), because "advance a Payment to paid and touch a Customer's balance" and "advance a Tenant's plan/expiry" have nothing in common beyond both flipping a status flag.

## Data model

### `TenantWhishSettings` (per-tenant Whish merchant credentials)

```python
class TenantWhishSettings(db.Model):
    """A tenant's own Whish merchant credentials, for accepting payments from
    THEIR customers (distinct from the platform-wide WHISH_CHANNEL/WHISH_SECRET
    env vars used for Pro-plan billing -- see
    docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md).
    Mirrors WhatsAppSettings' encrypted-credential pattern."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    whish_channel = db.Column(EncryptedString, nullable=True)  # encrypted at rest, like WhatsAppSettings.access_token
    whish_secret = db.Column(EncryptedString, nullable=True)   # encrypted at rest
    # The Whish "requestee"/target/email shown on their hosted payment page --
    # defaults to BusinessSettings' values if unset, overridable here in case a
    # tenant wants a different display name on the payment page than internally.
    display_name_override = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

Follows `WhatsAppSettings`' exact pattern: `EncryptedString` (Fernet, `FERNET_KEY`-backed, defined in `crypto.py`) for both secret fields, a boolean `enabled` gate, one row per tenant. `enabled` only flips to meaningfully-true once both credential fields are populated (validated at the settings-save route, same as how `whish_enabled` in `/api/billing/config` checks both `WHISH_CHANNEL`/`WHISH_SECRET` are set).

### `CustomerPaymentLink` (the parallel-to-`BillingPaymentAttempt` table)

```python
class CustomerPaymentLink(db.Model):
    """One Whish payment link generated for a specific customer Payment. The
    single-use, signed-token security boundary for the public payment page
    and Whish callback -- see this spec's Security section. Parallel to
    BillingPaymentAttempt (platform billing) but scoped to Payment/Customer,
    not Tenant, since this is a tenant's customer paying that tenant, not the
    tenant paying ServiceBills."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=False, index=True)
    # The amount/currency this link was generated for -- snapshotted at
    # creation, NOT re-read from Payment at Whish-callback time. If a staff
    # member edits Payment.amount after a link is generated, this link's
    # snapshot goes stale and is explicitly invalidated (see status='stale'
    # below), rather than silently completing at a since-changed amount.
    amount = db.Column(db.Numeric(18, 4), nullable=False)  # matches Part 1's Numeric money-column convention
    currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False)  # from Part 1's Currency model
    # The customer-facing PAGE token (long-lived within expiry, safe to view
    # repeatedly, reusable across page reloads/retries) vs. the Whish
    # CALLBACK token (single-use, only Whish's redirect ever presents it).
    # Two tokens because "view this invoice" and "authorize this invoice as
    # paid" are different privilege levels -- see Security.
    view_token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    callback_token = db.Column(db.String(64), nullable=False)
    whish_external_id = db.Column(db.String(64), unique=True, nullable=True, index=True)  # set once Whish create_payment succeeds
    status = db.Column(db.String(10), nullable=False, default='pending')  # pending, succeeded, failed, expired, stale
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)  # see Security for the expiry-window discussion
    completed_at = db.Column(db.DateTime, nullable=True)
```

Why two tokens (`view_token`, `callback_token`) instead of one, unlike `BillingPaymentAttempt`'s single `callback_token`: platform billing's flow is staff-initiated end-to-end (an admin clicks "Upgrade," is redirected straight to Whish, the token only ever needs to survive one browser round-trip through Whish's own redirect). This flow instead **starts** with a link a customer opens cold, days after a staff member generated it, possibly from a forwarded WhatsApp message on a different device — that link needs to be safely re-openable (network hiccup, customer re-reads the message tomorrow, customer's payment failed and they retry) without each reopen being a sensitive "authorize payment" action. Splitting the concern: `view_token` gates "show this specific customer's specific invoice amount and a Pay button" (repeatable, read-only); `callback_token` gates "Whish confirms money moved, mark this specific attempt paid" (single-use, exactly mirroring `BillingPaymentAttempt.callback_token`'s existing model).

`CustomerPaymentLink` is tenant-scoped (added to `TENANT_OWNED_MODELS`) even though its point is to be reachable by someone with **no** tenant session — tenancy here governs who can *create* a link (a tenant's own staff, scoped to that tenant's own customers/payments) and how it's looked up server-side (still filtered by the token, but the row itself still belongs to a tenant for every other purpose: reporting, cleanup, cascade-delete when a tenant is deleted).

**Link invalidation on `Payment` mutation**: if `Payment.amount` changes, `Payment` is deleted, or `Payment.paid`/`is_refund`/`reverted_at` changes after a `CustomerPaymentLink` was generated for it, any `pending` link for that `Payment` transitions to `status='stale'` (a lightweight guard — e.g. a `before_flush` check or an explicit call at the same call sites that already mutate those fields) and both tokens stop working. This closes an gap the platform-billing flow doesn't have to worry about (a `BillingPaymentAttempt`'s "amount" is a fixed plan price, never edited after creation) but a `Payment`'s amount is very much editable in this app today.

## Payment flow, end to end

1. **Staff generates a link.** From the existing Payment view/list (staff-facing, JWT-authenticated), a new "Send payment link" action calls `POST /api/customers/<id>/payments/<payment_id>/whish-link` (JWT + admin/finance, mirroring `add_payment`'s role gate). Validates: `TenantWhishSettings.enabled`, `Payment.paid == False`, `Payment.currency` is one Whish actually supports (`USD`/`LBP` only, per the 2026-08-26 spec's findings — reject with a clear error for any other `Currency` Part 1's general model might someday allow). Creates a `CustomerPaymentLink` row (`view_token`/`callback_token` = `secrets.token_urlsafe(32)`-equivalent, not `uuid4().hex` — see Security for why). Returns the shareable URL: `{APP_BASE_URL}/pay/{view_token}`.
2. **Staff delivers the link** to the customer — three interchangeable paths, all producing the same URL from step 1 (see Delivery below): (a) an explicit "Send via WhatsApp" button that reuses this app's existing WhatsApp-sending infrastructure, (b) an explicit "Send via email" button (best-effort, mirrors `email_util.send`'s existing fire-and-forget pattern elsewhere in this app), (c) a plain "Copy link" button for the staff member to paste anywhere themselves (SMS via their own phone, a different messaging app, in person). The link/page itself has zero dependency on which delivery path was used.
3. **Customer opens `GET /pay/<view_token>`** — public, no JWT. Looks up the `CustomerPaymentLink` by `view_token`; if missing/expired/stale, shows a generic "This payment link is no longer valid — please contact [tenant's business name/phone from BusinessSettings]" page (never a raw 404/500, and never confirms *why* it's invalid beyond that, to avoid leaking whether a guessed token was close). If valid: renders a minimal page — tenant's business name/logo (from `BusinessSettings`, already public-safe data used elsewhere like receipts), the amount and currency, and a "Pay with Whish" button. No login, no other data about the customer is shown beyond what's needed to confirm "yes, this is my bill" (name + amount; not full address/phone/balance history).
4. **Clicking "Pay with Whish"** calls `POST /pay/<view_token>/checkout` (public, still no JWT — the `view_token` itself is the auth). Server-side: looks up the tenant's `TenantWhishSettings`, calls `whish_billing.create_payment(...)` (the shared HTTP client) with the tenant's own decrypted credentials, `successCallbackUrl`/`failureCallbackUrl` pointing at new `/api/customer-whish/success` / `/failure` routes carrying `callback_token`, sets `whish_external_id` on the `CustomerPaymentLink` row, and returns the `collectUrl` for the frontend to redirect to. (Split into a separate POST rather than generating the Whish payment at link-creation time in step 1, so a `collectUrl` — which Whish may itself time out or invalidate — is only requested right when the customer is actually about to pay, not days earlier when the link was sent.)
5. **Customer pays on Whish's hosted page.** Whish redirects back to the success/failure callback, exactly like platform billing.
6. **Success callback** (`GET /api/customer-whish/success?order=<id>&token=<callback_token>`, public): validates `order`/`callback_token` (constant-time compare, single-use, expiry-checked — see Security), marks `CustomerPaymentLink.status='succeeded'`, marks the underlying `Payment.paid = True` / `paid_at = now()` (the exact same state transition `add_payment`'s "pre_payment" path or a manual "mark paid" action already produces — reusing that existing mutation path, not duplicating its balance-adjustment logic), and fires the existing `send_whatsapp_message(customer, 'payment_paid', ...)` notification if the tenant has that configured (this is the one place this spec DOES reuse existing infra directly, since "customer's payment was confirmed" is exactly the notification that function already exists to send). Redirects the customer's browser to a simple "Thank you, payment received" page (part of the same public `/pay/` surface, not the staff app).
7. **Failure callback**: marks the link `failed`, redirects to a "Payment was not completed — you can try again" page that links back to the same `view_token` page (still valid, since `view_token` isn't consumed by a failed attempt).

## Delivery: how a tenant's staff actually gets the link to a customer

The task brief's framing is right: the link/page must stand alone, not depend on WhatsApp delivery specifically, since email or manual copy-paste must also work — so delivery is designed as three independent buttons producing the same URL, not three different payment flows.

**WhatsApp delivery is the least straightforward of the three**, and deserves its own callout: this app's existing `send_whatsapp_message()` (used for payment confirmations, reminders, etc.) sends via Meta's **Cloud API using pre-approved message templates** — Meta requires every business-initiated WhatsApp message template to be submitted and approved in advance, and a template's structure (fixed body text with placeholder slots, optional buttons) doesn't naturally fit "send an arbitrary URL" without either (a) registering a new approved template with a dynamic **URL button** component (Meta supports this: a button whose URL has an approved static prefix and a dynamic suffix — the `view_token` would be the dynamic suffix), which requires the tenant's WhatsApp Business Account to go through Meta's template-approval process for this specific new template, or (b) tenants running in **`deeplink` mode** (`WhatsAppSettings.mode == 'deeplink'`) simply get a pre-filled `wa.me` link (staff clicks it, their own WhatsApp opens with the message text pre-filled, including the payment URL as plain text) — this already works today with zero new Meta approval needed, since deep-link mode was never template-gated to begin with. **Recommendation for a future implementation pass**: ship deep-link-mode WhatsApp delivery first (near-zero marginal work — it's the same `wa.me` link-building code this app already has, just with a payment URL substituted into the message body), and treat `api`-mode WhatsApp delivery (the new approved-template path) as a clearly-separated follow-up, not bundled into the same implementation task, since it depends on an external approval process this app doesn't control the timeline of.

**Email delivery** is comparatively simple: `email_util.send(to_email, subject, body)` already exists and already has a graceful multi-backend fallback chain (console/SMTP/SendGrid, per `config.py`'s `MAIL_BACKEND`). The only new requirement is that `Customer` doesn't currently have an email field in this app's schema (grep confirms: no `email` column on `Customer`) — sending this way needs either (a) a new nullable `Customer.email` column (small, additive, clearly in scope for the eventual implementation), or (b) the staff member types the destination email ad hoc at send-time without persisting it anywhere. Recommendation: (a), since a persisted customer email is generally useful beyond just this feature and a tenant will want to reuse it.

**Manual copy/paste** needs no new infrastructure — it's just exposing the URL from step 1's response in the UI with a copy-to-clipboard button.

## Currency handling (consistent with Part 1's multi-currency spec)

- `CustomerPaymentLink.currency` is always exactly `Payment.currency` (Part 1's multi-currency spec: every `Payment` already carries the currency it's denominated in, inherited from the customer's `subscription_plan.currency`). No conversion happens here — the customer pays the exact amount, in the exact currency, that their `Payment` row already says they owe. This spec does not introduce any new currency-conversion logic; it consumes Part 1's `Payment.currency` as an input and otherwise stays out of Part 1's territory (no interaction with `fx_rate_to_reporting`, `ExchangeRate`, or `reporting_currency` — those govern the tenant's own internal reporting, not what the customer is asked to pay).
- **Hard constraint**: Whish only supports `USD` and `LBP` (confirmed in the 2026-08-26 spec's reverse-engineered API contract). Part 1's `Currency` model is deliberately general (not a hardcoded 2-value enum), which means a future tenant *could* have a `Payment` denominated in some third currency Part 1's model allows in principle. Generating a Whish link for such a `Payment` must be rejected up front (step 1 above) with a clear error, not attempted and left to fail confusingly at Whish's API. This is the one place this spec must actively defend against a case Part 1's general model otherwise allows.
- A tenant billing in LBP gets an LBP-denominated Whish payment request; the amount is NOT converted to/from the tenant's `reporting_currency` — that concept is purely for the tenant's own internal reports (Part 1) and irrelevant to what the customer is actually asked to pay.

## Security model (threat model distinct from platform billing — thought through fresh, not copied)

The 2026-08-26 spec's threat model concludes: "the worst case of a forged callback is a tenant granting *themselves* Pro access without paying — not a cross-tenant or financial-exfiltration risk." **That conclusion does not carry over here, and the stakes are meaningfully higher**, for reasons specific to this flow:

- **The victim of a forged/leaked token here is not the party who controls the token.** In platform billing, the tenant admin who might exploit their own callback token is the same party whose own money/access is at stake — a low-stakes, self-limiting exploit. Here, the party a forged success callback would defraud is **the tenant's own customer relationship**: if an attacker can cause `CustomerPaymentLink.status` to flip to `succeeded` and `Payment.paid` to flip to `True` **without Whish ever having actually moved money**, the tenant's staff will believe a customer paid when they didn't (and, depending on how this integrates with service-suspension logic elsewhere in the app, could even cause a non-paying customer's internet service to stay active on the strength of a forged confirmation). This is a direct revenue-integrity attack on the tenant, not a self-serve-upgrade edge case.
- **The link is inherently more exposed than platform billing's token.** A platform-billing checkout token exists for the few seconds between an authenticated admin clicking "Upgrade" and Whish's own redirect firing — it is never composed into a shareable message and is never expected to survive being forwarded, screenshotted, or sitting unopened in someone's WhatsApp for a week. This flow's `view_token` is **designed** to be sent via WhatsApp/email and opened by a customer possibly days later, which is a categorically larger exposure window and a real chance of accidental further forwarding (a customer forwards "here's my Wifi bill" to a family member who pays it for them — a legitimate use, but it means this token routinely leaves the direct staff→customer channel by design, unlike platform billing's token).
- **Mitigations, addressing both of the above:**
  - **Split tokens (see Data model)**: only `callback_token` can ever flip `Payment.paid`; `view_token` (the one that's actually shared widely) can only ever read a `Payment`'s amount and initiate a *new* Whish checkout — it can never itself mark anything paid. Leaking `view_token` alone lets someone see the invoice and pay it (on the customer's behalf, if they want to) — it cannot be used to forge a "paid" state.
  - **`callback_token` is never exposed to the browser as a first-class value the customer/attacker directly controls the disclosure of** — it only ever appears embedded in the `successCallbackUrl`/`failureCallbackUrl` sent to Whish's servers, exactly like platform billing's `callback_token`. The realistic leak vector for it is the same as platform billing's (someone captures the callback URL itself, e.g. from browser history, a referrer header, or Whish's own server logs) — not something this spec can fully close (Whish's redirect-plus-shared-token model has no stronger primitive on offer, as the 2026-08-26 spec already established), but the blast radius is now scoped to one `Payment`, not a tenant's whole subscription.
  - **Constant-time comparison** on both tokens (`secrets.compare_digest`, matching the fix already applied to platform billing's callback in commit `d1ce72f`) — never a plain `==`.
  - **High-entropy tokens**: `secrets.token_urlsafe(32)` (256 bits, URL-safe) rather than `uuid.uuid4().hex` (122 bits of actual randomness) — platform billing's `uuid4().hex` was an acceptable choice given its narrow exposure window; this flow's tokens are designed to be shared and sit around, so a wider security margin is warranted. This is a genuine strengthening beyond what the platform-billing spec did, made deliberately, not an oversight in that spec.
  - **Single-use `callback_token`** — a `CustomerPaymentLink` can only transition `pending → succeeded`/`failed` once, exactly mirroring `BillingPaymentAttempt`'s existing single-use guarantee.
  - **Expiry**: `expires_at` set at link-creation time. **Recommendation: 7 days**, not platform billing's 24 hours — a customer invoice link is realistically opened well after the message was sent (unlike an in-session upgrade flow), so a short expiry would cause real, confusing failures ("the link my ISP sent me doesn't work"). This is a genuine, deliberate divergence from the 24h precedent, not a copy-paste — flagged as a recommendation, not a hard decision, since the right window is partly a product call (see Open Questions).
  - **Minimal disclosure on the view page**: only the customer's first name/full name (whichever this app already shows on receipts — reuse that convention) and the amount/currency are shown; no address, phone, balance history, or other account data appears on a page reachable by anyone with the link.
  - **No enumeration surface**: an invalid/expired/unknown `view_token` returns the exact same generic "not valid" page regardless of *why* it's invalid (never-existed vs. expired vs. already-paid vs. stale) — a timing-safe lookup (a DB index lookup on a 256-bit token is not meaningfully timing-attackable the way a byte-by-byte string compare would be, but the response itself must not distinguish "wrong token" from "right token, already used") prevents an attacker from learning anything by probing.
  - **Rate limiting on the public routes** (`/pay/<token>`, its checkout POST, and the callback routes) is a real gap this spec flags but does not fully design — this app has no existing rate-limiting infrastructure to point to (grep confirms no Flask-Limiter or equivalent is in use anywhere today), and a public, unauthenticated, token-guessing-resistant-but-not-token-guessing-proof endpoint is exactly the kind of surface that benefits from one. Listed as an explicit open item for the implementation pass, not solved here.
- **What this spec deliberately does NOT try to solve**: money misdirection (an attacker redirecting funds to *themselves* instead of the tenant) is not a real risk in this design, because the Whish payment is always created server-side using the tenant's own stored, encrypted `TenantWhishSettings` credentials — an attacker controlling a `view_token` can trigger a checkout, but the `collectUrl` and destination account are always determined by the tenant's real credentials, never anything the customer/attacker supplies. The realistic attack surface is entirely about the **paid/unpaid signal**, not fund destination.

## Testing approach (mirrors platform billing's established pattern)

- No real Whish credentials — same mocked-HTTP-client approach as `test_whish_billing.py`.
- `TenantWhishSettings`: encryption round-trip (mirrors `test_crypto.py`'s existing coverage of `EncryptedString`), tenant isolation.
- `CustomerPaymentLink` creation: rejects when `TenantWhishSettings` disabled/unconfigured; rejects a non-Whish-supported currency; rejects for an already-paid `Payment`.
- View route: valid unexpired token renders correctly; expired/stale/unknown token shows the generic invalid page (and returns the *same* response shape/status regardless of reason); already-succeeded link's view page still renders (so a customer re-opening a paid invoice's link sees "already paid," not a broken page) but its checkout POST is rejected.
- Checkout route: creates the Whish payment with the tenant's own (mocked) credentials, not the platform's; correct amount/currency from the snapshotted `CustomerPaymentLink`, not a live re-read of `Payment` (regression test for the staleness handling).
- Success/failure callbacks: correct `callback_token` → `Payment.paid` flips correctly and the underlying balance/notification side effects match what the existing manual "mark paid" path already does; wrong/reused/expired token → rejected, nothing mutated; replay of an already-succeeded callback → rejected.
- Staleness: editing `Payment.amount` (or deleting/refunding it) after a link exists → link transitions to `stale`, both its view and checkout routes reject.
- Tenant isolation: tenant A's staff cannot generate a link for tenant B's customer/payment; a valid token for tenant A's `CustomerPaymentLink` never resolves against tenant B's data (the token itself is the only lookup key on the public routes, so this is really "the row's own `tenant_id`/`customer_id`/`payment_id` foreign keys are internally consistent," verified by a test that a link's `Payment.customer_id == link.customer_id` and both belong to `link.tenant_id`).

## Open product questions for the business owner

These are business/product calls this spec deliberately does not resolve:

1. **Platform fee**: does ServiceBills take any cut of a tenant's customer's Whish payment collected through this feature, or is 100% of it the tenant's (ServiceBills only provides the software, no financial involvement)? This has real implications for whether ServiceBills needs any reconciliation/reporting visibility into these payments at all, or whether it's genuinely none of ServiceBills' business once the tenant's own Whish credentials are in play.
2. **Paid add-on vs. included feature**: is tenant-facing Whish payment collection a Pro-plan feature, a separately-priced add-on, or included for every tenant regardless of plan? Affects the gating logic entirely (a `plans.py`-style limit check, or none at all).
3. **How does a tenant actually obtain their own Whish merchant account/credentials?** This is presumably entirely external to ServiceBills (a tenant applies to Whish directly, the same way the platform's own `WHISH_CHANNEL`/`WHISH_SECRET` had to be obtained) — but if ServiceBills wants to make this easy (an in-app "apply for a Whish account" referral flow, or a supported/preferred path), that's product work not touched here.
4. **Is 7 days the right link expiry?** Recommended in the Security section as a reasonable default, but the actual right answer depends on how this business expects customers to behave (do people pay ISP bills same-day, or do reminders go out over weeks?) — a business call, not an engineering one.
5. **Should `Customer.email` (a new column this spec's email-delivery path needs) be added regardless of whether email delivery ships first**, since it may be independently useful (e.g. for a future customer-facing password-reset-style flow, or just better record-keeping)? Small either way, but worth a decision rather than defaulting silently.
6. **Should there be a staff-facing report of customer Whish payments collected** (a `CustomerPaymentLink`-backed list/report, analogous to how `ReportsView`/`EnhancedReportsView` already report on `Payment`s generally) as part of the first implementation pass, or is that explicitly deferred? This spec's data model supports building one at any time; whether it's in scope for v1 is a product/sequencing call.
7. **Should the customer-facing pages be branded per-tenant** (the tenant's own logo/colors, already partially possible via `BusinessSettings.logo_url`) or use a single ServiceBills-neutral look for the first version? Affects how much of `BusinessSettings`' existing public-safe fields (logo, business name) the `/pay/` page pulls in from day one vs. later polish.
8. **Rate limiting**: this spec flags the public routes as needing rate-limiting (Security section) but this app has no existing rate-limiting infrastructure. Is introducing one (e.g. Flask-Limiter, or an API-gateway-level control) in scope for this feature's first implementation pass, or should it ship without and be hardened in a fast-follow once real traffic patterns are known?
