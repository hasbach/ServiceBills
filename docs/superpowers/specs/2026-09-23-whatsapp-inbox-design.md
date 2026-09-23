# WhatsApp Inbox (Messaging → Inbox) — Design

**Date:** 2026-09-23
**Status:** Approved in brainstorming, pending spec review

## Goal

Give admins a WhatsApp inbox inside the Messaging tab. They can see every
conversation on the business's WhatsApp Cloud API number, with conversations that
need a human (AI failed, escalated, unknown sender, media the AI can't read,
send failed, AI switched off) surfaced first. They can play back text, emojis,
voice notes, images and stickers, and reply with text/emojis, recorded voice
notes, uploaded stickers, and reactions. Admins get web push notifications
(browser or installed PWA) when a conversation needs attention.

## Current state (why this is bigger than a UI)

- Inbound WhatsApp messages are **not persisted**. The only trace is a
  `CSAgentMessageLog` row, and that is written only when the AI produced a reply
  (`cs_agent_tools.py:2302`). When the AI returns nothing, the message is lost
  (`cs_agent_tools.py:2201`).
- Media is never stored. Voice notes are downloaded, transcribed in memory
  (`cs_agent_tools.py:1213`) and discarded. For image, sticker, video and
  document messages only a placeholder string reaches the AI
  (`app.py:7891-7929`).
- Outbound sending exists only for text, templates and TTS audio
  (`send_whatsapp_voice`, `cs_agent_tools.py:1170`). There is no image or
  sticker send, and no reaction send.
- Delivery `statuses` from the webhook are only logged (`app.py:7882`).
- Meta webhook retries are not deduplicated, so a retried delivery can trigger a
  second AI reply.
- Web push exists (`PushSubscription`, `send_push_notification`, `app.py:7237`)
  but it has three gaps:
  - It uses `tenant_query`, so it cannot be called from the webhook, which has
    no JWT.
  - It sends to every subscription in the tenant regardless of role.
  - `frontend/public/index.html` links a `manifest.json` that does not exist in
    the repo. Without it the app is not installable, and iOS web push requires
    an installed home-screen web app.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Inbox scope | Full inbox of every conversation, default filter "Needs attention" |
| AI after admin replies | AI paused for that conversation until admin clicks **Resolve**, or auto-resumes after 24h with no admin activity |
| Stickers | Upload only (.webp as-is; PNG/JPG converted to 512×512 webp). No sticker library in v1 |
| 24h window | Countdown shown; after expiry the composer becomes a template picker |
| Notifications | Web push to admins (browser + installed PWA) plus in-app badge polling |

## Data model

The new tables are defined in `app.py` next to the other CS agent models,
following the existing pattern. They carry `tenant_id`, which is stamped by the
`before_flush` hook. The Alembic migration **adds new tables only**, so the known
migration drift on old tables does not apply. The one column change is on
`PushSubscription` (below), and it must be guarded with an `inspect(bind)` check
before `ADD COLUMN`.

### `WhatsAppConversation`
One row per (tenant, customer phone).

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| tenant_id | int FK, indexed | |
| wa_phone | str(20) | digits only, as Meta sends `from`; unique with tenant_id |
| customer_id | int FK nullable | matched by last 8 digits, same rule as the webhook today |
| contact_name | str(120) nullable | WhatsApp profile name from `contacts[0].profile.name` |
| needs_attention | bool, default false, indexed | |
| attention_reason | str(30) nullable | see reasons below |
| attention_since | datetime nullable | |
| ai_paused | bool default false | |
| ai_paused_at | datetime nullable | |
| last_admin_reply_at | datetime nullable | drives 24h auto-resume |
| last_inbound_at | datetime nullable | drives the 24h customer-service window |
| last_message_at | datetime, indexed | list ordering |
| last_message_preview | str(200) | e.g. "🎤 Voice note", "Sticker", text snippet |
| unread_count | int default 0 | inbound messages since the last admin "mark read" |
| last_push_at | datetime nullable | push throttle |

