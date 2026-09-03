# Network Topology Tree — Design

## Problem

The first slice ([2026-09-01-network-device-health-monitoring-design.md](2026-09-01-network-device-health-monitoring-design.md)) gave DeltaNet on-demand health visibility for one device (the CCR), with a flat, unrelated `NetworkDevice` list. It does not yet represent DeltaNet's actual physical chain (CCR → OLT → ONU → customer) as a connected structure, and does not cover the OLT at all.

This spec is Layer 1 (generalized topology data model) and Layer 3 (OLT connector + Network Tree page) of the broader network AI diagnostic agent vision (see `[[project_network_ai_diagnostic_agent_vision]]` in memory). Layer 2 (local on-prem agent + outbound tunnel to production) and Layer 4 (fault-diagnosis engine) are explicitly not part of this pass — see Out of Scope.

## Layer 1: extend `NetworkDevice` and `Customer`

Two new columns on the existing `NetworkDevice` model (from the prior spec), no new table:

```python
device_type       # String(20), NOT NULL, default 'mikrotik_ccr' — 'mikrotik_ccr' | 'vsol_olt'.
                   # Selects which connector module handles this row: mikrotik.py for
                   # 'mikrotik_ccr', vsol_olt.py for 'vsol_olt'. Mirrors the existing
                   # UpstreamProvider.product convention (string discriminator, not an enum table).
                   # The column default exists only to backfill the one existing row
                   # (DeltaNet's already-created CCR, correctly 'mikrotik_ccr') during the
                   # migration. Going forward, the create endpoint requires the caller to
                   # specify device_type explicitly (UI: a required dropdown, CCR / OLT) --
                   # it is never silently inferred for a newly created device.
parent_device_id  # Integer, FK -> network_device.id, nullable. The CCR has parent_device_id
                   # = null (it's the root of the tree); the OLT's parent_device_id = the
                   # CCR's id. Self-referential FK, following the same pattern as any other
                   # nullable FK column in this codebase.
```

For `device_type = 'vsol_olt'` rows, `api_port` and `use_tls` are simply unused (the OLT is reached over plain HTTP for its web UI, not the RouterOS binary API) — the same way `service_name` is already unused/irrelevant for `UpstreamProvider` rows that don't need it.

One new column on `Customer`:

```python
onu_mac_address   # String(20), nullable — the MAC address of the ONU serving this customer,
                   # as reported by the OLT's ONU list. Many customers can share the same
                   # ONU (confirmed: DeltaNet's ONUs are transparent bridges with no per-customer
                   # split at the ONU level, and multiple customers can be wired behind one
                   # ONU), so this is a plain many-to-one string field, not a unique constraint
                   # or a separate join table — the same shape as the existing
                   # mikrotik_server_id/upstream_provider_id link columns, which are also
                   # many-customers-to-one-device.
```

Both `NetworkDevice.device_type`/`parent_device_id` and `Customer.onu_mac_address` are added via one Alembic migration, following the existing hash+description naming convention.

## Layer 3: OLT connector (`vsol_olt.py`)

New module, structurally mirroring `upstream_portal.py` (Playwright-based browser automation), not `mikrotik.py` (no RouterOS-style API exists for this OLT — confirmed by a live SNMP probe against the real device returning no response at all on UDP 161 despite SNMPv1/v2 community strings being configured in its web UI, so SNMP is not a viable path here; the web UI is the only confirmed source of this data).

```python
def get_olt_status(server) -> (ok: bool, value)
```

- Logs into the OLT's web UI at `http://{server.host}` using `server.username`/`server.password` (the same encrypted-at-rest fields `NetworkDevice` already has).
- Enumerates every PON port from the "ONU list" page's Port ID dropdown (VSOL's real UI, confirmed via screenshot: a dropdown listing `PON1`, `PON2`, ... for however many PON ports this OLT model has).
- For each PON port, scrapes the ONU table: ONU ID (e.g. `EPON0/1:2`), Status (`Online`/`Offline`), MAC Address, Description (staff-entered label, often already the customer's name in DeltaNet's real OLT — useful context but not used programmatically here).
- Returns `(True, [{"pon_port": str, "onu_id": str, "status": "online"|"offline", "mac_address": str, "description": str}, ...])` on success — a flat list across every PON port on the OLT.
- On failure (login failure, timeout, navigation error): `(False, message)` — never raises, same contract as every other connector module in this codebase (verified by an equivalent `test_unreachable_router_never_raises`-style test).

