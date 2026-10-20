# HTTP Contracts: Automatic WhatsApp Connection

All routes are JSON. Unless stated otherwise, each one requires `@jwt_required`, `@admin_required()` and `@require_module('whatsapp')`.

Responses fail in these ways:

| Status | Meaning |
|---|---|
| 402 | The plan lacks `whatsapp_api` |
| 404 | The feature is disabled: platform Meta config is missing, or the instance is on-prem |
| 400 / 409 / 502 | Validation, conflict and upstream Meta errors, as `{"error": "<plain language>", "meta_code": <int|null>}` |

## GET `/api/whatsapp/connect/config`

This tells the UI which methods to offer, and with what Facebook SDK parameters. No secrets are returned.

```json
{
  "automatic_available": true,
  "coexistence_available": true,
  "app_id": "1234567890",
  "graph_version": "v24.0",
  "es_config_id": "111",
  "es_coex_config_id": "222",
  "coex_extras": {"featureType": "whatsapp_business_app_onboarding", "sessionInfoVersion": "3"},
  "plan_allows_api": true
}
```

## POST `/api/whatsapp/connect/complete`

The frontend calls this immediately after `FB.login` returns a code, because the code expires after 30 seconds.

**Request**

```json
{
  "method": "coexistence" | "embedded",
  "code": "<auth code>",
  "waba_id": "…",
  "phone_number_id": "…",
  "business_id": "…",
  "es_event": "FINISH" | "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"
}
```

**Behavior**: the steps in research.md R-3, run in order. The request is idempotent if the same tenant and the same `phone_number_id` are already connected: it refreshes the stored values and returns 200.

**200**: `{ "connection": <ConnectionView> }`

**Errors**

| Status | Cause |
|---|---|
| 400 | Missing fields; `method` and `es_event` don't match; or the code exchange failed (expired or invalid code) |
| 403 | The token does not grant access to the claimed `waba_id` |
| 409 | `{"error": "This WhatsApp number is already connected to another account."}` (FR-009); or another admin finished first |
| 502 | A Meta call failed. The body includes the plain-language reason, for example "Number not eligible for coexistence" or "Registration limit reached, try again in 72 h" |

Every outcome writes a `WhatsAppConnectionEvent`, including `connect_failed` with the reason. On any error, the stored configuration is unchanged.

## GET `/api/whatsapp/connect/status`

**200**: `{ "connection": <ConnectionView>, "events": [<ConnectionEvent> × ≤20] }`

## POST `/api/whatsapp/connect/refresh`

Runs the health check now: it re-fetches the number and account details and updates the status.

**200**: `{ "connection": <ConnectionView> }`

## POST `/api/whatsapp/connect/disconnect`

**Request**: `{ "confirm": true }`

**Behavior**:

- Calls `DELETE /{waba_id}/subscribed_apps` on a best-effort basis; failures are logged, not fatal.
- Never deregisters the number. This is forbidden for coexistence, and for an `embedded` number it would strand the tenant's number.
- Clears the credentials, sets `mode='deeplink'`, and writes an event.

The same route works for `manual` tenants: it clears `access_token` and `app_secret`.

**200**: `{ "connection": <ConnectionView> }`

## ConnectionView

```json
{
  "method": "manual" | "coexistence" | "embedded",
  "status": "connected" | "needs_attention" | "disconnected",
  "status_reason": "string|null",
  "display_phone_number": "+961…",
  "verified_name": "…",
  "meta_business_name": "…",
  "quality_rating": "GREEN",
  "messaging_limit_tier": "TIER_250",
  "payment_method_status": "present" | "missing" | "unknown",
  "connected_at": "2026-10-20T09:00:00Z",
  "connected_by": "username|null",
  "last_health_check_at": "…"
}
```

It never includes a token, secret or PIN.

## Standard templates

### GET `/api/whatsapp/standard-templates`

Returns the catalog merged with the tenant's synced templates.

```json
{
  "templates": [
    {
      "key": "payment_paid",
      "name": "payment_confirmation",
      "category": "UTILITY",
      "language": "en",
      "header": null,
      "body": "Dear {customer_name}, we received your payment of {amount}. Balance: {balance}.",
      "footer": null,
      "buttons": [],
      "variables": [{"key": "customer_name", "label": "Customer name"}, …],
      "existing": {"status": "APPROVED", "id": 12} | null
    }
  ]
}
```

### POST `/api/whatsapp/standard-templates/submit`

**Request**

```json
{
  "language": "en",
  "items": [
    {"key": "payment_paid", "name": "payment_confirmation", "header": null, "body": "…{customer_name}…", "footer": null, "buttons": [], "use_existing": false}
  ]
}
```

**Behavior**:

- Validates every item (data-model.md §5).
- Valid items are submitted one by one through the existing create path, which stores `standard_key` and `param_map`.
- Invalid items are not submitted.
- Each submitted name is written into the matching `WhatsAppSettings.template_*` field.

**200**:

```json
{
  "results": [
    {"key": "payment_paid", "ok": true, "status": "PENDING"},
    {"key": "bulk_offer", "ok": false, "error": "Missing variable {message}"}
  ]
}
```

This works for every connection method (FR-015d). It requires `mode='api'` with a token and a WABA present, the same guard as today's template routes.

## Changed existing routes

| Route | Change |
|---|---|
| `GET /api/whatsapp-settings` | Adds `connection` (a ConnectionView). If `connection_method != 'manual'`, `access_token`, `app_secret` and `app_id` are returned as `""`. |
| `POST /api/whatsapp-settings` | If `connection_method != 'manual'`, these fields are ignored: `phone_number_id`, `business_account_id`, `app_id`, `app_secret`, `access_token`, `api_version`, `webhook_verify_token`. The automatic `subscribed_apps` call is skipped. |
| `POST /api/whatsapp/subscribe-waba` | Adds `@admin_required()`. Uses `wa_app_identity` (no visible change). |
| `GET /api/whatsapp/webhook` | Also accepts `hub.verify_token == META_WEBHOOK_VERIFY_TOKEN`. |
| `POST /api/whatsapp/webhook` | HMAC key comes from `wa_app_identity(tenant)`. Adds handling for the `account_update`, `phone_number_quality_update`, `smb_message_echoes` and `history` fields (`history` is acknowledged and ignored). Ignores tenants with `connection_status='disconnected'`. |
| `POST /api/internal/scheduled-jobs/whatsapp_connection_health` | New scheduled job (`CRON_TRIGGER_SECRET`), hourly. |

## Platform environment variables (config.py + render.yaml, `sync: false`)

| Var | Required for automatic methods | Notes |
|---|---|---|
| `META_APP_ID` | yes | Platform Tech Provider app |
| `META_APP_SECRET` | yes | Used for the code exchange and webhook HMAC |
| `META_ES_CONFIG_ID` | yes | Login for Business config for Embedded Signup |
| `META_ES_COEX_CONFIG_ID` | no | Falls back to `META_ES_CONFIG_ID` |
| `META_COEXISTENCE_ENABLED` | no (default `true`) | Kill switch if +961 turns out ineligible |
| `META_WEBHOOK_VERIFY_TOKEN` | yes | Random string, set in the App Dashboard |
| `META_GRAPH_VERSION` | no (default `v24.0`) | Used by the new code paths. Manual tenants keep their own `api_version`. |
