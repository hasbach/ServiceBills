# Phase 0 Research: Automatic WhatsApp Connection

**Date**: 2026-10-20 · **Spec**: [spec.md](spec.md)

Meta docs have moved to `developers.facebook.com/documentation/business-messaging/whatsapp/...` (old `/docs/whatsapp/...` URLs still resolve). Each item below is a decision, why we made it, and the alternatives considered. Items marked **VERIFY** must be confirmed against a real Meta test account during the first implementation slice (see [quickstart.md](quickstart.md) §0). They are not blockers for planning.

---

## R-0 Platform Meta app (checked 2026-10-20)

- **App**: "beta business response", App ID `8757534751003383`, Live mode, owned by the business portfolio **Beta consulting Agency** (`556476731588202`). Salloum Services is not a registered entity, so it can't pass business verification; the owner chose Beta.
- **Business verification**: Approved. **Tech Provider onboarding**: complete.
- **App Review** (submitted 2026-02-24):
  - `whatsapp_business_management`: **Approved**.
  - `whatsapp_business_messaging`: **Rejected**. The screencast didn't show the full Meta login and permission grant, asset/number selection, a live send from the app UI, and the message arriving in the WhatsApp client. Meta confirmed that the use case itself is allowed. **Action**: after US1 is implemented, re-record the ServiceBills connect-and-send flow and resubmit (task T045).
- **Webhook callback URL**: empty, so ServiceBills can use the app-level callback directly. No per-WABA override is needed.
- **Facebook Login for Business**: present on the app. A dedicated ServiceBills Embedded Signup configuration still has to be created.
- **Before approval**: Live-mode apps can use permissions that aren't approved yet on assets owned by the app's own admins and business. DeltaNet's own number can therefore be connected and tested end to end now. Outside tenants have to wait for the messaging approval.
- **Consent-screen branding**: tenants will see "Beta consulting Agency" as the business behind the app. The privacy policy must name Beta as the data processor.

## R-1 Platform role with Meta: Tech Provider

- **Decision**: Register DeltaNet's platform Meta app as a **Tech Provider**. Each tenant pays Meta directly with a card on their own WhatsApp Business Account (WABA).
- **Rationale**: Tech Provider is the self-serve route. Credit-line sharing (where the platform pays) exists only for Solution Partners, and the spec already assumes tenant-side billing.
- **Prerequisites** (outside the code; the owner does these in Meta):
  1. Business verification of DeltaNet's Meta business portfolio. This must happen before app review.
  2. App Review. Both `whatsapp_business_management` and `whatsapp_business_messaging` need **Advanced access**. Review requires two screen recordings: sending a message, and creating a template.
  3. A Facebook Login for Business **configuration** of type "WhatsApp Embedded Signup", which gives a `config_id`. Every host (production domain and dev/staging) must be listed under Allowed domains and Valid OAuth redirect URIs, HTTPS only.
  4. The webhook callback URL and verify token set on the platform app, with these fields subscribed: `messages`, `message_template_status_update`, `account_update`, `phone_number_quality_update`, `smb_message_echoes`. Deliberately **not** `history` or `smb_app_state_sync` (see R-6).
- **Timeline**: not documented by Meta. Treat it as the critical path and start it now, in parallel with development.
- **Alternatives**: Solution Partner (heavier commitment, credit lines; not needed). Partnering with an existing Solution Partner's app (adds a dependency and a revenue share).
- Sources: `.../solution-providers/get-started-for-tech-providers`, `.../solution-providers/share-and-revoke-credit-lines.md`

## R-2 Embedded Signup version and frontend flow

- **Decision**: Use **Embedded Signup v4**. v2 and v3 were deprecated on 2026-10-15, and any other version request is now served as v4.
  - Load the FB JS SDK only on the Settings > Messaging page, when one of the automatic buttons is clicked.
  - Call `FB.login(cb, {config_id, response_type:'code', override_default_response_type:true, extras:{setup:{}}})`.
  - Listen to `window.message` events whose origin ends with `facebook.com` and whose `type === 'WA_EMBEDDED_SIGNUP'`.
  - Success events: `FINISH` (new number) and `FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING` (coexistence). Both carry `phone_number_id`, `waba_id` and `business_id`.
  - `CANCEL` carries `current_step` or error details.