### `WhatsAppMessage`
One row per message, inbound or outbound.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| tenant_id | int FK, indexed | |
| conversation_id | int FK, indexed | |
| direction | str(3) | `in` / `out` |
| sender | str(10) | `customer` / `ai` / `admin` / `system` |
| sent_by_user_id | int FK nullable | admin who sent it |
| msg_type | str(20) | `text`, `audio`, `image`, `video`, `document`, `sticker`, `reaction`, `location`, `contacts`, `interactive`, `button`, `template`, `unsupported` |
| text | Text nullable | body, caption, button title, or template summary |
| transcript | Text nullable | voice-note STT text (from the existing ElevenLabs transcription) |
| wa_message_id | str(128) nullable | Meta `wamid`; unique with tenant_id; used for **webhook dedupe** and status updates |
| reply_to_wa_message_id | str(128) nullable | quoted message (`context.id`) |
| reaction_emoji | str(16) nullable | for `reaction` rows |
| reaction_target_wa_id | str(128) nullable | message the reaction applies to |
| wa_media_id | str(128) nullable | Meta media id (inbound) or uploaded id (outbound) |
| media_key | str(300) nullable | storage key of the original file |
| media_playback_key | str(300) nullable | browser-friendly copy (inbound audio → mp3) |
| media_mime | str(80) nullable | |
| media_status | str(10) | `none` / `pending` / `stored` / `failed` |
| status | str(12) | inbound: `received`; outbound: `queued` → `sent` → `delivered` → `read`, or `failed` |
| error_code | str(20) nullable | Meta error code, e.g. `131047` |
| error_message | str(300) nullable | |
| created_at | datetime | Meta's `timestamp` for inbound; now for outbound |

### `PushSubscription` (existing), one added column
- `topics` Text, JSON list, default `["tickets","whatsapp_inbox"]`. This lets a
  device turn off inbox pushes while keeping ticket pushes. Existing rows read as
  the default.

### Attention reasons

| Reason | Set when |
|---|---|
| `ai_failed` | the AI path raised or returned no reply, **or** the tenant has a Gemini key but Gemini produced nothing and the rule-based fallback answered instead (the fallback always produces some text) |
| `escalated` | `escalate_to_human` ran for this phone (tool call, keyword, or HTTP endpoint) |
| `awaiting_admin` | a new inbound message arrived while `ai_paused` |
| `unknown_sender` | the phone matched no `Customer` |
| `ai_inactive` | CS agent inactive or auto-reply disabled (the existing SupportTicket is still created) |
| `media_received` | an image, video, document, location or contact card was received. Stickers and reactions are **not** flagged |
| `send_failed` | an AI or admin outbound message failed (API error, or a `failed` status callback) |

Only the latest reason is stored; it overwrites the previous one. Any new
reason sets `needs_attention=true` and, if not already set, `attention_since`.

## Backend

New module **`whatsapp_inbox.py`** holds persistence, media and send helpers,
so `app.py` doesn't grow further. Routes are registered in `app.py`, following
the existing route pattern. Models are imported lazily inside functions (the
same approach as `cs_agent_tools.py`) to avoid circular imports.

### 1. Receiving: webhook changes (`whatsapp_webhook`, `app.py:7780`)

For each `messages[]` item, after tenant resolution and the HMAC check:

1. **Dedupe:** if a `WhatsAppMessage` with this `wa_message_id` already exists
   for the tenant, skip the message entirely, including the AI. This fixes
   double replies on Meta retries.
2. `upsert_conversation(tenant_id, from, contact_name)` matches the customer and
   updates `last_inbound_at`, `last_message_at`, the preview and `unread_count`.
3. `record_inbound(...)` writes the `WhatsAppMessage` row, parsing every type:
   - text
   - button and interactive titles
   - media id, mime and caption
   - reactions: emoji plus target wamid; an empty emoji means the reaction was
     removed
   - location, as "lat,lng name"
   - contacts, as the names
   - `context.id` stored as `reply_to_wa_message_id`
4. If the message carries media, set `media_status='pending'` and spawn a
   **media download greenlet** (separate small pool, not the AI pool):
   - It runs `download_meta_media` (reusing the existing helper) and saves the
     file with a new `storage.save_bytes(data, tenant_id, filename, mime)`.
   - For audio it also saves an mp3 playback copy via ffmpeg, because Safari and
     iOS can't reliably play ogg/opus.
   - It sets `media_status` to `stored` or `failed`, and never raises into the
     webhook.
5. Flag the conversation (and push, see below) for `unknown_sender` or
   `media_received`, where they apply.
6. **AI gate:**
   - If `ai_paused` and `last_admin_reply_at` is more than 24h old, auto-resume:
     set `ai_paused=false`.
   - If still paused, flag `awaiting_admin`, push, and **skip the AI**.
   - Otherwise run the existing AI path unchanged. The existing voice-note
     transcription result is also written to `WhatsAppMessage.transcript`.
