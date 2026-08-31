# Network Device Health Monitoring — Design

## Problem

DeltaNet (and, longer-term, other network-owning tenants) has no visibility inside ServiceBills into whether their own network hardware is actually up. The only network integration that exists today is [Network Enforcement](2026-08-12-network-enforcement-design.md)'s `MikrotikServer`/`UpstreamProvider` concepts, both scoped to *customer*-facing actions (PPPoE secret suspend/unsuspend, upstream renewal) — neither answers "is our core router even reachable right now."

This is the first slice of a larger, deliberately-deferred vision: a network topology/fault-diagnosis system that could eventually triage "customer says internet is down" by chaining billing status, upstream RADIUS session status, and DeltaNet's own hardware health (OLT/ONU/EDFA/CCR). That full vision — a local on-prem agent, multi-vendor SNMP/SSH connectors, a fault-diagnosis engine, and eventually an AI diagnostic agent — is explicitly **not** being built here. This spec is only the smallest useful, shippable piece of it: on-demand health visibility for the one device already reachable via an existing, working adapter.

## Scope decisions

- **Tenant-scoped from day one.** Follows the existing convention every tenant-owned model in this codebase already uses (`tenant_id` FK, `TENANT_OWNED_MODELS`, `tenant_query(...)` reads) — see `UpstreamProvider`/`MikrotikServer` in the network-enforcement spec. No cost to doing this correctly now, even though only DeltaNet exercises it initially.
- **RouterOS-only, no multi-vendor abstraction.** Only one real device is in hand (DeltaNet's MikroTik CCR). `mikrotik.py` already made this call for Concept B — "plain functions, not a class hierarchy, since there's exactly one implementation." A vendor-plugin interface built against zero second data point would be guessed, not designed. When a second real vendor (e.g. DeltaNet's OLT, once its model is known) shows up, that's the right moment to extract an interface.
- **Separate table from `MikrotikServer`, not an extension of it.** `MikrotikServer` is specifically about `local_mikrotik`-mode PPPoE secret management, gated by `network_mode` and linked from `Customer`. DeltaNet's CCR runs in bridge mode (it aggregates 4 upstream links and passes PPPoE through to the upstream's RADIUS server — it doesn't run local PPPoE secrets itself), and device-health visibility is a use case any tenant might want regardless of `network_mode`. Keeping the two concerns independent avoids conflating "this router runs my PPPoE" with "I just want to watch this router's health."
- **On-demand checks only, no scheduled polling.** Matches `MikrotikServer`'s existing "Test Connection" pattern. No new background-job infra, no history table. A later phase can add periodic polling + history if "was it down at 3am" becomes a real need.
- **Interfaces shown generically, labeled by staff after the fact.** Nobody has confirmed the exact RouterOS interface-name-to-upstream mapping (VLAN vs physical port) ahead of time. Rather than guess or hardcode it, the health check lists whatever interfaces RouterOS reports; staff assigns a friendly label to each one via the UI after seeing the real names from a first "Check Now."

## Data model

```python
class NetworkDevice(db.Model):
    id
    tenant_id          # FK -> tenant.id, NOT NULL, indexed
    name                # String(100), NOT NULL — e.g. "Core CCR"
    host                # String(255), NOT NULL
    api_port            # Integer, NOT NULL, default 8728 (8729 when use_tls)
    use_tls             # Boolean, default False
    username             # String(100), NOT NULL
    password             # db.Column(EncryptedString, nullable=False) — same crypto.py pattern as MikrotikServer/UpstreamProvider/WhatsAppSettings
    status                # String(20), default 'active'
    last_checked_at       # DateTime, nullable
    last_status            # String(20), nullable — 'online' | 'unreachable' | 'auth_failed'
    interface_labels        # JSON, default {} — maps raw RouterOS interface name -> staff-assigned friendly label, e.g. {"ether1": "thglobal", "sfp1": "smart networks"}
```

Added to `TENANT_OWNED_MODELS`. No link from `Customer` — this is device-level, not customer-level, visibility. One Alembic migration (`flask db migrate`, following the existing hash+description naming convention), no dialect guard needed (`render_as_batch=True` already configured app-wide).

## Connector: extend `mikrotik.py`

One new function, reusing the existing `_connect`/`_safe_close`/`_mark_checked`/`_classify_error` helpers already in [mikrotik.py](../../../mikrotik.py) — same RouterOS library (`librouteros`), same connection pattern, no new module needed:

```python
def get_device_health(server) -> (ok: bool, value)
```

- On success: `value` is a dict — `identity` (from `/system/identity`), `uptime` (from `/system/resource`), and `interfaces`: a list of `{name, running, disabled}` read from `/interface`, in whatever order/set RouterOS reports.
- On failure: same contract as every other function in the file — never raises; catches `_CONNECTION_ERRORS` (`OSError`, `LibRouterosError`); returns `(False, message)`.
- Side effect: sets `server.last_checked_at`/`server.last_status` (`'online'` / `'unreachable'` / `'auth_failed'`, same classification logic as `test_connection`) — caller commits.

Interface labeling happens entirely in the app layer (attaching `server.interface_labels.get(name)` to each interface dict when building the API response) — `mikrotik.py` itself stays unaware of labels, consistent with it being a pure RouterOS adapter with no app-level concepts.

## API

- `POST /api/network-devices/<id>/check-now` — calls `get_device_health`, commits `last_checked_at`/`last_status`, returns identity/uptime/interfaces (each interface annotated with its label if one is set).
- Standard CRUD: `GET/POST /api/network-devices`, `PUT/DELETE /api/network-devices/<id>` — same shape as `MikrotikServer`'s existing endpoints.
- `PATCH /api/network-devices/<id>/interface-labels` — body `{interface_name, label}`, updates one entry in `interface_labels` (merge, not replace-whole-dict).

All endpoints tenant-scoped via `tenant_query(...)`, matching every other model in this app.

## UI

- New "Network Devices" nav entry (not gated by `network_mode` — independent of PPPoE mode, since device-health visibility is orthogonal to how a tenant handles customer network links).
- List view: devices with name, host, status, last checked, last status badge.
- Create/edit form: name, host, port, use_tls, username, password (masked) — same style as the `MikrotikServer` form.
- Device detail view: identity, uptime, "Check Now" button (calls `check-now`, shows result inline), and a table of interfaces — raw name, running/disabled badge, an editable label field per row (saves via the `interface-labels` endpoint).

## Out of scope (explicitly deferred)

- OLT/EDFA/ONU monitoring — no connector exists, vendor/model for DeltaNet's OLT and EDFA still unconfirmed. Later phase, once known.
- Scheduled/periodic polling and health history — on-demand only in this slice.
- The local on-prem agent + outbound tunnel from the original vision — not needed here since the CCR is reachable via the same RouterOS API path `MikrotikServer` already uses; only becomes necessary once monitoring extends to devices that are LAN-local-only and not otherwise reachable.
- Fault-diagnosis / triage logic chaining billing + upstream RADIUS + hardware status — this slice is pure visibility, no diagnosis.
- Any AI agent / tool-calling layer.
- Multi-vendor connector abstraction — RouterOS-only for now; revisit when a second real vendor is in hand.
- Platform-wide (non-DeltaNet) rollout — the data model is tenant-scoped by convention, but this is being built and validated against DeltaNet only; no other tenant is expected to use it yet.

## Testing

- `tests/test_mikrotik.py`-style unit tests for `get_device_health`: mocked `Api` double, asserting identity/uptime/interface parsing, and that connection/protocol failures return `(False, message)` rather than raising (same pattern as the file's existing 15 tests).
- Ad hoc endpoint smoke tests (created, exercised, then deleted) for CRUD, `check-now`, and `interface-labels`, consistent with how `MikrotikServer` endpoints were verified in the network-enforcement spec.
- Tenant isolation relies on the same `tenant_query(...)` pattern as every other tenant-owned model; no dedicated new test, consistent with existing coverage conventions.
