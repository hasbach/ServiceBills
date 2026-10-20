---
description: "Task list for Automatic WhatsApp Connection (coexistence / new API number / manual)"
---

# Tasks: Automatic WhatsApp Connection

**Input**: Design documents from `specs/001-whatsapp-auto-connect/` ([plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md))

**Tests**: Included. The plan and quickstart §1 name the test files, and repo convention is tests-first with Graph calls mocked through `monkeypatch.setattr(<module>.requests, ...)` and helpers from `tests/inbox_helpers.py`. Write each story's tests first and confirm they fail before implementing.

**Organization**: Tasks are grouped by user story. The shared onboarding service is built in US1, the MVP; US2 adds only what is specific to new numbers.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US4 from spec.md
- Paths are relative to the repo root (the Flask backend sits at the root, the React app in `frontend/src/`)

---

## Phase 1: Setup (Meta spike + configuration)

**Purpose**: Settle the research items marked VERIFY against real Meta, and add the platform configuration. Meta business verification and App Review are owner actions outside the code and should start now, because they are the critical path.

- [ ] T001 Owner action (no code). The existing app "beta business response" (`8757534751003383`, Beta consulting Agency) is already a verified Tech Provider (see research.md R-0). Steps:
  - Create a Facebook Login for Business configuration of type "WhatsApp Embedded Signup" for ServiceBills.
  - Add `servicebills.salloumservices.com` and `servicebills.onrender.com`, plus the dev tunnel domain when testing, to Allowed domains and Valid OAuth redirect URIs.
  - In App settings > Basic, check that the privacy policy URL, terms URL, app icon and app domain are set.
  - Leave the webhook unset until slice 1 is deployed.
  - Record the config ID(s) in `specs/001-whatsapp-auto-connect/research.md` R-0.
- [ ] T002 Run the Meta spike from `specs/001-whatsapp-auto-connect/quickstart.md` §0 with a throwaway HTML page outside the repo. Confirm: (a) the coexistence launch parameters on v4 (`featureType` and/or a separate config ID); (b) that DeltaNet's own +961 Business-app number is accepted for coexistence; (c) that `debug_token` `granular_scopes[].target_ids` contains the WABA ID; (d) whether `GET /{waba_id}?fields=primary_funding_id` reflects a card; (e) whether Meta accepts `{{2}}` before `{{1}}` in a body. Write each outcome back into the matching section of `specs/001-whatsapp-auto-connect/research.md` (R-2, R-3, R-6, R-8).
- [ ] T003 [P] Save the real webhook payloads captured in the spike as fixtures: `tests/fixtures/meta_smb_message_echoes.json` (text, image, edit and revoke variants), `tests/fixtures/meta_account_update_partner_removed.json`, `tests/fixtures/meta_account_update_partner_app_uninstalled.json`, `tests/fixtures/meta_account_update_offboarded.json`, `tests/fixtures/meta_account_update_reconnected.json`, `tests/fixtures/meta_phone_number_quality_update.json` and `tests/fixtures/meta_history.json`. If one can't be captured, build it from the shapes in `specs/001-whatsapp-auto-connect/contracts/webhook-events.md` and add a top-level `"_synthetic": true` key.
- [ ] T004 [P] Add the platform settings to the `Config` class in `config.py`, all read with `os.environ.get`: `META_APP_ID`, `META_APP_SECRET`, `META_ES_CONFIG_ID`, `META_ES_COEX_CONFIG_ID` (falls back to `META_ES_CONFIG_ID`), `META_COEXISTENCE_ENABLED` (default true; parse "false"/"0" as false), `META_WEBHOOK_VERIFY_TOKEN`, `META_GRAPH_VERSION` (default `'v24.0'`). Add matching `- key: META_*` entries with `sync: false` to `render.yaml`, beside the WHISH entries.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: Schema, the app-identity resolver, the connection-status plumbing and the settings and webhook guards. Every story depends on these. Nothing here changes what tenants see.

**⚠️ CRITICAL**: no user-story work starts until this phase is done.

- [ ] T005 Write the failing tests in `tests/test_whatsapp_connect.py` (foundation section):
  - The migration backfill leaves an existing settings row with credentials as `connection_method='manual'`, `connection_status='connected'`, and a row without credentials as `'disconnected'`.
  - `wa_app_identity()` returns the tenant's own `app_id`/`app_secret` for manual tenants and `Config.META_APP_ID`/`META_APP_SECRET` for `coexistence`/`embedded` tenants.
  - `GET /api/whatsapp-settings` returns `access_token`, `app_secret` and `app_id` as `""` for automatic tenants, and includes a `connection` object.
  - `POST /api/whatsapp-settings` ignores `phone_number_id`, `business_account_id`, `app_id`, `app_secret`, `access_token`, `api_version` and `webhook_verify_token` for automatic tenants.
  - The webhook GET accepts `hub.verify_token == META_WEBHOOK_VERIFY_TOKEN`.
  - The webhook POST for an automatic tenant is accepted when signed with `META_APP_SECRET` and rejected (401) when signed with any other secret; a manual tenant keeps today's per-tenant secret behavior.
  - Webhook events for a tenant with `connection_status='disconnected'` are skipped.