7. The inactive-AI path keeps its current SupportTicket behaviour and also flags
   `ai_inactive`.

`statuses[]` items update the matching outbound `WhatsAppMessage.status`
(sent → delivered → read, never backwards). A `failed` status stores
`errors[0].code` and `errors[0].title`, and flags `send_failed`.

### 2. AI integration (`cs_agent_tools.py`)

- `handle_whatsapp_cs_ai_reply` records each outbound AI message it actually
  sends (text, plus the TTS voice when sent) as a `WhatsAppMessage` with
  `sender='ai'` and the `wamid` from the Graph response. When a send fails it
  records `status='failed'` and flags `send_failed`.
- The greenlet wrapper in the webhook catches exceptions and a `None` result and
  flags `ai_failed`. Other exits stay as they are, including the
  `forwarding_mobile` skip, which is not a failure.
- `escalate_to_human` calls `whatsapp_inbox.flag_attention(tenant_id, phone,
  'escalated')` when the channel is WhatsApp.
- `CSAgentMessageLog` stays as it is. It remains the AI's memory and history
  source, and the inbox does not replace it.

### 3. Sending: `whatsapp_inbox` send helpers

A single shared Graph helper, `graph_post(settings, path, json|files)`, builds
the request; there's no shared client today. Every send:

- runs `ensure_window_open(conversation)`. Free-form types need
  `now < last_inbound_at + 24h`; otherwise it raises `WindowClosed`, which the
  route returns as HTTP 409 `{"error":"window_closed"}`. Templates are exempt.
- writes the `WhatsAppMessage` row with `sender='admin'` and `sent_by_user_id`
  before calling Meta, then updates it with the `wamid` or the error.
- on success sets `last_admin_reply_at=now`, `ai_paused=true`,
  `needs_attention=false` and `attention_reason=null`. Replying counts as
  handling the conversation, and the AI stays paused until Resolve.
- maps Meta error `131047` (re-engagement required) to `window_closed`, in case
  Meta's view of the window differs from ours.

Supported sends:

| Type | Input | Processing |
|---|---|---|
| text | `text` (UTF-8 incl. emoji), optional `reply_to` wamid | `{"type":"text"}` with `context.message_id` when replying |
| voice | recorded blob (webm/opus from Chrome/Firefox, mp4/aac from Safari), ≤ 16 MB | ffmpeg → OGG/Opus mono 48 kHz (`-c:a libopus -b:a 32k -ac 1 -ar 48000`), `POST /{phone_id}/media`, then `{"type":"audio","audio":{"id":...}}`. Original + converted stored for playback |
| sticker | uploaded file | `.webp` 512×512 accepted as-is (static ≤ 100 KB, animated ≤ 500 KB). PNG/JPG/other webp → Pillow resize/pad to 512×512 transparent webp ≤ 100 KB. Upload `/media`, then `{"type":"sticker"}` |
| reaction | `target` (wamid of a customer message), `emoji` ("" removes) | `{"type":"reaction","reaction":{"message_id","emoji"}}`. Stored as a reaction row; shown on the target bubble |
| template | template name + params | reuses `build_meta_template_payload` and the cached template list (`GET /api/whatsapp/templates`); allowed outside the window |

New dependencies:
- **Pillow** in `requirements.txt`.
- **ffmpeg** in the Dockerfile (`apt-get install -y --no-install-recommends ffmpeg`).

If `ffmpeg` is missing on the host, voice send returns 503
`{"error":"voice_unavailable"}` and the UI hides the mic button. The capability
comes from the summary endpoint.

### 4. API endpoints (admin role only, all tenant-scoped via `tenant_query`)

| Method & path | Purpose |
|---|---|
| `GET /api/whatsapp/inbox/summary` | `{needs_attention, unread, voice_available}`, used for the badge poll (20s) |
| `GET /api/whatsapp/inbox/conversations?filter=attention\|unread\|all&q=&cursor=` | list, ordered by `last_message_at desc`, 30 per page; `q` matches name, phone, customer name |
| `GET /api/whatsapp/inbox/conversations/<id>/messages?before=<id>` | thread, newest 50 then paging back; includes window `expires_at`, `ai_paused`, the attention reason, and customer summary (name, account, plan, status) |
| `POST /api/whatsapp/inbox/conversations/<id>/read` | reset `unread_count` |
| `POST /api/whatsapp/inbox/conversations/<id>/resolve` | clear attention, set `ai_paused=false` |
| `POST /api/whatsapp/inbox/conversations/<id>/pause` | manually pause the AI without replying |
| `POST /api/whatsapp/inbox/conversations/<id>/send` | JSON for text/reaction/template; multipart for voice/sticker (`type` field) |
| `GET /api/whatsapp/inbox/media/<message_id>?variant=original\|playback` | auth-checked; streams the bytes from either backend (the browser fetches with the JWT as a blob, so no redirect is used; a redirect to R2 would need bucket CORS) |