- **The code expires 30 seconds after it is issued**, so the frontend posts it to the backend at once, together with the ids from the postMessage. Whichever of the two (callback or postMessage) arrives first waits up to ~10 s for the other.
- **VERIFY, coexistence on v4**: the docs disagree. v4 says `extras` must be empty and that features come from the Login configuration, but the coexistence page still shows `extras:{setup:{}, featureType:'whatsapp_business_app_onboarding', sessionInfoVersion:'3'}`.
  - Plan: support **two config ids**, `META_ES_CONFIG_ID` and `META_ES_COEX_CONFIG_ID` (the second falls back to the first), and send `featureType` only for coexistence.
  - Confirm in quickstart §0 which combination shows the "connect existing WhatsApp Business account" screen.
- Sources: `.../embedded-signup/versions.md`, `.../embedded-signup/implementation.md`, `.../embedded-signup/onboarding-business-app-users.md`

## R-3 Backend onboarding sequence

- **Decision**: one synchronous endpoint, `POST /api/whatsapp/connect/complete`, does the following in order and returns the resulting connection:
  1. **Exchange the code**: `GET /oauth/access_token?client_id&client_secret&code` (no `redirect_uri`). This returns a business integration system-user token for that one customer.
  2. **Prove ownership**: call `GET /debug_token`, and check that the `granular_scopes[].target_ids` for `whatsapp_business_management` include the `waba_id` the client claimed. Reject otherwise. This stops a tenant from claiming another business's WABA id from a doctored postMessage (it covers spec FR-009 and SC-006).
  3. **Check for duplicates**: no other tenant may hold this `phone_number_id` or `business_account_id`.
  4. **Fetch the number**: `GET /{phone_number_id}?fields=display_phone_number,verified_name,quality_rating,status,code_verification_status,platform_type,is_on_biz_app`. For coexistence, check `is_on_biz_app == true`.
  5. **Register** (new-number method only): `POST /{phone_number_id}/register {messaging_product, pin}`, with a random 6-digit PIN that is stored encrypted. This is **skipped for coexistence**, where it is forbidden because the number is already registered. Rate limit is 10 per 72 h (error 133016), shown in plain language.
  6. **Subscribe to webhooks**: `POST /{waba_id}/subscribed_apps`.
  7. **Read the account**: `GET /{waba_id}?fields=name,primary_funding_id` and the business portfolio's `whatsapp_business_manager_messaging_limit`, for display. **VERIFY**: `primary_funding_id` is documented only for credit lines. If it doesn't reflect a card, show "Payment method: check in WhatsApp Manager" with a link instead of a definite yes/no.
  8. **Save**: write everything in a single DB transaction. Credentials are swapped in only now, so a failure at any earlier step leaves the old configuration untouched (FR-008, FR-021).
- **Token lifetime**: undocumented. The Meta template name suggests 60 days, and forum reports say tokens are invalidated after account changes. So we store `token_obtained_at`, treat Graph error 190 or `PARTNER_APP_UNINSTALLED` as "Needs attention", and offer Reconnect. There is no silent refresh flow, because none is documented.
- **Alternatives**: running onboarding as a background job. Rejected: total latency is a few seconds, and the user is waiting on the dialog anyway.
- Sources: `.../embedded-signup/onboarding-customers-as-a-tech-provider`, `.../business-phone-numbers/registration.md`, `.../access-tokens.md`, `.../messaging-limits.md`

## R-4 Credentials model: per-tenant token, platform app identity

- **Decision**: keep using the existing `WhatsAppSettings` columns for the per-tenant values (`access_token` holds the business token; `phone_number_id`, `business_account_id`). For automatic methods, leave `app_id` and `app_secret` **NULL** and resolve them from platform config.
  - Add one resolver, `wa_app_identity(settings) -> (app_id, app_secret)`. It returns the tenant's own values for `connection_method == 'manual'` and `Config.META_APP_ID/SECRET` otherwise.
  - Only 3 code paths read `app_id` or `app_secret`: the webhook signature check, `upload_whatsapp_template_sample`, and the template-status webhook. Those are switched to the resolver.
  - The ~15 send call sites keep reading `settings.access_token` / `phone_number_id` unchanged.
- **Rationale**: smallest blast radius. Existing tenants (all manual) keep identical behavior (FR-017, FR-018, SC-004).
- **Alternatives**: a new `WhatsAppConnection` table holding credentials, with every call site refactored to a central Graph client. That is cleaner, but it touches ~15 call sites across 3 modules for no user-visible gain. Deferred as tech debt.

