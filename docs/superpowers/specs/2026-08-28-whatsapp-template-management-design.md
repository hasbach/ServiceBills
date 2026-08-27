# WhatsApp Template Management — Design

**Status (2026-08-28): design approved section-by-section during brainstorming. Ready for an implementation plan.**

## What this is

Today, a tenant using `WhatsAppSettings.mode == 'api'` (Meta Cloud API, Pro-plan gated via `plans.PLANS[...]["whatsapp_api"]`) must create and get approval for every WhatsApp message template in **Meta Business Manager**, then come back to ServiceBills and type the approved template's exact name into one of nine free-text fields in the Settings → WhatsApp tab (`template_payment_paid`, `template_payment_reminder`, etc. — `SettingsView.js:407-454`). ServiceBills already reads templates from Meta (`GET /{waba-id}/message_templates`, `app.py:4676-4724`) for a picker in the Messaging view, and already knows how to compose a send payload from a template's structure (`build_meta_template_payload`, `app.py:4816-4923`) — but it has never been able to **create** a template, and it silently drops any template that isn't `APPROVED` (so a tenant has no visibility into `PENDING`/`REJECTED` from inside ServiceBills at all).

This spec adds full template lifecycle management — create, edit, delete, and real-time approval-status tracking — directly inside ServiceBills, so a tenant never needs to open Meta Business Manager for this again. It sits under Settings, next to the existing WhatsApp Notifications tab, since it operates on the same per-tenant `WhatsAppSettings` credentials.

## Explicit non-goals

- **No `AUTHENTICATION`-category templates.** Meta's authentication category is a fixed OTP/one-time-password format meant for login/verification flows. ServiceBills has no customer login or OTP flow anywhere in the product — building UI for a template type nothing in the app would ever send is dead weight. Only `MARKETING` and `UTILITY` are supported, which is every category this app's existing sends actually use.
- **No editing an `APPROVED` template's content.** Meta's own console doesn't really support this either in the general case — a tenant wanting different wording creates a new template. Edit is only available while a template is `PENDING` or `REJECTED` (see Backend API).
- **No carousel/multi-product or other newer Meta template types** beyond the standard header/body/footer/buttons structure.
- **No in-app media library.** A media (image/video/document) header's required Meta "example" asset is uploaded once per template, at creation time, via Meta's resumable-upload API — not managed as a reusable asset library inside ServiceBills.
- **No platform-level Meta App credential.** Every call in this feature uses the tenant's own existing `WhatsAppSettings.access_token`/`business_account_id`, the same System User token already used for sending — confirmed via exploration that no app-level Meta credential exists anywhere in this codebase today, and this feature doesn't introduce one. (The tenant's System User token needs the `whatsapp_business_management` permission in addition to the `whatsapp_business_messaging` permission it already needs for sending — a note to add to the in-app "Quick Setup Reference" text, not a code change.)

## Architecture

**New tenant-scoped table `WhatsAppTemplate`** — a local cache of the tenant's Meta templates, mirroring the shape of other small tenant-scoped tables (e.g. `CustomerFeedback`). Meta remains the authoritative source of truth; this table exists so the list screen loads fast, so a template's history persists in ServiceBills even before Meta approves it, and so the status webhook has a row to update.

```python
class WhatsAppTemplate(db.Model):
    """Local cache of a tenant's Meta WhatsApp message templates. Meta is the
    source of truth; this table exists for fast list rendering and as the
    target of the message_template_status_update webhook. Reconciled against
    Meta's live GET on manual refresh (POST /api/whatsapp/templates/sync)."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    language = db.Column(db.String(10), nullable=False)
    category = db.Column(db.String(20), nullable=False)  # 'MARKETING' | 'UTILITY'
    status = db.Column(db.String(20), nullable=False, default='PENDING')  # PENDING, APPROVED, REJECTED, PAUSED, DISABLED
    rejected_reason = db.Column(db.String(500), nullable=True)
    components = db.Column(db.JSON, nullable=False)  # full header/body/footer/buttons structure, Meta's own shape
    meta_template_id = db.Column(db.String(64), nullable=True, index=True)  # Meta's template ID, used for edit/delete
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
```

`WhatsAppTemplate` is added to **both** `TENANT_OWNED_MODELS` and `_TENANT_DELETE_ORDER` from day one. This directly closes the exact class of bug already found and fixed twice before (`MonthlyProfitEstimate` in Phase 3, `BillingPaymentAttempt` in Phase 4a) and flagged a third time as an open follow-up (six other models currently missing from `_TENANT_DELETE_ORDER`) — rather than reproducing it an eighth time, this table is placed correctly in both lists as part of this implementation, and a regression test (see Testing) asserts every `TENANT_OWNED_MODELS` entry also appears in `_TENANT_DELETE_ORDER` so this can't silently drift again for any future model, including the six/seven already known to be missing today (that separate cleanup is still its own follow-up item, tracked outside this spec — this spec only guarantees it doesn't add a new instance of the bug).