Media is never linked publicly. Every access goes through the tenant check on
the message row.

## Web push notifications

### Backend
- Refactor to `send_push_notification(payload, tenant_id=None, roles=None,
  topic=None)`:
  - It queries `PushSubscription` by an explicit `tenant_id`, falling back to
    the current request's tenant so the existing ticket caller is unchanged.
  - It filters to users whose role is in `roles`, and to subscriptions whose
    `topics` include `topic`.
  - It removes subscriptions on 404/410, as today.
  - The VAPID `sub` claim moves to `VAPID_CLAIM_EMAIL` env (default: the
    current value).
- The inbox calls it with `roles=['admin']` and `topic='whatsapp_inbox'`.
- It is sent from the webhook greenlet **after** the DB commit, so a slow push
  service never delays the webhook response.
- **Triggers:** the conversation becomes `needs_attention` (any reason), or a
  new inbound message arrives on a conversation that is already
  `needs_attention` or `ai_paused`. AI-handled messages don't push.
- **Throttle:** at most one push per conversation per 2 minutes
  (`last_push_at`). The notification tag replaces older ones, so bursts
  collapse.
- **Payload:**
  - `title`: the contact or customer name.
  - `body`: the reason plus a preview. Examples: "AI couldn't answer: <text>",
    "🎤 Voice note", "Sticker".
  - `tag`: `wa-conv-<id>`.
  - `url`: `/?view=messaging&inbox=<id>`.
- New routes:
  - `POST /api/push-unsubscribe` removes the device's subscription.
  - `GET/PUT /api/push-subscription/topics` gets or sets this device's topics
    (matched by endpoint).

### Frontend / PWA
- **Add `frontend/public/manifest.json`**, which is currently missing:
  - `name` and `short_name`
  - `start_url: "/"`, `display: "standalone"`
  - theme and background colours
  - icons `logo192.png` and `logo512.png`, including a `purpose: "any
    maskable"` entry
  - add `<meta name="apple-mobile-web-app-capable">` and
    `apple-touch-icon` to `index.html`
- **`service-worker.js`:**
  - `showNotification` passes `tag` and `renotify: true`, plus `badge` and
    `icon`.
  - `notificationclick` focuses any open same-origin client and
    `postMessage({type:'open-conversation', id})`. If none is open it calls
    `openWindow(url)`. Today it only matches the exact URL.
- **`MessagingView`** reads the `inbox` query param or the SW message, switches
  to the Inbox tab and opens that conversation.
- **Inbox header "🔔 Notifications" control:** shows the state (blocked by
  browser / off / on for this device). "Enable" reuses
  `subscribeUserToPush` + `/push-subscribe` and sets topics to include
  `whatsapp_inbox`. "Disable" removes the topic. It also has a "Send test
  notification" item.
- **iOS:** push works only when the app is installed to the home screen (iOS
  16.4+). When the device is iOS Safari and not running standalone, the control
  shows an "Add to Home Screen first" hint instead of the Enable button.
- **Pre-flight (deploy check, not code):** confirm Render has
  `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` set. `vapid_keys.env` is
  gitignored and not in the image. Without the keys the control shows "not
  configured on the server", as `subscribeUserToPush` does today.

## Frontend: Inbox sub-tab

`MessagingView.js` gets a third tab, **Inbox**, placed first because it is the
daily-use tab; the Messaging nav item also shows a badge. New components go in
`frontend/src/components/inbox/`:

- **`InboxView.js`:** two-pane layout on desktop; on mobile, list and thread
  are separate stacked screens. It polls the list every 20s and the open thread
  every 5s. There are no websockets in v1.
- **`ConversationList.js`:**
  - "Needs attention / Unread / All" toggle (default: Needs attention) and
    search.
  - Each row shows the name/phone, preview, relative time, unread badge, a
    reason chip (colour per reason) and an "AI paused" chip.