## R-5 Webhook routing and signatures in a shared-app world

- **Decision**: keep the single endpoint `/api/whatsapp/webhook`.
  - **GET verify**: also accept `Config.META_WEBHOOK_VERIFY_TOKEN`.
  - **POST signature**: the tenant is resolved first, as today (by `metadata.phone_number_id`, or by WABA id for template status and `account_update`). The HMAC key is then `wa_app_identity(tenant_settings).app_secret`.
  - **Unknown phone/WABA ids**: still skipped, as today. For `account_update` with no tenant match, verify against the platform secret and log.
- **Routing**: for `messages` and `smb_message_echoes`, route by `value.metadata.phone_number_id`. For `account_update`, prefer `value.waba_info.waba_id` and fall back to `entry.id`; the docs are inconsistent on whether `entry.id` is the WABA or the business portfolio.
- **Retries and duplicates**: Meta retries for up to 7 days and can send duplicates. Inbound and echo messages are deduped on `wa_message_id`; the existing unique index covers this.
- Sources: `.../webhooks/overview.md`, `.../solution-providers/manage-webhooks.md`, `.../webhooks/create-webhook-endpoint`

## R-6 Coexistence specifics

- **Eligibility**: WhatsApp Business app version 2.24.17 or later. **VERIFY, Lebanon (+961)**: no official country list exists and vendor lists conflict. The first real test is DeltaNet's own number (quickstart §0). If Meta refuses it, the coexistence button is hidden by a platform flag (`META_COEXISTENCE_ENABLED`), and the other two methods are unaffected.
- **History (FR-014 = no import)**: history sync is a pull that the partner starts, and must be started within 24 h of onboarding. We **never call it**, and we do not subscribe to the `history` or `smb_app_state_sync` fields. If a `history` payload arrives anyway, we acknowledge it with 200 and ignore it.
- **Phone-sent messages (FR-012)**: handle `smb_message_echoes`.
  - Each `message_echoes[]` item becomes an outbound `WhatsAppMessage` with `direction='out'`, `sender='phone'`, `wa_message_id=id`, linked to the conversation keyed by `to`.
  - It does **not** trigger auto-reply, the AI agent, or tickets.
  - It pauses the AI on that conversation (`ai_paused=True`), because a human has taken over.
  - Edit and revoke echoes update or mark the original message.
- **Auto-reply default (FR-013)**: on a successful coexistence connect, set `auto_reply_enabled = False`. That flag also gates the AI agent today. The tenant can turn it back on.
- **Throughput**: fixed at 20 messages/second, well above our bulk-send rate.
- **Lost features**: groups, broadcast lists, disappearing messages, and Windows/WearOS companions. These are shown as an info note before the tenant starts the flow.
- **Disconnect signals**: `account_update` with `PARTNER_REMOVED` (`disconnection_info.reason` is `PRIMARY_INACTIVITY` after about 14 days with the phone app unused, or `ACCOUNT_DISCONNECTED`, etc.), and `ACCOUNT_OFFBOARDED` then `ACCOUNT_RECONNECTED`. See R-7.
- Sources: `.../embedded-signup/onboarding-business-app-users.md`, `.../webhooks/reference/smb_message_echoes.md`, `.../embedded-signup/reconnect-offboarded-coexistence-clients.md`

## R-7 Health detection (FR-019, FR-020, SC-005)

- **Decision**: three signals feed one function, `set_connection_status(settings, status, reason, source)`, which also writes a `WhatsAppConnectionEvent` and pushes an alert to tenant admins.
  1. **Webhook `account_update`**:
     - `PARTNER_APP_UNINSTALLED`, `PARTNER_REMOVED`, `ACCOUNT_DELETED`, `DISABLED_UPDATE`, `ACCOUNT_OFFBOARDED` → `needs_attention`
     - `ACCOUNT_RECONNECTED` → `connected`
     - `phone_number_quality_update` → refresh the quality and limit display only.
  2. **Send failures**: Graph error 190 (invalid token) or 200/10 (permission) inside `send_whatsapp_message` / `whatsapp_inbox._post_message` → `needs_attention`.
  3. **Scheduled job** `whatsapp_connection_health` (hourly): `GET /{phone_number_id}` for every automatic-method tenant; on failure → `needs_attention`. It is registered in `_SCHEDULED_JOBS` + `SCHEDULE` + the `.github/workflows/scheduled-jobs.yml` cron (a test enforces that these stay in sync).
  - The Settings page also runs the check on demand ("Refresh" button) when it opens.
