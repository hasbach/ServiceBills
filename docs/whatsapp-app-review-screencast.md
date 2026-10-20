# App Review resubmission: `whatsapp_business_messaging`

**App:** "beta business response" (`8757534751003383`), owned by Beta consulting Agency (verified Tech Provider).

**Previous result (2026-02-24):** `whatsapp_business_management` was approved and `whatsapp_business_messaging` was rejected with "Screencast Not Aligned with Use Case Details". The reviewer asked to see three things: (1) the asset selection, with the number visible; (2) a live send from the app's own UI; (3) the delivered message in the native WhatsApp client.

**Why this resubmission matters:** Embedded Signup refuses to onboard any number until both permissions have Advanced access (error #2655111 seen on 2026-10-20). Until approval, tenants can't connect WhatsApp automatically.

## Setup for the recordings (done once)

- **ServiceBills tenant:** WA Test (`wa-test`), Pro plan, **Manual configuration** with the app's own credentials:
  - App ID: `8757534751003383`
  - Phone Number ID: `919682917904290`, which is **+961 71 228 998** on the "Beta Marketing Solutions" WhatsApp account. That account has a payment method and is subscribed to the app's webhook.
  - WABA ID: `1528337918265430`
  - Token: a system-user token from Beta consulting Agency, with `business_management`, `whatsapp_business_messaging` and `whatsapp_business_management`.
- **Recording setup:**
  - Browser UI language: **English**.
  - Recorder: a screen recorder with captions (OBS, or the Windows Snipping Tool screen recording plus captions added in an editor). Use 1080p.
  - Have a phone with WhatsApp next to the computer and mirror its screen with **Phone Link** or **scrcpy**. The delivery has to be visible in the same video.
- **Before recording:** close unrelated tabs and hide any tokens or secrets. Never show the App Secret or the token on screen.

## Video 1: send a message from ServiceBills and show it arriving (≈ 2 minutes)

| # | On screen | Caption to add |
|---|---|---|
| 1 | Open `https://servicebills.onrender.com` and log in as the WA Test admin | "ServiceBills: billing platform for internet service providers. We are a Tech Provider sending WhatsApp messages on behalf of our business customers." |
| 2 | Settings → WhatsApp Notifications. Show the connection panel: **Connected**, number **+961 71 228 998**, and the WhatsApp Business Account | "Asset selection: the customer's WhatsApp Business number connected to our app." |
| 3 | Click **Send test message**, type the phone's number, click **Send**. Show the success message | "Live send from our app UI using the WhatsApp Cloud API (whatsapp_business_messaging)." |
| 4 | Switch to the mirrored phone: the message from +961 71 228 998 arrives in WhatsApp | "Message delivered in the native WhatsApp client." |
| 5 | Reply from the phone. Back in ServiceBills, open **Messaging → Inbox** and show the reply in the conversation | "Customer replies are received via webhook and shown in our shared inbox." |
| 6 | Optional, if the window is open: answer in the Inbox and show it arriving on the phone | "Agents reply to customers from ServiceBills." |
| 7 | Optional: record a payment for a test customer (that customer's phone = the phone on screen), then show the payment confirmation arriving on the phone | "Automatic transactional notifications: payment confirmations, renewals, reminders." |

## Video 2: create a message template from ServiceBills (≈ 1–2 minutes)

| # | On screen | Caption to add |
|---|---|---|
| 1 | Settings → WhatsApp Notifications → **Submit standard templates** | "Our customers manage their WhatsApp message templates from ServiceBills (whatsapp_business_management)." |
| 2 | Select **Payment confirmation**. Edit one sentence of the wording and show the variable chips (Customer name, Amount, Balance) | "Businesses edit the wording; required variables are enforced." |
| 3 | Click **Submit** and show the result **PENDING** | "Template submitted to Meta for review via the API." |
| 4 | Open WhatsApp Manager → Message templates for the Beta Marketing Solutions account, and show the same template listed | "The template is created in the customer's WhatsApp Business Account." |
| 5 | Optional: open the WhatsApp Templates tab in ServiceBills and show the status | "Status updates arrive by webhook (message_template_status_update)." |

## Submission notes (paste into App Review → `whatsapp_business_messaging` → "How will your app use this permission")

> ServiceBills (https://servicebills.onrender.com) is a SaaS billing and customer-management platform for internet service providers. Beta consulting Agency operates it as a WhatsApp Tech Provider. Each of our business customers connects its own WhatsApp Business number, using Embedded Signup or manual configuration. ServiceBills then uses whatsapp_business_messaging to send that business's transactional messages to its subscribers (payment confirmations, subscription renewals, payment reminders, balance statements, payment links and service-outage notices) and to let the business's staff reply to customers from a shared inbox. Incoming customer messages are received through our webhook. Each business uses its own WhatsApp Business Account, and Meta bills conversations to that account.
>
> The screencast shows a business user logged in to ServiceBills (English UI). The connected WhatsApp Business number (+961 71 228 998) is shown in Settings. A message is sent from our app UI with the Cloud API, and the same message is shown arriving in the native WhatsApp client on a phone. The phone's reply is then shown arriving in our inbox through the webhook. A second screencast shows a message template being created from ServiceBills with whatsapp_business_management.
>
> Our app uses Embedded Signup (Facebook Login for Business configuration "ServiceBills WA Signup") to onboard customers. Embedded Signup cannot onboard a number until this permission has Advanced access (error #2655111), so the recording shows a number connected to our app through our manual configuration path. The rest of the flow (send, delivery, inbox) is identical for Embedded Signup customers. Test login for reviewers: [fill in a reviewer account on the WA Test tenant, if Meta asks for one].

## After approval

1. Re-run the Embedded Signup test on WA Test with the spare SIM. Configure **coexistence** in the Login configuration ("ServiceBills WA Signup" → products/features), because ES v4 ignored the `featureType` extra on 2026-10-20.
2. Clear `META_AUTO_CONNECT_TENANTS` on Render so that every Pro tenant gets automatic connect.
3. Update `specs/001-whatsapp-auto-connect/research.md` R-0 and R-2 with the outcome.