- [ ] T006 Create the Alembic migration `migrations/versions/<random12hex>_whatsapp_auto_connect.py` with down_revision `58a2f084761c`. Generate the revision ID randomly, then run `flask db heads` and confirm there is exactly one head. Copy `_with_lock_retry(bind, ddl)` from `migrations/versions/58a2f084761c_network_offline_alerts.py`. Check existing columns with `inspect(bind)` before each ADD; use `ADD COLUMN IF NOT EXISTS` on Postgres and `batch_alter_table` on SQLite.
  - **On `whats_app_settings`**, add exactly:
    - `connection_method` String(20) NOT NULL default `'manual'`
    - `connection_status` String(20) NOT NULL default `'disconnected'`
    - `status_reason` String(300) NULL
    - `meta_business_id` String(64) NULL
    - `meta_business_name` String(200) NULL
    - `display_phone_number` String(32) NULL
    - `verified_name` String(200) NULL
    - `quality_rating` String(16) NULL
    - `messaging_limit_tier` String(32) NULL
    - `payment_method_status` String(16) NULL
    - `registration_pin` Text NULL (holds `EncryptedString` ciphertext)
    - `token_obtained_at` DateTime NULL
    - `connected_at` DateTime NULL
    - `connected_by_user_id` Integer NULL, FK `user.id` ON DELETE SET NULL
    - `last_health_check_at` DateTime NULL
  - **On `whatsapp_template`**, add `standard_key` String(40) NULL and `param_map` JSON NULL.
  - **Create table `whatsapp_connection_event`** with:
    - `id` PK
    - `tenant_id` Integer FK tenant.id NOT NULL, indexed
    - `user_id` Integer FK user.id ON DELETE SET NULL NULL
    - `event` String(32) NOT NULL
    - `method` String(20) NULL
    - `from_status` String(20) NULL
    - `to_status` String(20) NULL
    - `source` String(20) NOT NULL
    - `detail` String(500) NULL
    - `created_at` DateTime NOT NULL, indexed
  - **Backfill**: `UPDATE whats_app_settings SET connection_method='manual', connection_status = CASE WHEN access_token IS NOT NULL AND access_token <> '' AND phone_number_id IS NOT NULL AND phone_number_id <> '' THEN 'connected' ELSE 'disconnected' END`.
  - **No** unique index on `phone_number_id` or `business_account_id` (data-model.md §1).
  - Write a working `downgrade()`.
- [ ] T007 Extend the models in `app.py`.
  - Add the new columns to `WhatsAppSettings` (app.py ~1631), with `registration_pin = db.Column(EncryptedString, nullable=True)` and `connected_by_user_id` as `db.ForeignKey('user.id', ondelete='SET NULL')`.
  - Add `standard_key` and `param_map` (`db.JSON`) to `WhatsAppTemplate` (~1724), and include both in its `to_dict`.
  - Add a new `WhatsAppConnectionEvent` model with `__tablename__='whatsapp_connection_event'`, matching T006 exactly.
  - Add a `connection_view()` method to `WhatsAppSettings` that returns the ConnectionView from `specs/001-whatsapp-auto-connect/contracts/http-api.md`. It must never include a token, secret or PIN.
  - Change `WhatsAppSettings.to_dict()` to blank `access_token`, `app_secret` and `app_id` when `connection_method != 'manual'`.
- [ ] T008 Create `wa_connect.py` with the shared core:
  - `wa_app_identity(settings) -> (app_id, app_secret)`, as described in research.md R-4.
  - `automatic_available() -> bool`: true when `META_APP_ID`, `META_APP_SECRET` and `META_ES_CONFIG_ID` are set and `not onprem.is_onprem()`.
  - `coexistence_available() -> bool`: `automatic_available()` and `META_COEXISTENCE_ENABLED`.
  - `graph_url(path, version=None)`, using `Config.META_GRAPH_VERSION`.
  - `log_event(tenant_id, event, *, user_id=None, method=None, from_status=None, to_status=None, source, detail=None)`, which writes a `WhatsAppConnectionEvent` and truncates `detail` to 500 characters.
  - `set_connection_status(settings, status, reason, source, *, user_id=None, alert=True)`. If the status changed, it updates `connection_status` and `status_reason`, logs a `status_change` event, and when `alert` is set and the new status is `needs_attention`, calls `app.send_push_notification(payload, tenant_id=..., roles=['admin'], topic='whatsapp_inbox')` with the title "WhatsApp needs attention" and the reason as the body.
  - Import `app` lazily inside functions (as `whatsapp_inbox.py` does as `appmod`) to avoid a circular import.
- [ ] T009 Wire the resolver and guards into `app.py`.
  - **Webhook GET** (~9705): also accept `request.args['hub.verify_token'] == Config.META_WEBHOOK_VERIFY_TOKEN`, when that setting is non-empty.
  - **Webhook POST**: in both HMAC checks (template status ~9738 and messages ~9784), replace `settings.app_secret` with `wa_connect.wa_app_identity(settings)[1]`. Skip any resolved tenant whose `connection_status == 'disconnected'` and whose `connection_method != 'manual'`.
  - **`upload_whatsapp_template_sample`** (~8511): use `wa_app_identity(settings)[0]` as the app ID.
  - **`POST /api/whatsapp-settings`** (~7685): when `settings.connection_method != 'manual'`, drop these keys from the incoming data before the setattr loop: `phone_number_id`, `business_account_id`, `app_id`, `app_secret`, `access_token`, `api_version`, `webhook_verify_token`. Also skip the post-save `subscribed_apps` call.
  - **`GET /api/whatsapp-settings`** (~7621): add `'connection': settings.connection_view()`.
  - **`POST /api/whatsapp/subscribe-waba`** (~8281): add `@admin_required()`.