**Meta API calls added** (all authenticated with the tenant's existing `WhatsAppSettings.access_token`, scoped to `WhatsAppSettings.business_account_id`):
- `POST /{waba-id}/message_templates` — create/submit a new template.
- `POST /{template-id}` — edit a `PENDING`/`REJECTED` template's components (Meta resubmits it for review).
- `DELETE /{waba-id}/message_templates?name=...` — delete.
- Existing `GET /{waba-id}/message_templates` — reused for the manual "Refresh from Meta" sync action, and to reconcile templates created directly in Meta Business Manager (before this feature existed, or by habit) into the local cache.
- Meta's resumable-upload API — used once, at template-creation time, when a media (image/video/document) header needs an "example" asset handle for submission.

**Status sync (real-time + manual fallback)**: the existing WhatsApp webhook handler (`app.py:5760`, already does per-tenant HMAC-SHA256 verification before any side effect) gains a branch for Meta's `message_template_status_update` webhook field. The payload's `entry[].id` is the WABA ID, used to resolve the owning `WhatsAppSettings` row by `business_account_id` (the same tenant-resolution pattern already used for `phone_number_id` on message/status events), then the matching `WhatsAppTemplate` row is found by `name`+`language` and its `status`/`rejected_reason` updated. Before this ships, confirm `message_template_status_update` is included among the webhook fields subscribed via the existing `/subscribed_apps` call (`app.py:4646`) and add it if not. The manual "Refresh from Meta" button (`POST /api/whatsapp/templates/sync`) is the fallback for a missed webhook event, or a template whose status changed outside ServiceBills entirely.

## Backend API

New routes, all `@jwt_required()` + `@admin_or_finance_required()` (matching the existing `/api/whatsapp-settings` gate), and all requiring the tenant to be in `WhatsAppSettings.mode == 'api'` with `plans.limits(tenant.plan)["whatsapp_api"]` true — the same `whatsapp_api` plan gate already enforced at `app.py:4624` for turning API mode on at all. Templates have no meaning in `deeplink` mode, so this is inheriting the existing gate, not introducing a new one.

- **`GET /api/whatsapp/templates`** — rewritten to read from the local `WhatsAppTemplate` cache and return every status, not just `APPROVED` (fixing today's silent-drop behavior).
- **`POST /api/whatsapp/templates/sync`** — calls Meta's `GET` live and upserts local rows: new templates found, status changes, anything created outside ServiceBills.
- **`POST /api/whatsapp/templates`** — create. Validates the builder payload server-side (required `BODY`, sequential `{{1}}, {{2}}...` variables each with a required sample value — Meta rejects submissions missing these — category restricted to `MARKETING`/`UTILITY`, button type/count combinations within Meta's limits), calls Meta's `POST`, stores the local row with whatever `status` Meta's response carries (typically `PENDING`).
- **`PUT /api/whatsapp/templates/<id>`** — edit. Only allowed when the local `status` is `PENDING` or `REJECTED`; calls Meta's `POST /{template-id}`, resets local `status` to `PENDING`.
- **`DELETE /api/whatsapp/templates/<id>`** — calls Meta's `DELETE`, removes the local row.

**Error handling**: Meta's template APIs return detailed validation errors (duplicate name, malformed variable numbering, immediate policy rejection before human review even reaches it). Every route parses Meta's `error.error_user_msg`/`error.message` and surfaces it verbatim to the tenant, following the same pattern already used for Whish checkout failures (`checkout → real Whish API call → correctly-parsed rejection → 502 → frontend error banner`) rather than a generic failure message.

## Frontend UI

**New Settings tab, "WhatsApp Templates"** (`SettingsView.js`'s existing `Tabs`/`Tab` bar gains one more entry, alongside "WhatsApp Notifications"). The tab's content lives in its own component, **`WhatsAppTemplatesManager.js`**, following the same self-contained-CRUD-component-as-Settings-sub-tab pattern already used by `ExpenseCategoryManager.js`/`SectorManager.js` — keeping this out of the already-558-line `SettingsView.js`.

- **List**: a table of the tenant's templates — name, category, language, status (a colored chip: grey `PENDING`, green `APPROVED`, red `REJECTED` with the reason on hover, orange `PAUSED`/`DISABLED`), last-synced time. A "Refresh from Meta" button calls the sync route. Row actions: Edit (disabled once `status === 'APPROVED'`) and Delete.
- **"New Template" builder dialog**, following the existing dialog patterns from `SubscriptionPlanForm.js`:
  - Name, Category (`MARKETING`/`UTILITY`), Language (dropdown, defaulting to the tenant's existing `template_language`).
  - Header: None / Text / Image / Video / Document. A text header gets a text field with an optional single `{{1}}` variable. A media header prompts for a sample file, uploaded via Meta's resumable-upload flow to obtain the example handle Meta requires for submission.
  - Body (required): multiline text, an "Insert variable" control that appends the next `{{n}}`, and a required sample-value field per variable used (Meta rejects submissions missing these).
  - Footer: optional single-line text, no variables (Meta's own constraint).
  - Buttons: URL (static, or one dynamic `{{1}}` suffix — mirroring `build_meta_template_payload`'s existing dynamic-URL-suffix logic), Phone Number, or Quick Reply, addable up to a sane soft limit. Rather than hardcoding every one of Meta's exact numeric/mix-and-match limits client-side (these have shifted across Graph API versions), the client applies reasonable soft caps and defers hard validation to Meta's own API error response, surfaced through the error handling above.
  - A live preview pane approximating the composed WhatsApp message, reusing the existing preview pattern from the deep-link settings section.

**Settings WhatsApp Notifications tab changes**: the nine free-text "Approved Template Names" fields (`SettingsView.js:407-454`) become `TextField select` dropdowns (matching the existing `network_mode` pattern), populated from local `WhatsAppTemplate` rows filtered to `status === 'APPROVED'` — removing the risk of a typo'd or unapproved name silently breaking a send.

## Testing

Mirrors the project's established mocked-HTTP-client pattern (e.g. `test_whish_billing.py`):

- **CRUD routes**: create validates required `BODY`/sample values and rejects `AUTHENTICATION` category; edit is blocked once `status == 'APPROVED'`; delete removes the local row. All mock the Meta HTTP calls, no real credentials.
- **Webhook**: a `message_template_status_update` event updates the correct tenant's `WhatsAppTemplate` row via WABA-ID lookup; an unmatched WABA ID is a no-op, not an error; the existing HMAC signature check still gates before any of this runs.
- **Plan gating**: template routes 403 when the tenant isn't in `mode == 'api'` or doesn't have `whatsapp_api` on their plan — same shape as the existing `test_whatsapp_api_mode_gated_by_plan`.
- **Tenant-delete regression**: a test asserting every model in `TENANT_OWNED_MODELS` also appears in `_TENANT_DELETE_ORDER`, so `WhatsAppTemplate` (and any future model) can't silently repeat this bug. This test only needs to pass for models this spec touches; it will fail against the six/seven pre-existing models already missing today until that separate follow-up is done — the implementation plan should decide whether to scope the new test to exclude the known-bad set (with a comment pointing at the tracked follow-up) or take the opportunity to fix all of them in the same PR.
