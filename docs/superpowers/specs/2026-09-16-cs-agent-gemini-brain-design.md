# CS Agent Brain Replacement (Gemini) + Tenant Memory — Design Spec

> **Version:** 0.1 — 2026-09-16
> **Status:** Draft for Review
> **Supersedes:** Resolved Decision #4 ("Claude Haiku as the default LLM") in
> `docs/superpowers/specs/2026-09-12-customer-service-agent-design.md`. In
> production, WhatsApp text and voice-note replies actually run through
> ElevenLabs Conversational AI (`query_elevenlabs_conversational_ai`), not
> Claude Haiku as originally planned. This spec replaces that ElevenLabs
> ConvAI usage on WhatsApp with a self-orchestrated, per-tenant Gemini brain.

---

## Problem

ElevenLabs Conversational AI is billed usage, and every WhatsApp message the
CS agent handles costs money regardless of which tenant sent it — there is no
per-tenant cost isolation today. Google's Gemini API has a genuinely free
tier (no credit card) that's large enough for a single ISP's WhatsApp volume.
Separately, the agent has no way to get better at a specific tenant's
recurring questions over time — every reply is generated fresh, with no
memory of what worked before or what a human corrected.

This spec replaces the ElevenLabs ConvAI brain with a self-hosted
tool-calling loop against the free Gemini API, configured per tenant so each
tenant supplies (and pays nothing for, up to free-tier limits) their own key,
and adds a tenant-scoped "knowledge" memory the brain can draw on.

---

## Goals

1. **Replace ElevenLabs ConvAI as the WhatsApp brain** with a self-orchestrated
   Gemini tool-calling loop, using the same five tools that exist today.
2. **Zero marginal cost to the platform**: each tenant supplies their own
   Gemini API key; a tenant without one gets the existing rule-based
   processor only, never another tenant's quota.
3. **Graceful multi-step fallback**: Gemini Flash → Gemini Flash-Lite (on
   rate limit) → existing rule-based processor (`process_customer_message_ai`),
   so a free-tier hiccup never breaks the WhatsApp reply path.
4. **Tenant-scoped learned memory**: a curated set of question/answer pairs
   per tenant that gets injected into the prompt, so the agent's answers
   improve for that tenant's specific customers and network setup over time.
5. **Human-curated, not auto-learned**: memory only grows from entries a
   tenant admin explicitly adds or approves — never silently from raw,
   unreviewed conversation logs.

---

## Non-Goals (this spec)

- **STT/TTS are unchanged.** ElevenLabs Scribe (STT) and ElevenLabs TTS
  (voice-note replies) stay exactly as they are today. This spec covers only
  the conversational brain (ElevenLabs ConvAI → self-orchestrated Gemini).
- **Phone calls.** No Twilio/SIP integration exists in production today —
  ElevenLabs ConvAI is only ever invoked over WebSocket from Flask for
  WhatsApp. If a real telephony channel is built later, it's a separate spec.
- **Fine-tuning.** Memory is retrieval-based (inject relevant past Q&A into
  the prompt), not model training. Carried over from the original spec's
  Non-Goals.
- **Vector/embedding-based retrieval.** Keyword search is sufficient at
  expected per-tenant memory-entry volumes. Documented as a future upgrade
  path, not built now.
- **Automatic/unapproved learning from raw conversation logs.** Per the
  curation decision below, only admin-added or admin-approved entries become
  memory.

---

## Architecture Overview

```
BRAIN LAYER (per WhatsApp message, replaces ElevenLabs ConvAI call)
──────────────────────────────────────────────────────────────────
handle_whatsapp_cs_ai_reply()
        │
        ▼
query_gemini_agent(tenant, incoming_text, customer, recent_history)
        │
        ├─ 1. No gemini_api_key configured for tenant?
        │       → skip straight to process_customer_message_ai() (unchanged)
        │
        ├─ 2. Build system prompt:
        │       - existing Lebanese-Arabic persona
        │       - tenant network/business context
        │       - top-5 keyword-matched CSAgentKnowledgeEntry rows (memory)
        │
        ├─ 3. Call Gemini (model: gemini-flash) with function-calling
        │       schemas for the 5 existing tools, in-process (no HTTP
        │       self-call)
        │       - tool loop: execute requested tool(s), feed results back,
        │         repeat until Gemini returns a final text reply or a
        │         cap of 6 tool-call round-trips is hit (covers the
        │         two-sequential-tool diagnostic chain from the original
        │         spec with headroom; hitting the cap falls through like
        │         any other failure)
        │       - on 429 / rate-limit response: retry once on gemini-flash-lite
        │       - on any other failure, timeout, or exhausted iterations:
        │         fall through to process_customer_message_ai()
        │
        ▼
   { reply_text, intent, ticket_tag, escalate }   (same shape ai_result has today)
```