- [ ] T010 Run `python -m pytest tests/test_whatsapp_connect.py tests/test_iso_webhook.py tests/test_whatsapp_templates.py tests/test_iso_whatsapp.py -q`. Fix until it passes.

**Checkpoint**: the schema and guards are in place, manual tenants are unchanged, and the platform app identity is resolvable.

---

## Phase 3: User Story 1 — Connect an existing WhatsApp Business app number (coexistence) (Priority: P1) 🎯 MVP

**Goal**: A tenant connects their phone-app number through Meta Embedded Signup and keeps using the phone. Automatic messages go out from that number, phone-sent messages show in the Inbox, and the standard templates can be edited and submitted so that sending actually works.

**Independent Test**: quickstart §2 steps 3, 5 and 6. Connect a coexistence test number, see "Connected" with the number and business name, send from the phone and see it in the Inbox as "sent from phone", submit the edited standard templates, and after approval record a payment and confirm the customer receives the right values in the right places.

### Tests for User Story 1 ⚠️ write first, they must fail

- [ ] T011 [P] [US1] Add connect-flow tests to `tests/test_whatsapp_connect.py`, mocking `wa_connect.requests.get`/`post`:
  - `POST /api/whatsapp/connect/complete` with `method='coexistence'` exchanges the code (`GET /oauth/access_token` with `client_id`, `client_secret`, `code` and no `redirect_uri`), calls `debug_token`, rejects with 403 when `waba_id` is not in the `whatsapp_business_management` `target_ids`, and fetches `/{phone_number_id}`.
  - It never calls `/register`, calls `POST /{waba_id}/subscribed_apps`, and stores `access_token`, `phone_number_id`, `business_account_id`, `meta_business_id`, `display_phone_number`, `verified_name`, `quality_rating`, `token_obtained_at`, `connected_at` and `connected_by_user_id`.
  - After success: `mode='api'`, `enabled=True`, `connection_method='coexistence'`, `connection_status='connected'`, `auto_reply_enabled=False`, `app_id` and `app_secret` are NULL, and a `connect` event is written.
  - Returns 409 when another tenant has the same `phone_number_id` or `business_account_id`.
  - If any Graph call fails, the existing settings row is unchanged (compare its `to_dict()` before and after) and a `connect_failed` event is written.
  - Returns 402 when the plan lacks `whatsapp_api`, and 404 when `META_APP_ID` is unset.
  - A non-admin (finance role) gets 403.
  - Calling again for the same tenant and the same `phone_number_id` returns 200 and refreshes the values.
- [ ] T012 [P] [US1] Create `tests/test_whatsapp_echoes.py`, using `tests/inbox_helpers.py` plus the T003 fixtures, signed with `META_APP_SECRET` for a coexistence tenant:
  - A text echo creates a `WhatsAppMessage` with `direction='out'`, `sender='phone'` and `status='sent'` in the conversation keyed by `to`, sets `conversation.ai_paused=True`, and triggers no auto-reply, AI run or `SupportTicket`.
  - A duplicate echo `id` is ignored.
  - An edit echo replaces the original's `text` and sets `status='edited'`.
  - A revoke echo sets the original's `status='revoked'`.
  - A `history` payload returns 200 and creates nothing.
  - An image echo stores `wa_media_id`.
- [ ] T013 [P] [US1] Create `tests/test_whatsapp_standard_templates.py`:
  - Each catalog entry's `variables` equals the parameter keys `send_whatsapp_message` sends for that event. This pins the catalog to the real send code: `payment_paid → [customer_name, amount, balance]`, `subscription_renewed → [customer_name, plan, expiry_date, balance]`, `payment_reminder`/`current_balance → [customer_name, balance, expiry_date]`, `bulk_outage → [message]`, `bulk_maintenance → [location, eta]`, `bulk_feature`/`bulk_offer → [message]`, and `payment_link` → a body with no variables plus a URL button `{pay_token}`.
  - Validation rejects a body that is missing a variable (the error names it, e.g. "Missing variable {balance}"), has an extra or unknown variable, uses a variable twice, starts or ends with a variable, or exceeds the limits "body 1024, header 60, footer 60".
  - Renumbering `"Paid {amount} by {customer_name}, balance {balance}"` gives `"Paid {{1}} by {{2}}, balance {{3}}"` with `param_map=["amount","customer_name","balance"]` and `example.body_text` built from `sample_values` in the same order.
  - `POST /api/whatsapp/standard-templates/submit` with one valid and one invalid item submits only the valid one, returns per-item results, stores `standard_key` and `param_map` on the `WhatsAppTemplate` row, and writes the name into the matching `WhatsAppSettings.template_*` field.
  - An item with `use_existing=true` makes no Meta call.
  - `send_whatsapp_message` for `payment_paid` with a template row whose `param_map=["amount","customer_name","balance"]` posts parameters in that order. With `param_map` NULL it posts the legacy order.

