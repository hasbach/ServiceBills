# Implementation Plan: Automatic WhatsApp Connection (Coexistence / New API Number / Manual)

**Branch**: `feat/whatsapp-auto-connect` | **Date**: 2026-10-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-whatsapp-auto-connect/spec.md`

## Summary

Tenants will be able to connect WhatsApp by signing in to Meta, with no pasted credentials. There are two automatic methods, both built on Meta **Embedded Signup v4** through one platform-owned Tech Provider app:

- **Coexistence** keeps the tenant's existing WhatsApp Business app number working on the phone.
- **Embedded** connects a fresh API number.

Manual configuration stays exactly as it is today.

How it works:

- **Connecting.** The backend exchanges the signup code for a per-tenant business token and proves the token can reach the WABA the tenant claimed. It then registers the number (embedded only), subscribes webhooks, and saves everything in one transaction. The existing `WhatsAppSettings` columns carry the per-tenant token and IDs, so the ~15 existing send paths are untouched. Only the webhook HMAC and template-sample upload switch to resolving the app identity from platform config for automatic tenants.
- **Coexistence extras.** Messages the tenant sends from the phone (`smb_message_echoes`) appear in the Inbox as `sender='phone'`. History is never imported.
- **Health.** Health comes from three signals: `account_update` webhooks, send errors, and an hourly check. Admins get an alert and a Reconnect path.
- **Standard templates.** A new catalog lets tenants edit template wording with named variable chips. The server enforces the required variables, renumbers placeholders by order of appearance, and stores a `param_map` so the send path fills the right values.

## Technical Context

**Language/Version**: Python 3.13 (Flask, SQLAlchemy, Alembic); JavaScript (React, MUI) in `frontend/`

**Primary Dependencies**:

- Existing: Flask-JWT-Extended, `requests` for Graph API calls, Fernet `EncryptedString` (crypto.py), web-push (VAPID).
- New: Facebook JS SDK, loaded on demand in the browser only. No new Python packages.

**Storage**: PostgreSQL on Render (SQLite in tests and on-prem). One additive Alembic migration (see [data-model.md](data-model.md)).

**Testing**: pytest. Graph calls are mocked via `monkeypatch.setattr(<module>.requests, ...)`, and the helpers in `tests/inbox_helpers.py` are reused.

**Target Platform**: Render web service (SaaS). On-prem ServiceBills is excluded from the automatic methods (research R-10).

**Project Type**: Web application, with a monolithic Flask `app.py` plus helper modules, and a React SPA in `frontend/src`.

**Performance Goals**: The connect endpoint finishes in under 10 s, since that is about 6 Graph calls in sequence and the signup code expires after 30 s. The echo webhook is processed inline in under 200 ms. The hourly health job does one Graph GET per automatic tenant.

**Constraints**:

- Must not change behavior for existing manual tenants (SC-004).
- Hot-table migration must use the lock-retry pattern.
- A new scheduled job must be registered in three places, which a test checks.
- Secrets must never appear in API responses for automatic tenants.

**Scale/Scope**: Tens of tenants and one number per tenant. Touches about 4 backend areas (settings, webhook, templates, scheduler) and 2 frontend areas (Settings > Messaging, the template submit dialog).

**Unknowns resolved in research.md**: all of them. Five items are marked **VERIFY** and are confirmed by the Meta-side spike in [quickstart.md](quickstart.md) §0, which runs before the UI is built:

1. The coexistence `featureType` on v4
2. Whether Lebanon (+961) is eligible for coexistence
3. The payment-method signal
4. The Meta rule on variable order
5. The real echo payload

None of these change the architecture. Each has a defined fallback.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

`.specify/memory/constitution.md` is still the unfilled template, so it sets no formal gates. Instead, the plan is checked against the conventions this repo already follows:

| Convention | Status |
|---|---|
| Tests first and mocked Graph calls for every new path, with existing WhatsApp tests passing unchanged | ✅ planned (quickstart §1) |
| Alembic: random revision ID, single head, `inspect(bind)` before ADD, `_with_lock_retry` on the hot table, backfill in the same migration | ✅ data-model.md |
| Scheduled jobs registered in `_SCHEDULED_JOBS` + `SCHEDULE` + workflow cron | ✅ R-7 |
| Tenant isolation: no cross-tenant credential or number sharing (SC-006) | ✅ `debug_token` ownership proof plus a duplicate check (R-3) |
| Secrets encrypted at rest and never returned to the client (new paths) | ✅ R-9 |
| Local test server isolated from the real dev DB | ✅ quickstart §2 |
| Minimal blast radius: no refactor of the 15 send call sites | ✅ R-4 (refactor deferred) |

**Post-design re-check**: passes. There are no violations, so Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-whatsapp-auto-connect/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1 (includes the Meta-side spike, §0)
├── contracts/
│   ├── http-api.md      # new and changed REST routes, env vars
│   └── webhook-events.md
├── checklists/requirements.md
└── tasks.md             # Phase 2, written by /speckit-tasks
```

