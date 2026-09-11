# Customer Service AI Agent — Design Spec

> **Version:** 0.1 — 2026-09-12
> **Status:** Draft for Review

---

## Problem

DeltaNet's customers currently reach support by calling a mobile number or messaging the owner directly on WhatsApp. This does not scale: every routine question ("Am I blocked?", "What do I owe?", "My internet is slow") requires a human response and interrupts engineering work. There is no after-hours coverage and no structured way to collect fault reports that feed back into the network monitoring system.

This spec defines a customer-facing AI service agent that speaks Lebanese Arabic, understands voice notes and text on WhatsApp, handles phone calls via voice, and can diagnose network issues by delegating to the existing on-premise agent — without requiring human intervention for the majority of support queries.

---

## Goals

1. **Answer routine queries without human involvement**: subscription status, balance due, expiry date, plan details, payment confirmation.
2. **Diagnose connectivity issues**: determine whether the customer's ONU is online, whether there is an OLT/PON-level outage, and relay a plain-language diagnosis back to the customer.
3. **Send payment links**: dispatch a Whish self-serve payment link when a customer wants to pay.
4. **Speak Lebanese colloquial Arabic**: the agent's voice and text persona must be indistinguishable in dialect and register from a native Lebanese support agent — not Modern Standard Arabic, not Egyptian, not a robot.
5. **Accept voice notes on WhatsApp**: a customer sending a voice note receives a text and/or voice note reply.
6. **Handle phone calls end-to-end**: real-time spoken conversation with no human in the loop for standard queries.
7. **Escalate gracefully**: when the agent cannot resolve an issue, it collects context and flags the conversation for a human to follow up.

---

## Non-Goals (v1)

- **Billing mutations**: the agent cannot change a subscription plan, process a payment, or cancel a subscription on behalf of the customer. It can send a link; a human or the Whish flow completes it.
- **English or French primary mode**: the agent detects and responds in English when addressed in English (many Lebanese are bilingual), but the default persona and first greeting are in Lebanese Arabic.
- **Inbound SMS**: no Lebanese SMS short-code integration in v1.
- **Proactive outbound calls**: agent only responds to inbound contacts.
- **Training the LLM on proprietary data**: the LLM is prompted at runtime; no fine-tuning.
- **Replacing the on-premise agent**: the customer service agent does not run inside the LAN. It delegates network jobs to the existing `servicebills_agent.py` via the cloud job queue.

---

## Architecture Overview

```
CHANNEL LAYER
─────────────
[Customer WhatsApp]  ──► Meta Cloud API webhook ──► Flask /api/cs-agent/whatsapp
[Customer Phone Call] ──► ElevenLabs Conv. AI  ──► Tool webhook calls

BRAIN LAYER (stateless per turn)
─────────────────────────────────────────────────────────────────────────────
Flask handler resolves customer → builds context → calls LLM → picks tool calls
                                                               │
                 ┌─────────────────────────────────────────────┤
                 │ Tool A: lookup_customer (DB)                 │
                 │ Tool B: get_customer_status (DB)             │
                 │ Tool C: network_diagnostic (→ job queue)     │
                 │ Tool D: send_payment_link (Whish)            │
                 │ Tool E: escalate_to_human (flag + notify)    │
                 └──────────────────────────────────────────────┘

VOICE LAYER (WhatsApp voice notes only)
────────────────────────────────────────
Incoming voice note OGG → Whisper STT → Brain Layer → ElevenLabs TTS → OGG → WhatsApp reply

NETWORK LAYER (when diagnostic is needed)
──────────────────────────────────────────
Flask creates NetworkAgentJob row
On-premise servicebills_agent.py polls → runs olt_status / secret_status → posts result
Flask polls until done (max 30 s) → returns plain-language result to Brain Layer
```

---

## Channel 1 — WhatsApp

### Webhook
- Endpoint: `POST /api/cs-agent/whatsapp`
- Provider: Meta Cloud API (free), WABA (WhatsApp Business Account) required
- Message types handled: `text`, `audio` (voice notes), `image` (ignored in v1 with a polite reply)
- Verification: Meta hub-mode GET handshake at the same endpoint

### Voice Note Pipeline (WhatsApp)
1. Meta delivers `audio` message with a `media_id`
2. Flask downloads the OGG/Opus file via `GET https://graph.facebook.com/v19.0/<media_id>`
3. File is sent to **OpenAI Whisper** (`whisper-1`) with `language=ar` hint
   - Whisper handles Lebanese colloquial including French/English code-switching
   - Returns a text transcript
4. Transcript enters the Brain Layer as if it were a text message
5. LLM response is sent back as **text** and optionally as a **voice note** (see TTS below)