### Implementation for User Story 1

- [ ] T014 [US1] Implement `complete_connection(tenant, user, *, method, code, waba_id, phone_number_id, business_id, es_event)` in `wa_connect.py`. Follow research.md R-3 steps 1–8 in order, with a 10 s timeout on each Graph call:
  1. Exchange the code.
  2. Check `debug_token` ownership.
  3. Check for duplicates across *other* tenants.
  4. Fetch number details (`display_phone_number`, `verified_name`, `quality_rating`, `status`, `code_verification_status`, `platform_type`, `is_on_biz_app`). For coexistence, require `is_on_biz_app` to be true; otherwise fail with "This number is not a WhatsApp Business app number eligible for coexistence".
  5. Register, but only when `method=='embedded'` (left as a no-op hook for US2).
  6. Subscribe the app to the WABA.
  7. Fetch the WABA `name`, plus `primary_funding_id` and the messaging limit as the T002 outcome dictates, mapping to `payment_method_status` `present` / `missing` / `unknown`.
  8. Only now, in one transaction: write the credentials and display fields, set `mode='api'`, `enabled=True`, `connection_method`, `connection_status='connected'`, `status_reason=None`, NULL `app_id`/`app_secret`, and for coexistence `auto_reply_enabled=False`. Log a `connect` event, or a `reconnect`/`method_change` event when a previous connection existed.

  Raise a `ConnectError(status_code, message, meta_code)` with plain-language messages. Map Meta codes: 133016 → "Registration limit reached for this number — try again in 72 hours"; 190 → "Meta sign-in expired, please try again".
- [ ] T015 [US1] Create `wa_connect_routes.py`, a blueprint registered in `app.py` the same way `whatsapp_inbox_routes` is registered. Every route uses `@jwt_required()`, `@admin_required()` and `@require_module('whatsapp')`, returns 404 when `not wa_connect.automatic_available()` (config excepted), and returns 402 when `not plans.limits(current_tenant().plan)["whatsapp_api"]`. Routes:
  - `GET /api/whatsapp/connect/config`: returns `automatic_available`, `coexistence_available`, `app_id`, `graph_version`, `es_config_id`, `es_coex_config_id`, `coex_extras` and `plan_allows_api`. No secrets.
  - `POST /api/whatsapp/connect/complete`: calls `complete_connection` and maps `ConnectError` to its status code with `{"error","meta_code"}`. Rejects 400 when `method='coexistence'` comes with `es_event` other than `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING`, or `method='embedded'` with `es_event` other than `FINISH`.
  - `GET /api/whatsapp/connect/status`: returns `{connection, events}` with the latest 20 events, newest first.
- [ ] T016 [US1] Handle `smb_message_echoes` and `history` in the webhook POST loop in `app.py` (~9770, inside the per-change loop and after the HMAC check). When `change['field'] == 'smb_message_echoes'`, call a new `whatsapp_inbox.record_phone_echoes(tenant_id, value)` and `continue`. When the field is `'history'` or `'smb_app_state_sync'`, log at debug level and `continue`. Make sure echoes never reach the inbound-messages, auto-reply, AI or ticket code paths.
- [ ] T017 [US1] Implement `record_phone_echoes(tenant_id, value)` in `whatsapp_inbox.py`. For each item in `value['message_echoes']`:
  - **Edit**: if `edit` is present, update the message with `wa_message_id == edit['original_message_id']`, setting `text` and `status='edited'`.
  - **Revoke**: if `revoke` is present, set that message's `status='revoked'`.
  - **Otherwise**: `upsert_conversation(tenant_id, item['to'])`, then `record_outbound` with `sender='phone'`, `direction='out'`, `status='sent'`, `wa_message_id=item['id']`, `msg_type=item['type']`, the text from `text.body` and the media ID from `<type>.id`. Swallow the duplicate-`wa_message_id` `IntegrityError` the same way `record_inbound` does. Set `conversation.ai_paused=True` and refresh the conversation's last-message preview.

  Add `'phone'` to the sender comment on `WhatsAppMessage.sender` in `app.py` (~2105).
- [ ] T018 [P] [US1] Create `wa_standard_templates.py` with the catalog from data-model.md §5:
  - One entry per key: `payment_paid`, `subscription_created`, `subscription_renewed`, `payment_reminder`, `current_balance`, `payment_link`, `forward_alert`, `forward_keepalive`, `bulk_outage`, `bulk_maintenance`, `bulk_feature`, `bulk_offer`.
  - Each entry has `default_name` equal to the current `WhatsAppSettings.template_*` default, plus `settings_field`, `category`, `language='en'`, `header`/`body`/`footer`/`buttons` written with `{named}` placeholders, `variables` in legacy order with human labels, and `sample_values`.
  - Read the real variable lists for `subscription_created`, `forward_alert` and `forward_keepalive` from `send_whatsapp_message` (~8854–8901), `cs_agent_tools.escalate_to_human` (~836–870) and `send_daily_whatsapp_keepalive` (~3878) before writing those entries.
  - Implement `validate_item(key, header, body, footer, buttons) -> list[str]` with the rules in data-model.md §5.
  - Implement `to_meta_components(key, header, body, footer, buttons) -> (components, param_map)`, which renumbers placeholders by order of appearance and fills `example.body_text` and header/button examples from `sample_values`.