**Concurrency:** a **separate** `threading.Semaphore(1)` (`_olt_check_semaphore`), independent from the existing `_upstream_sync_semaphore` used by upstream-portal automation. Deliberately not shared: this avoids touching the existing, already-tested upstream-portal concurrency code at all. Worst case this allows 2 concurrent headless Chromium instances (one upstream sync + one OLT check) instead of 1 — a modest, accepted memory increase over the current single-semaphore ceiling. Same acquire-with-timeout-and-clear-error pattern as `_sync_customer_upstream_status_core`: a manual "Check Now" click fails fast (~30s) with "Too many OLT checks already in progress — try again shortly" rather than hanging.

## Network Tree page

New page, consuming Layers 1 and 3 together:

1. Load every `NetworkDevice` for the tenant, build the tree from `parent_device_id` (root = device with `parent_device_id = null`; today that's always the CCR).
2. For each device of `device_type = 'vsol_olt'` in the tree, on click/on-demand (no automatic polling, matching the existing on-demand-only convention), call `vsol_olt.get_olt_status()` to get the live ONU list for that OLT.
3. For each ONU returned, look up every `Customer` row where `onu_mac_address` matches (0, 1, or many).
4. Render the full tree: CCR → OLT → ONU → customer name(s), each node colored by status (green = online/reachable, red = unreachable/offline, matching the color convention already used for `last_status` chips elsewhere in the app).

The passive/unmanageable links in DeltaNet's real topology (splitter, unmanaged switch) are not represented as nodes — they were never modeled as `NetworkDevice` rows and aren't reported by any connector, consistent with `[[reference_deltanet_network_topology]]`'s note that they're permanent blind spots, only inferable by correlation (a splitter fault would show up as several unrelated ONUs going dark at once — not something this page attempts to detect in this pass).

## Out of scope (explicitly deferred)

- **Layer 2: local on-prem agent + outbound tunnel.** Both connectors in this spec (`mikrotik.py`, `vsol_olt.py`) run directly inside the Flask process, reaching devices over the local network the process itself is on — exactly like the CCR connector already does today. This only works because ServiceBills is being run locally/on-prem for this work right now. Once ServiceBills needs to run on Render for this feature, Render cannot reach DeltaNet's private LAN IPs (192.168.x.x) at all, regardless of credentials — a local agent relaying calls through an outbound tunnel becomes a real, separate prerequisite at that point, not built here.
- **Layer 4: fault-diagnosis engine.** This spec is pure topology + visibility, same as the prior CCR-only slice. No logic here decides "is this actually the customer's fault or ours."
- **EDFA monitoring.** Confirmed out of scope by the user: it's a passive optical amplifier merging two wavelengths, nothing to diagnose.
- **Customer-behind-ONU MAC table** (the ONU's own downstream customer-device MAC table, confirmed available via the OLT's web UI "MAC Info" page). Not pulled in this pass — the Network Tree page only needs ONU-level status, not the deeper per-customer-device MAC list. Worth adding later if a real need for it shows up.
- **SNMP.** Confirmed unreachable on the real device during this design session (no response on UDP 161). Not revisited unless something changes on the device/network side.
- **Scheduled/periodic polling of the OLT or CCR**, and any history table — on-demand only, matching the existing convention.

## Testing

- Model-level: `NetworkDevice.device_type`/`parent_device_id` and `Customer.onu_mac_address` covered by a small extension to the existing model tests (default value, self-FK round-trip, migration up/down).
- `vsol_olt.py`: unit tests mirroring `tests/test_upstream_portal.py`'s pattern exactly — `sync_playwright` monkeypatched to a fake Playwright/Browser/Page double, no real browser or real OLT involved. Covers: multi-PON-port enumeration, ONU row parsing (online/offline/MAC/description), and that login/navigation failures return `(False, message)` without raising.
- Network Tree endpoint: ad hoc smoke test (created, exercised, then deleted) confirming the tree assembles correctly from `parent_device_id` links and that ONU-to-customer matching via `onu_mac_address` works for the many-customers-to-one-ONU case.
- Concurrency: a `test_olt_semaphore_returns_clear_error_when_exhausted`-style test mirroring the existing `test_concurrency_semaphore_returns_clear_error_when_exhausted`, confirming the new `_olt_check_semaphore` is genuinely independent from `_upstream_sync_semaphore` (exhausting one doesn't block the other).