### TTS for WhatsApp Voice Note Replies
- ElevenLabs TTS API is called with the text response
- Model: `eleven_multilingual_v2` with the cloned Lebanese voice
- Output format: MP3 → converted to OGG/Opus with ffmpeg (WhatsApp requires OGG Opus, `audio/ogg; codecs=opus`)
- Sent via `POST https://graph.facebook.com/v19.0/<phone_number_id>/messages` with type `audio`
- **Policy**: voice reply is only sent when the customer's original message was a voice note, or when the customer explicitly asks for a voice reply. All other replies are text-only to avoid being intrusive.

### Conversation Session
- A `CSAgentSession` DB row (one per `wa_id`, i.e. WhatsApp user ID) tracks:
  - `customer_id` (nullable until identified)
  - `state` enum: `identifying | active | escalated | closed`
  - `context` JSONB: last N message turns for LLM context window
  - `created_at`, `last_message_at`
- Sessions expire after 24 hours of inactivity (reset to `identifying` state)

---

## Channel 2 — Phone Calls (ElevenLabs Conversational AI)

### Agent Configuration (ElevenLabs Dashboard)
- **Voice**: Cloned Lebanese Arabic voice (see Voice section below)
- **Language**: Arabic (multilingual v2)
- **System Prompt**: Lebanese dialect persona (see Persona section)
- **First Message**: "أهلاً وسهلاً! أنا سلام، مساعدك في دلتانت. كيف فيني ساعدك اليوم؟"
- **Tools**: Five tool webhooks pointing to `/api/cs-agent/tools/*`
- **Silence timeout**: 8 seconds (Lebanese callers speak fast, pause before switching to English)
- **End-call phrases**: "يسلمو", "مع السلامة", "شكراً"

### Phone Number
- Option A: ElevenLabs built-in phone number (if available in Lebanon / Lebanon DID)
- Option B: Twilio DID (Lebanese +961 number) forwarded to ElevenLabs via SIP — ElevenLabs supports Twilio SIP trunking

### Tool Webhook Security
- ElevenLabs signs webhook calls with an HMAC header
- Flask verifies the signature before executing any tool
- Tools return JSON only; no HTML, no redirects

---

## Voice — Lebanese Arabic Persona

### The Problem With Off-the-Shelf Arabic TTS
Standard Arabic TTS (MSA) sounds formal and alien to Lebanese customers. Egyptian Arabic is the most common alternative but is equally wrong for this context. The solution is **voice cloning**.

### Voice Cloning Process
1. Record a native Lebanese Arabic speaker (a team member or a voice actor) for 10–30 minutes of varied, natural speech:
   - Greetings, apologies, confirmations, numbers (phone numbers, amounts)
   - Code-switched sentences ("إنترنت" + "offline" + "subscription")
   - Calm reassuring tone suitable for support
2. Upload to ElevenLabs **Professional Voice Clone** (PVC) — best quality, ~30 min audio
3. The clone is used for both ElevenLabs Conversational AI (phone calls) and ElevenLabs TTS API (WhatsApp voice notes)
4. Name the voice internally: **"Salam"** (سلام)

### Whisper STT for Lebanese Dialect
- Model: `whisper-1` (cloud API) or `whisper-large-v3` (self-hosted if privacy is a concern)
- Language hint: `ar`
- Whisper was trained on broad Arabic audio including Levantine and handles code-switching well
- Post-processing: normalize digits (٣ → 3), strip filler words common in Lebanese (يعني, هيك, بس)

---

## Brain Layer — LLM and Tools

### LLM Choice
- **Primary**: `claude-3-5-haiku-20241022` — fast, cheap, good Arabic, good tool-use; suitable for high-volume support chat
- **Escalation / complex diagnosis**: `claude-3-5-sonnet-20241022` — when the agent needs to reason about multi-device network state
- **WhatsApp**: stateless per-turn with session context injected
- **ElevenLabs**: ElevenLabs manages the LLM call internally; tool results are returned as JSON