The five tools called from the loop are the same logic that backs
`/api/cs-agent/tools/*` today (`lookup_customer`, `get_customer_status`,
`network_diagnostic`, `send_payment_link`, `escalate_to_human`). Those route
handlers currently contain the logic inline; this spec factors each into a
plain Python function the route handler and the new Gemini tool dispatcher
both call, so there's one implementation, not two. The HTTP endpoints stay in
place unchanged (nothing else currently depends on them, but removing them
isn't necessary or in scope).

**Timeout budget**: unchanged from today — up to 55s total for the reply
(`network_diagnostic` alone can take 30s of polling), matching the existing
tuned value in `handle_whatsapp_cs_ai_reply`.

---

## Multi-Tenant Configuration

New columns on `CSAgentSettings` ([app.py:1569](../../../app.py)):

```python
gemini_api_key = db.Column(EncryptedString, nullable=True)   # same encrypted-at-rest
                                                               # pattern as access_token,
                                                               # portal_password
gemini_model   = db.Column(db.String(50), nullable=True)     # optional override;
                                                               # None -> default chain
                                                               # (gemini-flash then
                                                               # gemini-flash-lite)
```

**Cost isolation**: a tenant with no `gemini_api_key` never calls Gemini at
all — `query_gemini_agent()` returns `None` immediately and
`handle_whatsapp_cs_ai_reply()` falls to the rule-based processor, exactly as
it does today when ElevenLabs ConvAI has no agent configured. No tenant's
messages can ever be billed against another tenant's key, and a tenant that
never adds a key costs the platform nothing beyond what the rule-based
processor already costs (nothing).

**UI**: add a "Gemini API Key" field to the existing CS Agent settings
screen (same screen as the ElevenLabs agent ID today), with a short
instruction and link for the tenant admin to generate their own free key
from Google AI Studio.

---

## Memory / Tenant Knowledge

### New table: `cs_agent_knowledge_entry`

```
id                  INTEGER PK
tenant_id           INTEGER FK → tenant.id, indexed
question_text       TEXT NOT NULL   -- the customer question / phrasing
answer_text         TEXT NOT NULL   -- the correct answer
source              VARCHAR(20) NOT NULL   -- 'manual' | 'conversation_log'
source_log_id       INTEGER FK → cs_agent_message_log.id, nullable
created_by          INTEGER FK → user.id, nullable
is_active           BOOLEAN NOT NULL default true
created_at          DATETIME NOT NULL
updated_at          DATETIME NOT NULL
```
Index: `(tenant_id, is_active)`.

### Populating it

1. **Manual entry** — a "Question" + "Answer" form in a new "Agent Memory"
   section of CS Agent Settings. Saves directly with `source='manual'`.
2. **Promote from a real conversation** — a review list pulling recent
   `CSAgentMessageLog` in/out pairs for the tenant, with an "Add to memory"
   action that pre-fills the same form (editable before saving) and sets
   `source='conversation_log'`, `source_log_id` set.

Both paths write to the same table — the brain layer doesn't need to know or
care which path an entry came from.

### Retrieval at reply time

Before calling Gemini, `query_gemini_agent()` runs a keyword match: Postgres
`ILIKE` against `question_text` for terms from the incoming customer message,
scoped to `tenant_id` and `is_active`, limit 5, ordered by simple relevance
(number of matched terms). Matches are injected into the system prompt as a
"Known answers for this business" block. Zero matches (including a tenant
with no entries yet) simply omits the block — no special-casing needed
elsewhere in the prompt-building code.

This is deliberately not embedding/vector search: at the scale of one
tenant's curated Q&A set, keyword matching is simple, free, and needs no new
infrastructure (no pgvector, no embeddings API calls). If keyword matching
proves too fuzzy for paraphrased questions once tenants have built up larger
memory sets, the upgrade path is Gemini's free embedding endpoint plus
pgvector — not built now, since it isn't needed yet.

### UI

New "Agent Memory" section in CS Agent Settings:
- Manual-add form (Question, Answer) at the top
- Searchable table of existing entries below, with edit / deactivate / delete
- A secondary tab or expandable panel for the review-from-conversation list

---

## Fallback and Error Handling

| Condition | Behavior |
|---|---|
| No `gemini_api_key` configured for tenant | Skip Gemini entirely, go straight to `process_customer_message_ai()` |
| Gemini Flash returns 429 (rate limited) | Retry once on Gemini Flash-Lite |
| Gemini Flash-Lite also fails/429s | Fall through to `process_customer_message_ai()` |
| Gemini API key invalid | Log a warning once (avoid spamming every message), fall through to rule-based for that call |
| Gemini call times out | Fall through to rule-based, within the existing 55s overall budget |
| Tool loop exceeds max iterations without a final reply | Fall through to rule-based |

This mirrors the existing "ElevenLabs ConvAI fails → rule-based" fallback
shape already in `handle_whatsapp_cs_ai_reply()` — the rule-based processor's
role in the architecture doesn't change, only what sits above it.

---

## Security and Privacy

- `gemini_api_key` is encrypted at rest via the existing `EncryptedString`
  column type (same as `access_token`, `portal_password`).
- Tool dispatch stays in-process; no new network-exposed endpoints are added
  for the Gemini path itself (the existing `/api/cs-agent/tools/*` endpoints
  are unchanged and keep their current server-to-server secret-header auth).
- Prompt-injection mitigation carries over unchanged: customer message
  content is wrapped in a quoted block, and the system prompt instructs the
  model to ignore embedded instructions.
- `cs_agent_knowledge_entry` is tenant-scoped like every other CS agent
  table — entries never cross tenant boundaries. Content is admin-authored
  or admin-approved, so redacting any PII a customer's original question
  contained is the tenant admin's responsibility at add/approve time, same
  as it already is for anything sent to `escalate_to_human`.

---

## Data Model Summary

- `CSAgentSettings`: + `gemini_api_key` (EncryptedString), + `gemini_model` (String, nullable)
- New table `cs_agent_knowledge_entry` (see above)
- No changes to `CSAgentSession` or `CSAgentMessageLog`

---

## Testing

Extend `tests/test_cs_agent_tools.py`:
- Tool-calling loop dispatch, mocking Gemini responses (tool-call request →
  tool executed → result fed back → final text)
- Fallback chain: 429 on Flash → retried on Flash-Lite; both failing → falls
  to `process_customer_message_ai()`
- No API key configured → skips Gemini, calls rule-based directly
- Memory retrieval: entries seeded for tenant A are never returned for
  tenant B; keyword match returns expected top-5 ordering; zero-match case
  omits the block cleanly
- Manual-add and promote-from-log paths both produce a usable
  `cs_agent_knowledge_entry` row

---

## Resolved Design Decisions

1. **Gemini free tier over ElevenLabs ConvAI for the WhatsApp brain.**
   ElevenLabs ConvAI is billed per use with no per-tenant isolation; Gemini's
   free tier (Flash/Flash-Lite) is large enough for a single ISP's WhatsApp
   volume and lets each tenant own their own cost.

2. **Per-tenant API key, not a shared platform key.** Keeps marginal cost at
   zero for the platform regardless of tenant count, and matches how the
   platform already treats tenant-owned credentials (WhatsApp access tokens,
   etc.).

3. **Flash → Flash-Lite → rule-based fallback chain**, not a single model
   with no fallback. A free-tier rate limit is expected behavior at this
   price point, not an outage — it needs a cheap, fast fallback, not an
   error shown to the customer.

4. **In-process tool dispatch, not HTTP self-calls.** The existing tool
   endpoints exist for ElevenLabs (an external caller) to reach over HTTP;
   Gemini's tool loop runs inside the same Flask process, so calling the
   underlying Python functions directly avoids an unnecessary network hop.

5. **Admin-curated memory, not automatic learning from raw logs.** An
   unreviewed bad or escalated conversation must never get reinforced into
   future replies. Growth is slower, but every memory entry is something a
   human has actually vetted.

6. **Keyword search over embeddings for memory retrieval.** Per-tenant
   memory sets are expected to stay small; embeddings/vector search adds
   infrastructure (pgvector, embedding API calls) that isn't justified until
   keyword matching actually proves insufficient.

---

## Out of Scope (Future)

- Embedding/vector-based memory retrieval, if keyword matching proves too
  fuzzy at scale
- A phone-call channel (would still need its own real-time voice spec)
- Automatic promotion of high-confidence conversation-log answers into
  memory with a lighter review step, if manual review becomes a bottleneck
- Cross-tenant shared memory (e.g. common ISP FAQ) — deliberately excluded
  now; every entry is single-tenant only