- **While `needs_attention`**: automatic sends are not attempted. The caller gets a clear error ("WhatsApp connection needs attention — reconnect in Settings"), which keeps the failure visible instead of silent.
- **Alert channel**: the existing web-push `send_push_notification(..., roles=admin, topic='whatsapp_inbox')`, plus a persistent banner on Settings > Messaging and the Inbox header. No new push topic is needed.
- **Alternatives**: a classic FB "Deauthorize callback". The WhatsApp docs don't use it, so we rely on `PARTNER_APP_UNINSTALLED`.

## R-8 Standard templates with editable text (FR-015 to FR-015d)

- **Decision**: add a code-defined catalog, `wa_standard_templates.py`. One entry per standard template: `key`, default `name`, `category`, `language`, `body`/`header`/`footer`/`buttons` text written with **named placeholders** (`{customer_name}`), and `variables`, the ordered list of semantic keys that `send_whatsapp_message` already sends for that event.
  - The ordered lists are taken from today's hard-coded mapping (`app.py` ~8854-8901): for example `payment_paid → [customer_name, amount, balance]`.
- **Editor flow**: the tenant edits text with named chips. On submit, the server:
  1. checks that the set of placeholders in the text **equals** the template's `variables` set (FR-015b; an error names any missing or extra variable);
  2. **renumbers** the placeholders to `{{1}}…{{n}}` in order of appearance;
  3. stores `param_map` (the semantic key for each position) on the `WhatsAppTemplate` row;
  4. builds `example.body_text` from built-in sample values;
  5. posts to `POST /{waba_id}/message_templates`, reusing the existing create path and `_validate_template_components`.
- **Send side**: `send_whatsapp_message` builds its parameters as a dict of semantic key → value. If the template row has a `param_map`, it orders the parameters by that map; otherwise it uses today's fixed order. Existing tenants are therefore unchanged.
- **Why renumber instead of keeping a fixed order**: Meta requires positional parameters to be sequential, and **VERIFY** whether they must appear in ascending order in the text. Renumbering by order of appearance satisfies either rule, and lets tenants move variables around freely (FR-015b).
- **Alternatives**:
  - Meta's NAMED parameter format (`parameter_format: NAMED`). It would avoid `param_map`, but the whole send path (`build_meta_template_payload`, the inbox template send, and the keep-alive retries) would need to switch to `parameter_name`, and that is riskier for existing tenants.
  - Fixed order with only the surrounding text editable. Simpler, but it fails the user's requirement that variables can be repositioned.
- **Existing templates with the same name** (edge case): before submitting, check the synced templates. If the name exists, show its status and offer "use existing" or "edit".
- **Reseller templates** (4 hard-coded names) are outside this catalog (spec: reseller WhatsApp is out of scope).

## R-9 Secrets on the settings endpoint

- **Finding**: `GET /api/whatsapp-settings` currently returns `access_token` and `app_secret` in plaintext. `POST` writes back whatever it receives.
- **Decision**: for automatic methods, `to_dict` returns no token at all (it is never editable), and `POST` **ignores** credential fields unless `connection_method == 'manual'`. Today's manual behavior is unchanged, to keep the scope tight. Masking the manual secrets is noted as a separate hardening follow-up.

## R-10 On-prem (ServiceBills) and plan gating

- **Decision**: the automatic methods are shown only when `Config.META_APP_ID` and `META_ES_CONFIG_ID` are set **and** the instance is not on-prem. Embedded Signup needs a public HTTPS domain that is allow-listed in the platform Login configuration. On-prem keeps manual configuration only.
- **Plan gating**: the same check as `plans.limits(plan)["whatsapp_api"]` (FR-002), enforced server-side on `/connect/*` and shown as a lock in the UI. This copies the Whish-tab `LockIcon` pattern, since there is no frontend lock on API mode today.

## R-11 Permissions

- **Decision**: connect, disconnect, refresh and template submit use `@jwt_required` + `@admin_required()` + `@require_module('whatsapp')`. That is stricter than settings GET/POST (admin or finance), because connecting changes who controls the number (FR-003).
- **Side finding**: `POST /api/whatsapp/subscribe-waba` (app.py ~8281) lacks an admin decorator. This will be fixed in passing, since that route is touched anyway.
