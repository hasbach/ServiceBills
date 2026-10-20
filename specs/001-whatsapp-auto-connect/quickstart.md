# Quickstart / Validation Guide: Automatic WhatsApp Connection

This guide proves the feature works end to end. Contracts are in [contracts/](contracts/) and entities in [data-model.md](data-model.md).

## 0. Meta-side spike: do this first, before building the UI

Several open questions (research items marked VERIFY) can only be answered against Meta. Run this spike once the platform app exists, even in development mode, since an app in development mode works for its own admins' accounts.

1. **Platform app setup.** Create the platform app with the WhatsApp use case, start Tech Provider onboarding, and create the Login for Business configuration ("WhatsApp Embedded Signup"). Add the dev HTTPS host (a tunnel such as ngrok or cloudflared) to Allowed domains.
2. **Throwaway page.** Build a minimal HTML page with the FB SDK and the postMessage listener, then:
   - **Coexistence:** launch with the v4 config plus `featureType:'whatsapp_business_app_onboarding'`. Confirm whether the "connect existing WhatsApp Business account" screen appears. If it doesn't, try the Login config's product/feature settings. Record which combination works (R-2).
   - **Lebanon eligibility:** use **DeltaNet's own +961 Business-app number** (a test number is better if one exists). Confirm Meta accepts it for coexistence (R-6).
3. **Code exchange and ownership.** Exchange the code with curl, call `debug_token`, and confirm that `granular_scopes.target_ids` contains the WABA id (R-3).
4. **Payment method.** Run `GET /{waba_id}?fields=primary_funding_id` on a WABA with a card and on one without. Decide between the "present/missing" and "unknown + link" display (R-3, step 7).
5. **Variable order.** Submit a test template whose body has `{{2}}` before `{{1}}`, and record whether Meta accepts it. This only tells us whether renumbering is required or just a nice-to-have; the design works either way (R-8).
6. **Payload samples.** Send a message from the phone app and capture the `smb_message_echoes` payload into `tests/fixtures/`.

Write the outcomes back into research.md, then start implementation.

## 1. Local automated checks

```bash
python -m pytest tests/test_whatsapp_connect.py tests/test_whatsapp_echoes.py tests/test_whatsapp_standard_templates.py tests/test_whatsapp_connection_health.py -q
```

```bash
python -m pytest tests -q
```

Expected results:

- **Connect:**
  - The connect endpoint rejects a WABA that isn't in `debug_token`'s target_ids (403) and a duplicate number (409).
  - When any Meta call fails, the previous settings stay byte-for-byte unchanged.
  - Coexistence never calls `/register`, while the embedded method does.
- **Webhooks:**
  - A webhook signed with the platform secret is accepted for an automatic tenant and rejected for a manual tenant (and the reverse).
  - Echo payloads create `sender='phone'` messages, trigger no auto-reply, and pause the AI.
  - A `history` payload is ignored.
- **Standard templates:** a template missing a variable is blocked with that variable named; reordered variables are renumbered and `param_map` is stored; the send path orders parameters by `param_map`.
- **Health:** `PARTNER_APP_UNINSTALLED` moves the tenant to `needs_attention`, writes an event, and pushes an alert.
- **Scheduled jobs:** the schedule-sync test still passes with the new job in `_SCHEDULED_JOBS`, `SCHEDULE` and the workflow cron.
- **Regression:** existing WhatsApp tests (`test_iso_webhook`, `test_whatsapp_templates`, `test_whatsapp_inbox_*`, `test_reseller_whatsapp`) pass unchanged.

## 2. Local isolated dev server

Follow the memory note on local test-server isolation: pin `cwd` and a scratch `DATABASE_URL` so the user's real dev database is never written.

1. Run `flask db upgrade` on the scratch DB, then confirm that existing settings rows show `connection_method='manual'`.
2. Set the `META_*` env vars from the spike app and expose the server through the HTTPS tunnel.
3. Settings > Messaging shows three method cards:
   - On a free plan, the automatic cards are locked and show an upgrade prompt.
   - With `META_APP_ID` unset, only Manual appears.
4. **Embedded (US2):** connect a test number. The status card shows the number, name, quality and limit, and "Send test message" works.
5. **Coexistence (US1):**
   - Connect the phone-app number. The status shows "WhatsApp Business app (coexistence)", and auto-reply is now off.
   - Send from the phone app; within seconds the message appears in the Inbox marked "sent from phone".
   - Send a customer message; it shows up in both the phone app and the Inbox.
6. **Standard templates (US1/2 follow-up):**
   - Open "Submit standard templates" and edit the payment confirmation, moving `{amount}` before `{customer_name}`. Delete `{balance}` from another template: submit is blocked for that one only.
   - Submit, and the statuses show PENDING. After Meta approves, record a payment and confirm the customer message has the right values in the right places.
7. **Health (US4):** remove the app in Meta Business Settings > Integrations. Within seconds (webhook), or at the latest within the hour (job), Settings shows "Needs attention", an admin push arrives, and sends fail with a visible reason. Click **Reconnect**, re-run the flow, and the status returns to Connected.
8. **Disconnect:** the tenant goes back to deeplink mode, history stays in the Inbox, and the event log shows who did it.
9. **Manual (US3):** on an existing manually configured tenant, nothing changes. The credential form and saving behave as before.

## 3. Production rollout checks

- Add the `META_*` env vars on Render. Set the app's webhook callback to `https://<prod>/api/whatsapp/webhook` with `META_WEBHOOK_VERIFY_TOKEN`, and subscribe the fields listed in research R-1.
- After deploy, run a read-only production query: `SELECT connection_method, connection_status, count(*) FROM whats_app_settings GROUP BY 1,2`. Every pre-existing row should be manual.
- Confirm that existing tenants' automatic messages keep sending (SC-004): use the Render logs and a real payment.
- The automatic methods stay hidden until the Meta app passes review. Until then, only the app admins' test accounts can use them in development mode.