- [ ] T019 [US1] Add the standard-template routes in `wa_connect_routes.py`, both admin-only and requiring `settings.mode=='api'` with a token and a WABA:
  - `GET /api/whatsapp/standard-templates`: the catalog merged with the tenant's `WhatsAppTemplate` rows by name, including `existing: {status, id}` when found.
  - `POST /api/whatsapp/standard-templates/submit`: validates each item. Valid items not marked `use_existing` are created through the same code path as `create_whatsapp_template` (app.py ~8396). Extract the Meta POST and local-row creation into a reusable `_create_template_on_meta(settings, name, language, category, components)` helper in `app.py`, and keep the existing route's behavior identical. Set `standard_key` and `param_map` on the row and write the name into `getattr(settings, settings_field)`. Returns per-item results as in contracts/http-api.md.
- [ ] T020 [US1] Make `send_whatsapp_message` in `app.py` (~8854–8901) build its body parameters as a dict keyed by the semantic names from T018. Then look up the tenant's `WhatsAppTemplate` row by template name and language. If that row has `param_map`, order the values by `param_map`; otherwise use the existing legacy order, so the output stays byte-identical for tenants without a `param_map`. Apply the same ordering wherever `whatsapp_inbox.send_admin_message` calls `build_meta_template_payload` with semantic params, if it does. Do not change the reseller template paths.
- [ ] T021 [P] [US1] Create `frontend/src/components/WhatsAppConnectCard.js`:
  - **Methods**: on mount, fetch `/api/whatsapp/connect/config` and `/api/whatsapp/connect/status` through new `AppContext` methods. Render three method cards: "Use my WhatsApp Business app number (coexistence)", "New number (WhatsApp Business API)" and "Manual configuration", each with its one-line explanation. Lock the automatic cards with `LockIcon` and an upgrade message when `plan_allows_api` is false, using the Whish-tab pattern in `SettingsView.js` ~1300. Hide coexistence when `coexistence_available` is false, and hide both automatic cards when `automatic_available` is false.
  - **SDK**: load `https://connect.facebook.net/en_US/sdk.js` on first click and call `FB.init({appId, version: graph_version, xfbml:false})`.
  - **Coexistence info**: before launching coexistence, show an info dialog listing what stops working: groups, broadcast lists, disappearing messages, Windows/WearOS companions, and that the phone app must be opened at least every ~14 days.
  - **Launch**: call `FB.login(cb, {config_id, response_type:'code', override_default_response_type:true, extras:{setup:{}, ...(coex ? coex_extras : {})}})`.
  - **Collect**: add a `message` listener that accepts only origins ending in `facebook.com` and `data.type==='WA_EMBEDDED_SIGNUP'`. Collect `{event, phone_number_id, waba_id, business_id}`. On `CANCEL`, show "Connection not completed" plus `current_step`.
  - **Submit**: as soon as both the code and a success event are in hand (wait at most 10 s for the second one), POST `/api/whatsapp/connect/complete`. Show the server's `error` text on failure. Show a pop-up-blocked hint if `FB.login` never opens.
  - **Status card**: on success, show the ConnectionView fields (method, number, verified name, business name, quality, limit, payment-method status with a WhatsApp Manager link when `missing`/`unknown`, and connected-at/by).
  - **Templates prompt**: if no standard template exists yet, show a "Submit standard templates" button that opens T022's dialog.
  - **Layout**: must work at 375 px width.
- [ ] T022 [P] [US1] Create `frontend/src/components/StandardTemplatesDialog.js`:
  - Load `GET /api/whatsapp/standard-templates`. List each template with a checkbox, its name, its existing status chip, a "use existing" option when it already exists, and editable header/body/footer/button text fields.
  - Render variables as labeled chips (e.g. "Customer name") inside the text, with an "insert variable" menu limited to that template's `variables`.
  - Show live validation that mirrors the server: name any missing or extra variable, warn about a leading or trailing variable, and show length counters for "body 1024, header 60, footer 60".
  - Disable submit for invalid selected items. POST to `/api/whatsapp/standard-templates/submit` and show per-item results.
  - Must work at 375 px width.
- [ ] T023 [US1] Integrate in `frontend/src/components/SettingsView.js` on the WhatsApp tab (~921–1295). Render `<WhatsAppConnectCard>` at the top. Move the existing API-credential section (~995–1056) plus the verify-token, webhook URL and "Link WABA" block (~1200–1250) under the "Manual configuration" card, shown only when manual is selected or `connection.method==='manual'`. Leave the deeplink/api toggle, the template-name autocompletes, forwarding and auto-reply fields as they are. Add the API methods to `frontend/src/context/AppContext.js`: `getWaConnectConfig`, `getWaConnectStatus`, `completeWaConnect`, `refreshWaConnect`, `disconnectWa`, `getStandardTemplates`, `submitStandardTemplates`.
- [ ] T024 [US1] Run `python -m pytest tests/test_whatsapp_connect.py tests/test_whatsapp_echoes.py tests/test_whatsapp_standard_templates.py -q` plus the regression set `tests/test_iso_webhook.py tests/test_whatsapp_templates.py tests/test_whatsapp_inbox_*.py tests/test_reseller_whatsapp.py tests/test_whatsapp_keepalive.py`. Build the frontend (`npm run build` in `frontend/`). Then run quickstart §2 steps 3, 5 and 6 on an isolated local server: pin `cwd` and a scratch `DATABASE_URL`, and never use the real dev DB.

