# WhatsApp Reply Forwarding — Restoring Rich Forwarding via a Daily Keep-Alive

**Status: Implemented (2026-08-12).**

## Problem

When a customer messages the tenant's WhatsApp Business number, the app forwards an alert to a staff mobile number (`WhatsAppSettings.forwarding_mobile`). Two generations of this feature exist in the git history:

1. **Original (commit `771fd64`, extended in `71f1014`):** forward the actual message — free-form text, plus the raw audio/video/image/document if present — directly to `forwarding_mobile`.
2. **Current, pre-this-change (commit `72d6316`):** the free-form *text* forward was replaced with a Meta-approved **template** send. Root cause, confirmed live: `forwarding_mobile` is an internal alert recipient, not a real customer conversation — it essentially never has an open WhatsApp 24-hour customer-service session. The Graph API returns a synchronous `200 OK` for a free-form send even when delivery is certain to fail (the actual rejection, error 131047, arrives only later via an async status webhook), so a "try free-form, fall back to template on failure" approach can never detect the failure and silently never falls back. Sending via the approved template directly was the fix — it works regardless of session state.

The **raw media forward** (audio/video/images/documents) was never fixed the same way — it's still a free-form send today, so it has almost certainly been silently failing for `forwarding_mobile` the whole time, for the identical underlying reason.

The tenant wants the original rich behavior back — real text and media, not a truncated template — because a template can't carry a voice note, a photo, or the customer's actual wording.

## Why a template alone can't fix this

WhatsApp's 24-hour customer-service session opens **only when the user messages the business** — never the reverse. A business-initiated template send does not open a session for the business to then send free-form messages back to that user. So no purely server-side fix exists: something on `forwarding_mobile`'s own side has to actually message the business number for a session to open.

## Design

**Daily keep-alive, closed on the tenant's own device, not in this app:**

1. A new daily scheduled job, `send_daily_whatsapp_keepalive` (mirrors the existing `generate_missing_payments_with_context` pattern — one `*_with_context` function looping active tenants, registered on the same `BackgroundScheduler` with `trigger="interval", days=1, next_run_time=datetime.now()`), sends a Meta-approved template (`WhatsAppSettings.template_forward_keepalive`, default `'daily_checkin'`) to `forwarding_mobile` once a day.
2. **This send does not open the session by itself.** It's the *prompt* — the tenant configures forwarding_mobile's own device (confirmed: it runs the WhatsApp Business App, which has its own native auto-reply/away-message feature, entirely outside this codebase) to automatically reply to it. That reply is a real message from `forwarding_mobile` **to** the business number, which is what genuinely opens a fresh 24-hour session.
3. For the rest of that session, the webhook's forwarding logic sends free-form text and raw media to `forwarding_mobile` exactly as before `72d6316` — no template, no fallback. **Deliberate choice, confirmed with the tenant:** if the keep-alive chain ever lapses for any reason (daily job didn't run, auto-reply wasn't configured that day, template got rejected), the forward silently fails again the same way it did before — there's no template safety net this time. Simpler, but means the daily chain actually has to keep working.
4. Guarded with `WhatsAppSettings.last_forwarding_keepalive_sent_at` so the job doesn't re-send on every process restart — this host (Render free tier) spins down when idle and the scheduler fires all daily jobs immediately on every wake-up (see the existing comment block above the scheduler registration in `app.py`), so without this guard a restart-heavy day would blast the keep-alive template repeatedly.
5. The existing customer-facing auto-reply (`auto_reply_enabled`/`auto_reply_message`, fires on any inbound message to the business number) is explicitly skipped when the sender is `forwarding_mobile` itself — its replies exist only to keep the session open, not to request support, so an automated reply back to it would just be noise (and would itself count as a free-form send, consuming the same session it's trying to keep open for no reason).

## What Meta policy risk this carries

Using a template purely to solicit a reply that reopens a session for *other*, unrelated content is a known gray area in Meta's Business Messaging Policy — template categories (Utility/Marketing/Authentication) are approved for specific purposes, and a template whose real function is "please tap reply so I can message you freely later" may not match what any of those categories were intended for. This is a judgment call the tenant made deliberately, not something to silently build around — flagged here, not resolved, because it's a policy/business decision, not a technical one. If Meta ever restricts the template or the WABA over this pattern, the fallback is to revert to the template-only alert (still fully intact, `template_forward_alert` and its 3→2→1-param logic were not removed, just no longer called from the forwarding path — see `git log` for `app.py` around this date to restore it).

## Data model

Two new `WhatsAppSettings` columns:

```python
template_forward_keepalive          # String(200), default 'daily_checkin' — the daily prompt template name
last_forwarding_keepalive_sent_at   # DateTime, nullable — guards against re-sending same-day on restart
```

Migration: `migrations/versions/ac416dc32280_add_whatsapp_forwarding_keepalive_.py`.

## Testing

`tests/test_whatsapp_keepalive.py` — mocks `requests.post`, no real Meta API calls:

- Sends the template and records the timestamp on success.
- Does not resend the same day.
- Resends once a day has actually passed.
- Skipped when `forwarding_mobile` isn't set.
- Skipped when WhatsApp isn't enabled.
- `template_forward_keepalive` round-trips through the settings save/get endpoints.

Not tested (no test harness for real webhook payloads / a live Graph API): the restored raw text/media forward itself, and the `auto_reply` skip-guard for `forwarding_mobile`'s own replies — both are small, directly-reviewed diffs against the pre-`72d6316` code shape, not independently unit tested.
