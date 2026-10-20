# Data Model: Automatic WhatsApp Connection

All changes add to the schema; none modify or remove existing columns. A single Alembic migration on top of head `58a2f084761c` does everything:

- **Revision id**: generate it randomly and check that there is still a single head (see the memory note on revision id collisions).
- **Columns on `whats_app_settings`**: add each with `ADD COLUMN IF NOT EXISTS`, inside the `_with_lock_retry` savepoint pattern (it is a hot table, so follow the PR #35 pattern).
- **Existing schema**: check it with `inspect(bind)` first, because production's schema has drifted from the migration history.

## 1. `WhatsAppSettings` (existing table, extended)

Existing per-tenant columns we reuse unchanged: `mode`, `enabled`, `phone_number_id`, `business_account_id`, `app_id`, `app_secret`, `access_token`, `api_version`, the `template_*` columns, `auto_reply_enabled`, `forwarding_mobile`, and `webhook_verify_token`.

| New column | Type | Null | Default | Meaning |
|---|---|---|---|---|
| `connection_method` | String(20) | no | `'manual'` | `manual` \| `coexistence` \| `embedded` |
| `connection_status` | String(20) | no | `'disconnected'` | `connected` \| `needs_attention` \| `disconnected` |
| `status_reason` | String(300) | yes | | Plain-language reason shown with `needs_attention` |
| `meta_business_id` | String(64) | yes | | Business portfolio id returned by Embedded Signup |
| `meta_business_name` | String(200) | yes | | Name of the business account (WABA) |
| `display_phone_number` | String(32) | yes | | For example "+961 3 123 456" |
| `verified_name` | String(200) | yes | | Display name approved by Meta |
| `quality_rating` | String(16) | yes | | GREEN \| YELLOW \| RED \| UNKNOWN |
| `messaging_limit_tier` | String(32) | yes | | For example TIER_250, TIER_2K |
| `payment_method_status` | String(16) | yes | | `present` \| `missing` \| `unknown` (see research R-3, step 7) |
| `registration_pin` | EncryptedString | yes | | 6-digit two-step PIN, used by the `embedded` method only |
| `token_obtained_at` | DateTime | yes | | When the business token was exchanged |
| `connected_at` | DateTime | yes | | |
| `connected_by_user_id` | Integer FK user.id, ON DELETE SET NULL | yes | | |
| `last_health_check_at` | DateTime | yes | | |

**Backfill rule (FR-018)**, run in the same migration: for each existing row, `connection_method='manual'`. If `access_token` and `phone_number_id` are present, `connection_status='connected'`; otherwise `'disconnected'`. Sending behavior does not change; it still keys off `mode` and the presence of credentials.

**Invariants**

- When `connection_method in ('coexistence','embedded')`, `app_id` and `app_secret` are NULL, and the app identity comes from platform config (research R-4).
- **One number, one tenant**: no two rows with a non-null `phone_number_id` may share it, and the same holds for `business_account_id`. This is enforced in application code when connecting (FR-009). It is *not* added as a DB unique index in this migration: existing manual data may already have duplicates (the webhook handler logs "N settings rows share business_account_id"), and a unique index would fail on deploy. Adding the index is a follow-up, once a production query shows there are no duplicates.
- `connection_status='needs_attention'` → automatic sends are not attempted (research R-7).

**State transitions for `connection_status`**

```
disconnected --(connect/complete OK)--> connected
connected --(account_update PARTNER_APP_UNINSTALLED | PARTNER_REMOVED | ACCOUNT_DELETED | DISABLED_UPDATE | ACCOUNT_OFFBOARDED,
             Graph error 190/200 on send, health check failure)--> needs_attention
needs_attention --(account_update ACCOUNT_RECONNECTED | reconnect OK | health check OK)--> connected
connected | needs_attention --(disconnect)--> disconnected
any --(connect/complete FAILS)--> unchanged
```

**Side effects of a successful automatic connect**: `mode='api'`, `enabled=True`, and for coexistence also `auto_reply_enabled=False` (FR-013).

**Side effects of disconnect**:

- `access_token`, `registration_pin` and `token_obtained_at` are set to NULL.
- `mode='deeplink'` and `connection_status='disconnected'`.
- `phone_number_id`, `business_account_id` and the display fields are kept for reference until the next connect. They are no longer used for routing: the webhook handler ignores tenants whose status is `disconnected`.

## 2. `WhatsAppConnectionEvent` (new table, `whatsapp_connection_event`)

This is an audit trail (FR-023).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Integer PK | | |
| `tenant_id` | Integer FK tenant.id | no | indexed |
| `user_id` | Integer FK user.id ON DELETE SET NULL | yes | NULL for webhook or scheduler events |
| `event` | String(32) | no | `connect`, `reconnect`, `method_change`, `disconnect`, `status_change`, `connect_failed` |
| `method` | String(20) | yes | Connection method at the time |
| `from_status` / `to_status` | String(20) | yes | |
| `source` | String(20) | no | `user`, `webhook`, `send_error`, `health_check` |
| `detail` | String(500) | yes | Meta error message, account_update event name, or reason |
| `created_at` | DateTime | no | indexed |

Rows are never updated. The latest 20 per tenant are shown in Settings.

## 3. `WhatsAppMessage` (existing)

- `sender` gains the value **`'phone'`**, which fits the existing String(10), so no migration is needed. It marks outbound messages that the tenant typed in the WhatsApp Business app (coexistence echoes, FR-012).
- Echo rows have `direction='out'`, `status='sent'`, `wa_message_id` set to the echo id, and are deduped by the existing `uq_whatsapp_message_tenant_wamid` constraint.
- **Revoke echo**: the original row's `text` is kept, and its `status` is set to `'revoked'` (fits String(12)).
- **Edit echo**: `text` is replaced, and an `(edited)` flag is shown in the UI from `status='edited'`.

## 4. `WhatsAppTemplate` (existing, extended)

| New column | Type | Null | Meaning |
|---|---|---|---|
| `standard_key` | String(40) | yes | Catalog key if the template was created from the standard catalog (for example `payment_paid`) |
| `param_map` | JSON | yes | Ordered list of the semantic variable keys for `{{1}}..{{n}}` in the body, for example `["customer_name","amount","balance"]`. NULL means the legacy fixed order. |

## 5. Standard template catalog (code, not DB): `wa_standard_templates.py`

Each entry has:

- `key`
- `default_name`, matching the existing `WhatsAppSettings.template_*` defaults so that the send path picks it up with no settings change
- `settings_field`, for example `template_payment_paid`
- `category`
- `language`
- `header` / `body` / `footer` / `buttons`, with named placeholders
- `variables`, the required set, in the legacy order
- `sample_values`, used for Meta's `example`

| key | settings_field | variables (legacy order) |
|---|---|---|
| payment_paid | template_payment_paid | customer_name, amount, balance |
| subscription_created | template_subscription_created | (taken from the send mapping during implementation) |
| subscription_renewed | template_subscription_renewed | customer_name, plan, expiry_date, balance |
| payment_reminder | template_payment_reminder | customer_name, balance, expiry_date |
| current_balance | template_current_balance | customer_name, balance, expiry_date |
| payment_link | template_payment_link | body: none; URL button: pay_token |
| forward_alert | template_forward_alert | (taken from `escalate_to_human`) |
| forward_keepalive | template_forward_keepalive | (taken from `send_daily_whatsapp_keepalive`) |
| bulk_outage | template_bulk_outage | message |
| bulk_maintenance | template_bulk_maintenance | location, eta |
| bulk_feature | template_bulk_feature | message |
| bulk_offer | template_bulk_offer | message |

Rows marked "taken from" must be read from the current send code before the catalog is written. A unit test pins each catalog entry's `variables` against what the send path actually passes.

**Validation rules (FR-015b)**:

- The set of placeholders in the edited text must equal `variables`: none missing, none extra, none unknown.
- Each variable must appear exactly once.
- The body must not start or end with a variable (a Meta rule).
- Text must stay within Meta's length limits: body 1024, header 60, footer 60.
- The template name must not collide with an existing synced template unless the tenant chose "use existing".