**Checkpoint**: coexistence works end to end. This is the MVP.

---

## Phase 4: User Story 2 — Connect a new number dedicated to the API (Priority: P2)

**Goal**: The tenant connects a fresh number through standard Embedded Signup. The platform registers it and sending works.

**Independent Test**: quickstart §2 step 4. Connect a fresh test number, then send a test message from Settings.

### Tests for User Story 2

- [ ] T025 [P] [US2] Add tests to `tests/test_whatsapp_connect.py`:
  - `method='embedded'` calls `POST /{phone_number_id}/register` with `{"messaging_product":"whatsapp","pin":<6 digits>}` and stores `registration_pin` encrypted.
  - It does not change `auto_reply_enabled`.
  - Meta error 133016 returns 502 with "Registration limit reached for this number — try again in 72 hours".
  - A number that Meta reports as already on the WhatsApp consumer app surfaces Meta's message plus the hint "try the coexistence option or a different number".
  - Reconnecting an `embedded` tenant reuses the stored PIN.

### Implementation for User Story 2

- [ ] T026 [US2] Fill in the register step in `complete_connection` in `wa_connect.py`: generate a PIN with `secrets.randbelow(10**6)`, zero-padded to 6 digits, or reuse `settings.registration_pin` if one is set. POST register, map errors, and store the PIN in `registration_pin`.
- [ ] T027 [US2] Enable the "New number" card's flow in `frontend/src/components/WhatsAppConnectCard.js` using `es_config_id`, no `featureType`, and expected event `FINISH`. After success, add a "Send test message" action that reuses the existing test-send UI or endpoint if one exists. If none exists, add a small input for a number and send the `forward_keepalive` template through the existing send path.
- [ ] T028 [US2] Run `python -m pytest tests/test_whatsapp_connect.py -q` and quickstart §2 step 4.

**Checkpoint**: both automatic methods work.

---

## Phase 5: User Story 3 — Keep manual configuration as a third option (Priority: P2)

**Goal**: Existing and new manual tenants keep exactly today's behavior, now presented as one of the three methods, and can switch methods safely.

**Independent Test**: quickstart §2 step 9. On a pre-existing manual tenant, sending, Inbox, templates and webhooks are unchanged, and the form saves as before.

### Tests for User Story 3

- [ ] T029 [P] [US3] Add regression tests to `tests/test_whatsapp_connect.py`:
  - A tenant created by `tests/inbox_helpers.setup_wa_tenant` (manual) can save credentials through `POST /api/whatsapp-settings` exactly as before: all seven credential fields persist, and the post-save `subscribed_apps` call still happens.
  - Its webhook HMAC uses its own `app_secret`.
  - `GET` still returns its own values unmasked.
  - A manual tenant that completes an automatic connect becomes `coexistence`/`embedded`, its old `app_id`/`app_secret` are cleared, and a `method_change` event is logged.
  - An automatic tenant cannot switch back to manual by POSTing credentials; it must disconnect first. The fields are ignored, and the UI explains this.

### Implementation for User Story 3

- [ ] T030 [US3] In `frontend/src/components/WhatsAppConnectCard.js`:
  - Selecting "Manual configuration" while an automatic connection is active shows a confirm dialog: "Switching to manual will disconnect the current automatic connection." On confirm, call disconnect and then reveal the manual form.
  - Selecting an automatic method while connected (manual or automatic) shows "Your current connection stays active until the new one succeeds", then launches the flow.
- [ ] T031 [US3] Run `python -m pytest tests -q -k "whatsapp or iso_webhook or reseller"` and quickstart §2 step 9 against a migrated copy of an existing manual tenant.

**Checkpoint**: all three methods are selectable, and existing tenants are unaffected.

---

## Phase 6: User Story 4 — Connection health, reconnect and disconnect (Priority: P3)

**Goal**: A broken connection becomes visible within the hour (or at the next send), admins are alerted, and Reconnect and Disconnect work.

**Independent Test**: quickstart §2 steps 7–8. Remove the app in Meta Business settings; Settings shows "Needs attention", an admin push arrives, and Reconnect restores sending. Disconnect returns the tenant to deeplink mode and keeps message history.

### Tests for User Story 4