- **`ChatThread.js`:**
  - WhatsApp-style bubbles. Customer on the left; AI (🤖 label) and admin
    (admin name) on the right in different colours.
  - Renders per type:
    - text with emoji;
    - audio: `<audio>` using the playback variant, plus the collapsible
      transcript;
    - image: thumbnail, click to enlarge;
    - sticker: 128px, no bubble;
    - video player;
    - document download link;
    - location: link to maps;
    - reactions: emoji on the target bubble.
  - Quoted replies; delivery ticks (sent/delivered/read, and failed with the
    error tooltip); and "media unavailable" for `failed`.
  - The header shows the linked customer (name, account, status), with a link
    to the customer, and the **Resolve** and **Pause AI** buttons.
- **`Composer.js`:**
  - text field with an emoji picker (`emoji-picker-react`);
  - a reply-to chip set by hovering or long-pressing a customer message;
  - a react action on customer bubbles (quick 6 emojis plus the full picker);
  - a sticker upload button;
  - the voice recorder;
  - the window countdown ("Window closes in 5h 12m"). After expiry the
    composer is replaced by a template picker with parameter fields, sent
    through the template send type.
- **`VoiceRecorder.js`:** `MediaRecorder` with the mime chosen by
  `isTypeSupported` (`audio/webm;codecs=opus`, then `audio/mp4`).
  Record → stop → preview playback → send or discard. It has a 5-minute cap and
  shows a clear error when mic permission is denied.
- The API wrappers are added to `context/AppContext.js`, next to the existing
  messaging calls.

## Error handling

- Webhook persistence errors are logged and do not stop the AI path. The
  webhook still returns 200, because Meta retries non-200 responses and would
  cause duplicates. The dedupe step makes retries safe.
- A failed media download leaves `media_status='failed'`. The UI shows "media
  unavailable" and the text/transcript if any.
- For send errors the UI shows the Meta error title on the failed bubble, with a
  retry action for text. Voice and stickers are re-sent from the stored file.
- The window is enforced on the server, not only in the UI.
- Push failures never propagate. A 404/410 prunes the subscription.

## Security

- All inbox routes require JWT + admin role and use `tenant_query`, so one
  tenant can never read another's conversations or media.
- The media endpoint checks the message's tenant before streaming. Responses
  are `Cache-Control: private, max-age=300`.
- Upload limits:
  - voice ≤ 16 MB;
  - sticker source ≤ 5 MB;
  - mime is sniffed with Pillow or ffmpeg, not trusted from the client.
- ffmpeg runs with argument lists (no shell), a timeout, and temp files in
  `tempfile.TemporaryDirectory`.

## Testing (pytest, `tests/test_whatsapp_inbox.py`, reusing the `_signed_post` HMAC helper)

- Webhook persists each inbound type (text, audio, image, sticker, reaction,
  location, interactive) and parses the conversation and contact name.
- A duplicate `wamid` is not stored twice and the AI is not invoked twice.
- Each attention reason is set in its path:
  - AI returns `None` or raises → `ai_failed`
  - escalation → `escalated`
  - paused → `awaiting_admin`, with the AI **not** called
  - unknown sender → `unknown_sender`
  - inactive → `ai_inactive`
  - image → `media_received`, but a sticker is **not** flagged
  - failed status callback → `send_failed`
- Auto-resume after 24h without admin activity.
- Status callbacks advance outbound status and never regress it.
- Send endpoints, with the Graph API mocked:
  - text with reply context;
  - reaction;
  - sticker PNG → webp conversion (dimensions/size);
  - voice conversion, skipped if ffmpeg isn't installed in the test env;
  - template outside the window.
- Free-form send outside the window → 409, and a Meta `131047` also maps to
  `window_closed`.
- An admin send pauses the AI and clears attention; resolve un-pauses it.
- Tenant isolation: tenant B gets 404 on tenant A's conversation, messages and
  media. Non-admin roles get 403.
- Push: targets only admins of the right tenant with the topic enabled,
  respects the 2-minute throttle, and the existing ticket push still works.

## Out of scope (v1)

- Sticker library, and saving received stickers for reuse.
- Sending images, video or documents from the admin side.
- Backfilling history. Messages from before launch were never stored.
- Real-time transport (websockets or SSE). Polling is used instead.
- Message retention and cleanup of stored media.
- Assigning conversations to specific admins.