### Source Code (repository root)

```text
config.py                          # + META_* env vars
render.yaml                        # + META_* keys (sync: false)
app.py                             # WhatsAppSettings / WhatsAppTemplate new columns + WhatsAppConnectionEvent model;
                                   #   settings GET/POST guards; webhook: platform verify token, wa_app_identity HMAC,
                                   #   account_update / quality / echoes / history handling; send_whatsapp_message
                                   #   param_map ordering + needs_attention short-circuit + 190/200 detection;
                                   #   upload-sample app_id via resolver; subscribe-waba admin guard;
                                   #   scheduled job registration
wa_connect.py                      # NEW: Embedded Signup onboarding (code exchange, debug_token ownership,
                                   #   register, subscribe, details fetch), wa_app_identity, set_connection_status,
                                   #   health check, disconnect
wa_connect_routes.py               # NEW: /api/whatsapp/connect/* blueprint
wa_standard_templates.py           # NEW: catalog, placeholder validation, renumbering -> components + param_map
whatsapp_inbox.py                  # record echo (sender='phone'), edit/revoke, ai_paused; _post_message error hook
migrations/versions/<rand>_whatsapp_auto_connect.py   # NEW
.github/workflows/scheduled-jobs.yml                  # + hourly whatsapp_connection_health
frontend/src/components/SettingsView.js              # Messaging tab: method picker + status card replacing the
                                                     #   always-on credential form (manual form kept under "Manual")
frontend/src/components/WhatsAppConnectCard.js       # NEW: method cards, FB SDK loader, postMessage listener,
                                                     #   status/health/events, reconnect/disconnect
frontend/src/components/StandardTemplatesDialog.js   # NEW: editable templates with variable chips + validation
frontend/src/context/AppContext.js                   # + API methods for connect/* and standard-templates
tests/test_whatsapp_connect.py                       # NEW
tests/test_whatsapp_echoes.py                        # NEW
tests/test_whatsapp_standard_templates.py            # NEW
tests/test_whatsapp_connection_health.py             # NEW
tests/fixtures/meta_*.json                           # NEW: captured payloads from the spike
```

**Structure Decision**: Follow the existing pattern from the WhatsApp Inbox work (`whatsapp_inbox.py` + `whatsapp_inbox_routes.py`). New logic goes into its own modules, and `app.py` only gets the model columns, webhook dispatch, and small hooks into existing functions. This keeps the already large `app.py` diff reviewable.

## Delivery Slices (inform /speckit-tasks)

| # | Slice | Covers | Can ship alone? |
|---|---|---|---|
| 0 | **Meta spike** (quickstart §0) plus starting Meta business verification and app review | VERIFY items | n/a. Starts now; Meta review time is the critical path |
| 1 | Migration, models, `wa_app_identity`, settings GET/POST guards, webhook HMAC via resolver, platform verify token | FR-017/018, R-4/R-5/R-9 | Yes: invisible, manual tenants unchanged |
| 2 | `wa_connect` onboarding + `/connect/*` routes + method picker and status card UI (embedded and coexistence) | US1, US2, FR-001–011, 016, 019 | Yes, behind the env-var flag |
| 3 | Echoes, history ignore, coexistence auto-reply default | FR-012/013/014 | Yes |
| 4 | Health: `account_update`, send-error detection, hourly job, alerts, reconnect and disconnect, event log | US4, FR-020–023 | Yes |
| 5 | Standard templates catalog + editor + `param_map` send ordering | FR-015–015d | Yes; also useful to manual tenants |

## Risks

| Risk | Mitigation |
|---|---|
| Meta app review or business verification is slow or rejected | Start it now (slice 0). Everything ships behind `META_APP_ID` being set, and manual configuration is unaffected. |
| Coexistence not available for +961 | `META_COEXISTENCE_ENABLED=false` hides it; the embedded and manual methods remain. Confirmed in the spike with DeltaNet's own number. |
| Business token silently expires (lifetime undocumented) | Error 190 detection + hourly health check → Needs attention + Reconnect (R-7) |
| `param_map` ordering bug sends values in the wrong slots | Unit test pins the catalog `variables` against the send mapping. Rows without a map keep the legacy order. Verify with a real payment in quickstart §2.6. |
| Duplicate WABA rows already in production break the duplicate guard | The guard checks only *other tenants*. No DB unique index in this migration (data-model §1). |

## Complexity Tracking

No violations.