- [ ] T032 [P] [US4] Create `tests/test_whatsapp_connection_health.py`, using the T003 fixtures signed with `META_APP_SECRET` and monkeypatching `send_push_notification`:
  - **Account events**:
    - `PARTNER_APP_UNINSTALLED` → `needs_attention`, plus an event with `source='webhook'` and a push to admins.
    - `PARTNER_REMOVED` with `PRIMARY_INACTIVITY` → `needs_attention` with the reason "WhatsApp Business app not opened for ~14 days".
    - `ACCOUNT_OFFBOARDED` → `needs_attention` with no push; then `ACCOUNT_RECONNECTED` → `connected`.
    - `ACCOUNT_RESTRICTION` → status unchanged, `status_reason` set, push sent.
    - `PARTNER_ADDED` → no change.
  - **Quality updates**: `phone_number_quality_update` updates `quality_rating` and `messaging_limit_tier`; `FLAGGED` sends a push.
  - **Routing**: `account_update` is resolved by `value.waba_info.waba_id`, falling back to `entry.id`.
  - **Send errors**: `send_whatsapp_message` getting Graph error code 190 → `needs_attention` with `source='send_error'`. While `needs_attention`, `send_whatsapp_message` makes no Graph call and returns the error "WhatsApp connection needs attention — reconnect in Settings".
  - **Health job**: `whatsapp_connection_health` with a failing `GET /{phone_number_id}` → `needs_attention`; with a succeeding one → `connected` and refreshed fields. It also pushes for any tenant left in `ACCOUNT_OFFBOARDED` for more than 15 minutes. It skips manual and disconnected tenants.
  - **Disconnect**: `POST /api/whatsapp/connect/disconnect {"confirm":true}` calls `DELETE /{waba_id}/subscribed_apps` (a failure is tolerated), never calls `/deregister`, clears `access_token`, `registration_pin` and `token_obtained_at`, sets `mode='deeplink'` and `connection_status='disconnected'`, keeps every `WhatsAppMessage`, and logs a `disconnect` event with `user_id`. Without `confirm` it returns 400.
  - **Refresh**: `POST /api/whatsapp/connect/refresh` runs the health check.
  - **Schedule sync**: the existing schedule-sync test passes with the new job.

### Implementation for User Story 4

- [ ] T033 [US4] Implement in `wa_connect.py`:
  - `handle_account_update(settings, value)`, mapping events per `specs/001-whatsapp-auto-connect/contracts/webhook-events.md` §2 through `set_connection_status`.
  - `handle_quality_update(settings, value)`, per §3.
  - `check_health(settings) -> bool`, which does a `GET /{phone_number_id}` with the stored token, updates the display fields and `last_health_check_at`, and sets `connected` or `needs_attention`.
  - `disconnect(settings, user)`, per contracts/http-api.md. It works for manual tenants too (clears `access_token` and `app_secret`).
  - `is_auth_error(meta_error) -> bool`: true for code 190, or 200/10 permission errors.
- [ ] T034 [US4] Add webhook dispatch in `app.py` for `account_update` (tenant resolved by `value.get('waba_info',{}).get('waba_id') or entry['id']` against `business_account_id`, HMAC-verified via `wa_app_identity`) and for `phone_number_quality_update` (resolved by `entry['id']`). Neither field may fall into the messages path.
- [ ] T035 [US4] Hook send errors in two places: `send_whatsapp_message` in `app.py` (~8778) and `whatsapp_inbox._post_message` in `whatsapp_inbox.py` (~466).
  - **Before the Graph call**: if `settings.connection_method != 'manual'` and `settings.connection_status == 'needs_attention'`, return a failure with "WhatsApp connection needs attention — reconnect in Settings" and make no call.
  - **After the call**: if the response error satisfies `wa_connect.is_auth_error`, call `set_connection_status(settings, 'needs_attention', <meta message>, 'send_error')`.
  - Manual tenants: only the post-call detection applies, with no pre-call block.
- [ ] T036 [US4] Add the scheduled job `whatsapp_connection_health`:
  - Write the function `whatsapp_connection_health_with_context()` in `app.py`, next to `send_daily_whatsapp_keepalive_with_context` (~3943). It loops over `WhatsAppSettings` with `connection_method in ('coexistence','embedded')` and `connection_status != 'disconnected'` and calls `wa_connect.check_health`.
  - Register it in `_SCHEDULED_JOBS` (~4287) and in `SCHEDULE` (~4314) as hourly.
  - Add the matching hourly cron line to `.github/workflows/scheduled-jobs.yml`, so that the existing schedule-sync test passes.
- [ ] T037 [US4] Add routes to `wa_connect_routes.py`: `POST /api/whatsapp/connect/refresh` (calls `check_health`, returns `{connection}`) and `POST /api/whatsapp/connect/disconnect` (requires `{"confirm": true}`, calls `disconnect`).
- [ ] T038 [P] [US4] UI in `frontend/src/components/WhatsAppConnectCard.js`:
  - A status chip: Connected (green), Needs attention (amber, showing `status_reason`), Disconnected.
  - A "Refresh" button.
  - A "Reconnect" button that re-runs the same method's flow.
  - A "Disconnect" button with a confirm dialog saying that automatic sending stops and history is kept.
  - A collapsible "Connection history" list of the last 20 events: time, event, by-user or source, detail.
  - Also show a dismissible amber banner in the Inbox header (`frontend/src/components/` inbox view; find the component that renders the Inbox list) when `connection.status === 'needs_attention'`, with a link to Settings > Messaging.
- [ ] T039 [US4] Run `python -m pytest tests/test_whatsapp_connection_health.py tests -q -k "whatsapp or schedule"` and quickstart §2 steps 7–8.

