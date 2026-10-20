# WhatsApp automatic connection: platform setup

This is the owner-facing setup for letting tenants connect WhatsApp through Meta Embedded Signup. They can either keep using their WhatsApp Business app number ("coexistence") or connect a new number, alongside the existing manual configuration.

Design and decisions: [specs/001-whatsapp-auto-connect/](../specs/001-whatsapp-auto-connect/), in particular research.md R-0.

Until the env vars below are set, the automatic options stay hidden and only manual configuration is offered. On-prem installs always use manual configuration only.

## 1. The platform Meta app (done 2026-10-20)

- **App:** "beta business response", App ID `8757534751003383`.
- **Business portfolio:** Beta consulting Agency. It is verified and is a Tech Provider.
- **Facebook Login for Business configuration:** "ServiceBills WA Signup", ID `1455508473309505`.
  - WhatsApp Embedded Signup.
  - WhatsApp Cloud API only.
  - Token never expires.
  - Permissions: `whatsapp_business_management` and `whatsapp_business_messaging`.
- **Login settings:**
  - JavaScript SDK login is on.
  - Allowed domains and redirect URIs: `https://servicebills.salloumservices.com` and `https://servicebills.onrender.com`.
  - For local testing, add the dev tunnel's HTTPS domain to both lists.

Still to do before outside tenants can connect:

- **App settings > Basic:** fill in **App domains** and **Terms of Service URL**. The privacy policy must name Beta consulting Agency as the data processor. Consider renaming the display name to something tenants recognise, since it shows on the Meta consent screen.
- **App Review:** `whatsapp_business_messaging` was rejected on 2026-02-24 because the screencast was incomplete. Re-record it with ServiceBills (task T045 in tasks.md). The recording must show, in order:
  1. The Meta login and the permission grant.
  2. Selecting the number.
  3. A send from the ServiceBills UI.
  4. The message arriving in WhatsApp on a phone.
  5. Template creation.

  Until approved, only the app's own admins and business can connect, which is enough for testing with DeltaNet's number.

## 2. Environment variables (Render: servicebills-web > Environment)

| Variable | Value |
|---|---|
| `META_APP_ID` | `8757534751003383` |
| `META_APP_SECRET` | App settings > Basic > App secret (enter it yourself; never paste it in chat) |
| `META_ES_CONFIG_ID` | `1455508473309505` |
| `META_ES_COEX_CONFIG_ID` | optional; only if coexistence needs its own Login configuration (see T002) |
| `META_COEXISTENCE_ENABLED` | `true` (set `false` to hide coexistence, e.g. if +961 numbers turn out ineligible) |
| `META_WEBHOOK_VERIFY_TOKEN` | a long random string you choose; must match step 3 |
| `META_GRAPH_VERSION` | optional, default `v24.0` |

## 3. Webhook (after the code is deployed)

In the app: **Use cases > Connect on WhatsApp > Customize > Configuration > Webhook**.

- **Callback URL:** `https://servicebills.onrender.com/api/whatsapp/webhook`, or the salloumservices domain.
- **Verify token:** the same value as `META_WEBHOOK_VERIFY_TOKEN`. Meta calls the URL when you save, so deploy first.
- **Subscribe these fields:**
  - `messages`
  - `message_template_status_update`
  - `account_update`
  - `phone_number_quality_update`
  - `smb_message_echoes`
- **Do not subscribe** `history` or `smb_app_state_sync`. Chat history is deliberately never imported. The app also ignores them if they arrive.

## 4. Scheduled job

`whatsapp_connection_health` runs hourly (`5 * * * *`) through `.github/workflows/scheduled-jobs.yml`. No extra setup is needed beyond the existing `CRON_TRIGGER_SECRET`.

## 5. Before turning it on in production

Check for WhatsApp accounts (WABAs) shared by more than one tenant (task T043; read-only). The duplicate guard only blocks *other* tenants, but this decides whether a unique index can be added later:

```sql
SELECT business_account_id, count(*) FROM whats_app_settings
WHERE business_account_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1;
```

After deploy, every pre-existing row should show `connection_method = 'manual'`:

```sql
SELECT connection_method, connection_status, count(*) FROM whats_app_settings GROUP BY 1, 2;
```