### System Prompt (excerpt — full prompt in implementation plan)
```
أنت سلام، موظف الدعم الافتراضي لشركة دلتانت للإنترنت.
بتحكي عربي لبناني عامي. ما بتستخدم مصطلحات رسمية أو فصحى إلا لما الزبون يبدأ بالفصحى.
بتفهم إنجليزي وفرنساوي وبتجاوب بنفس لغة الزبون.
ما بتذكر للزبون إنك AI إلا إذا سألك مباشرة.
```
*(You are Salam, DeltaNet's virtual support agent. You speak Lebanese colloquial Arabic. You understand English and French and respond in the customer's language. You don't mention being an AI unless directly asked.)*

### Tool Definitions

#### Tool A — `lookup_customer`
**Trigger**: agent needs to identify who it's speaking with
**Input**: `{ "phone": "+96170123456" }` — the WhatsApp number or caller ID
**Flask endpoint**: `GET /api/cs-agent/tools/lookup-customer?phone=<e164>`
**Logic**: `Customer.query.filter_by(tenant_id=..., phone=normalized_phone)` (supports multiple matches — returns list)
**Output**: `[{ "id": 42, "name": "Georges Khoury", "plan": "50 Mbps Monthly", "status": "active" }]`

#### Tool B — `get_customer_status`
**Trigger**: agent needs full detail on a specific customer
**Input**: `{ "customer_id": 42 }`
**Flask endpoint**: `GET /api/cs-agent/tools/customer-status?customer_id=42`
**Output**:
```json
{
  "name": "Georges Khoury",
  "plan": "50 Mbps Monthly",
  "status": "active",
  "expiry_date": "2026-10-01",
  "days_until_expiry": 19,
  "balance_due": 0,
  "last_payment": { "amount": 25, "date": "2026-09-01", "method": "cash" },
  "onu_mac": "aa:bb:cc:dd:ee:ff",
  "onu_status": "online",
  "upstream_provider": "manual"
}
```

#### Tool C — `network_diagnostic`
**Trigger**: customer reports connectivity issue; agent needs live network state
**Input**: `{ "customer_id": 42 }`
**Flask endpoint**: `POST /api/cs-agent/tools/network-diagnostic`
**Logic**:
1. Look up customer's `network_device_id` (their Mikrotik/OLT)
2. Create `NetworkAgentJob(operation='olt_status', ...)` if OLT, or `secret_status` if Mikrotik
3. Poll `NetworkAgentJob.status` up to 30 s (500 ms intervals)
4. Translate raw result into a plain object:
```json
{
  "onu_online": false,
  "onu_rx_power_dbm": null,
  "olt_pon_has_outage": true,
  "affected_onus": 12,
  "diagnosis": "pon_outage",
  "plain_ar": "في انقطاع على البورت الضوئي اللي إنت متصل فيه. مش مشكلتك الشخصية — 12 زبون متأثرين. فريق الصيانة عارف."
}
```
5. If job times out: `{ "error": "timeout", "plain_ar": "ما قدرت تحقق من الشبكة هلق. حاول بعد دقيقتين." }`

#### Tool D — `send_payment_link`
**Trigger**: customer wants to pay their bill
**Input**: `{ "customer_id": 42 }`
**Flask endpoint**: `POST /api/cs-agent/tools/send-payment-link`
**Logic**: Generates or retrieves the customer's Whish self-serve payment URL (uses `CustomerWhishPaymentAttempt` / `public_pay_slug` when Whish feature is implemented; falls back to a WhatsApp message with bank details in v1)
**Output**: `{ "link": "https://servicebills.salloumservices.com/pay-business?t=...", "sent": true }`

#### Tool E — `escalate_to_human`
**Trigger**: agent cannot resolve the issue, customer is frustrated, or issue is outside agent's scope
**Input**: `{ "customer_id": 42, "reason": "customer requesting plan change", "summary": "..." }`
**Flask endpoint**: `POST /api/cs-agent/tools/escalate`
**Logic**:
1. Sets `CSAgentSession.state = 'escalated'`
2. Sends a WhatsApp message to the admin/support number with the conversation summary
3. Sends the customer a message: "رح يتواصل معك أحد من الفريق قريباً. شكراً لصبرك."
**Output**: `{ "escalated": true }`

---

## Data Model

### New Table: `cs_agent_session`
```
id                  INTEGER PK
tenant_id           INTEGER FK → tenant.id  NOT NULL
wa_id               VARCHAR(20)   -- WhatsApp E.164 number, nullable
customer_id         INTEGER FK → customer.id  NULL until identified
channel             VARCHAR(10)   -- 'whatsapp' | 'phone'
state               VARCHAR(20)   -- 'identifying' | 'active' | 'escalated' | 'closed'
context_json        TEXT          -- last N turns as JSON array for LLM context
created_at          DATETIME
last_message_at     DATETIME
```
Indexes: `(tenant_id, wa_id)`, `(tenant_id, customer_id)`.

### New Table: `cs_agent_message_log`
```
id                  INTEGER PK
session_id          INTEGER FK → cs_agent_session.id
direction           VARCHAR(3)    -- 'in' | 'out'
channel             VARCHAR(10)
content_type        VARCHAR(10)   -- 'text' | 'audio' | 'tool_call' | 'tool_result'
content_text        TEXT          -- transcript or message text
audio_url           TEXT          -- for voice notes (stored temporarily)
tool_name           VARCHAR(50)
tool_input_json     TEXT
tool_output_json    TEXT
llm_model           VARCHAR(50)
latency_ms          INTEGER
created_at          DATETIME
```
Purpose: audit trail, debugging, future quality improvement.

---

## Security and Privacy

- **Customer identification is by caller-ID / WhatsApp number only** — the agent never asks for passwords or PINs. If a phone matches multiple customers (same number on two accounts), the agent asks to confirm by name.
- **No credential exposure**: Tool C (`network_diagnostic`) creates a job row; the agent never receives device IPs, passwords, or community strings.
- **HMAC webhook verification**: both Meta Cloud API (X-Hub-Signature-256) and ElevenLabs tool webhooks are verified before any logic runs.
- **Rate limiting**: `POST /api/cs-agent/whatsapp` is rate-limited per `wa_id` to prevent prompt-injection flooding.
- **Tool endpoint authentication**: tool endpoints require a server-to-server secret header (`X-CS-Agent-Secret`), not a JWT — they are not user-facing.
- **Audio files are not stored permanently**: downloaded OGG files for Whisper transcription are deleted immediately after transcription.
- **Prompt injection mitigation**: customer message content is wrapped in a quoted block before being passed to the LLM. The system prompt explicitly instructs the model to ignore instructions embedded in customer messages.
- **PII in logs**: `cs_agent_message_log.content_text` stores transcripts — this table must be excluded from any log exports and access-controlled to admin only.

---

## Persona and Conversation Design

### Language Policy
| Customer writes/says | Agent responds in |
|---|---|
| Lebanese Arabic | Lebanese Arabic |
| Modern Standard Arabic | Lebanese Arabic (gently) |
| English | English |
| French | French |
| Mixed (Arabic + English + French) | Same mix |

### Tone
- Warm, informal, fast — Lebanese service culture is direct and personable
- First greeting uses the customer's first name once it's known
- Apologies are genuine, not corporate ("يعني أنا آسف كتير, هيدا مش معقول")
- Numbers are spoken as Lebanese Arabic (مية وخمسة وعشرين ألف ليرة / خمسة وعشرين دولار)

### Failure Modes — Graceful Degradation
| Failure | Agent behavior |
|---|---|
| On-premise agent offline / job timeout | Acknowledges issue, cannot diagnose, escalates |
| Customer not found in DB | Asks to confirm number, then escalates if still not found |
| Whisper STT returns empty / gibberish | Asks customer to repeat or type the question |
| LLM API error | Replies with a fixed fallback message, logs error |
| ElevenLabs TTS error | Falls back to text-only reply on WhatsApp |

---

## Resolved Design Decisions

1. **Separate agent, not an extension of the on-premise agent.** The on-premise agent is a deterministic job executor inside the LAN. The customer service agent is a cloud-hosted conversational AI. Different threat models, different runtimes, different failure domains.

2. **Voice cloning over off-the-shelf Arabic TTS.** No existing TTS system has a native Lebanese Arabic voice. Voice cloning a real speaker is the only path to authentic dialect — and ElevenLabs PVC quality is sufficient for phone call use.

3. **Whisper for STT, not ElevenLabs STT.** ElevenLabs STT is optimised for the voice it cloned (output path). Whisper has broader Arabic training data and is proven on Levantine dialect + code-switching. Used only on the WhatsApp input path; ElevenLabs handles STT natively for phone calls.

4. **Claude Haiku as the default LLM.** Speed is critical for WhatsApp (customers expect <3 s reply). Haiku delivers sub-second inference at low cost and handles Arabic tool-use reliably. Sonnet is reserved for the network diagnostic reasoning step.

5. **WhatsApp via Meta Cloud API directly, not Twilio.** Meta's direct API is free per message (only template messages have fees). Twilio WhatsApp adds a per-message fee on top of Meta's fees.

6. **Tool endpoints are internal (server-to-server), not JWT-protected.** JWT is designed for user-facing requests. These endpoints are called by ElevenLabs or the Flask WhatsApp handler — a pre-shared secret is simpler and appropriate.

7. **Voice note reply is opt-in behaviour.** Sending unrequested voice notes is intrusive. The agent only replies with audio if the customer's message was audio, or if they explicitly say "ابعتلي رسالة صوتية".

8. **`cs_agent_session` is tenant-scoped.** DeltaNet may eventually white-label this to other ISP tenants. Every session, message log, and tool call is tenant-isolated from day one.

---

## Out of Scope (Future)

- **Proactive outbound notifications by voice** (expiry reminders, payment confirmations) — deferred, needs campaign management
- **Ticketing system integration** — escalations currently go to the admin's WhatsApp; a proper ticket trail is a follow-on feature
- **Customer portal self-service link** — the Whish self-serve page (planned feature, not yet built)
- **Multi-tenant white-label persona** — each tenant gets a different name/voice — possible with the data model but not configured in v1
- **Real-time sentiment analysis and quality scoring** — the `cs_agent_message_log` table is designed to support this; the analysis is deferred