**Checkpoint**: all user stories work independently.

---

## Phase 7: Polish & cross-cutting concerns

- [ ] T040 [P] Write the owner-facing setup doc `docs/whatsapp-auto-connect-setup.md`. It covers the Meta app and Tech Provider steps, the env vars (contracts/http-api.md table), the webhook callback URL and verify token, the fields to subscribe (`messages`, `message_template_status_update`, `account_update`, `phone_number_quality_update`, `smb_message_echoes`; deliberately not `history` or `smb_app_state_sync`), adding domains to the Login config, and the `META_COEXISTENCE_ENABLED` kill switch.
- [ ] T041 [P] Security pass on the diff:
  - No token, secret or PIN in any JSON response, log line or event `detail`. Grep the new code for `access_token`, `app_secret` and `registration_pin` inside `logging.` / `jsonify` calls.
  - The postMessage origin check is in place.
  - `debug_token` ownership is enforced before any write.
  - All `/connect/*` and standard-template routes are admin-only.
- [ ] T042 Run the full suite (`python -m pytest tests -q`) and the frontend build. Then do a final pass of quickstart §2 on an isolated server. Fix any regressions.
- [ ] T043 Before merging, run a read-only production check through the Render Postgres tool: `SELECT business_account_id, count(*) FROM whats_app_settings WHERE business_account_id IS NOT NULL GROUP BY 1 HAVING count(*)>1`. Record the result in `specs/001-whatsapp-auto-connect/research.md`. If it returns zero rows, note the follow-up of adding unique indexes. Do not add them in this feature.
- [ ] T045 Owner + Claude: after US1 works on DeltaNet's own number, record the App Review screencast for `whatsapp_business_messaging` on app `8757534751003383`. The app UI must be in English, with captions. The recording must show, in order:
  1. The complete Meta login and permission grant, done through the ServiceBills "Connect" button.
  2. The WhatsApp number being selected.
  3. A live send from the ServiceBills UI (a test message, or a payment confirmation).
  4. The same message arriving in the WhatsApp client on a phone.
  5. A message template being created through "Submit standard templates".

  Resubmit with notes describing the Tech Provider use case. This fixes the 2026-02-24 rejection (research.md R-0). Outside tenants can't connect until it is approved.
- [ ] T044 Open a PR from `feat/whatsapp-auto-connect` to `main`. The description covers the three methods, the env vars that stay unset until Meta review passes (so the automatic methods remain hidden), the migration ID, and quickstart §3 rollout steps. After merge and deploy, run the quickstart §3 production checks.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: T001 is an owner action that runs in parallel with everything; only live testing waits for it. T002 needs T001's app. T003 needs T002. T004 has no dependencies.
- **Foundational (Phase 2)**: depends on T004 and blocks every story. T005 → T006 → T007 → T008 → T009 → T010.
- **US1 (Phase 3)**: depends on Phase 2. Its live verification (T024) depends on T001/T002. Its tests can use the synthetic fixtures from T003.
- **US2 (Phase 4)**: depends on US1's T014/T015/T021, since it extends the same onboarding function, routes and card.
- **US3 (Phase 5)**: depends on Phase 2. T030 depends on T021 and on T037 (disconnect), so run it after US4's T037 or stub the disconnect call.
- **US4 (Phase 6)**: depends on Phase 2 and T015 (the routes file). Otherwise independent of US1's UI.
- **Polish (Phase 7)**: after the chosen stories.

### Within each story

Tests first (they must fail) → `wa_connect.py` service functions → routes and webhook dispatch → `app.py` hooks → frontend → verification task.

### Parallel opportunities

- T003 ∥ T004
- US1: T011 ∥ T012 ∥ T013 (separate test files); T018 ∥ T021 ∥ T022 (separate new files)
- US4's T032 can be written in parallel with US1 implementation, and T038 with T033–T037
- T040 ∥ T041

## Parallel Example: User Story 1

```text
# Tests together (separate files):
T011 tests/test_whatsapp_connect.py (connect flow)
T012 tests/test_whatsapp_echoes.py
T013 tests/test_whatsapp_standard_templates.py

# New modules together:
T018 wa_standard_templates.py
T021 frontend/src/components/WhatsAppConnectCard.js
T022 frontend/src/components/StandardTemplatesDialog.js
```

## Implementation Strategy

### MVP first (User Story 1)

1. Start T001 (Meta verification and review) **today**; it gates live use, not coding.
2. Phase 1 → Phase 2. This can be merged and deployed on its own: it is invisible, and manual tenants are unchanged.
3. Phase 3 (US1). Ship with the `META_*` env vars unset in production, so it stays hidden. Test it live in development mode with the app admins' own accounts.
4. Stop and validate with DeltaNet's own number (quickstart §2).

### Incremental delivery

Foundation → US1 coexistence + templates (MVP) → US2 new number → US3 switching polish → US4 health. Each is its own PR, following the repo's PR-per-slice habit. Set the env vars in production only after Meta App Review passes.

## Notes

- After each implementation task, run the matching tests. A task is done only when its tests pass and the build is green.
- Never write to the user's real dev DB during manual verification (pin `cwd` and `DATABASE_URL`).
- Commit after each task or logical group, and do not push to `main` directly.
