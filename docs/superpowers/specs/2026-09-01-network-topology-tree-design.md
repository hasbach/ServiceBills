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

For `device_type = 'vsol_olt'` rows the existing credential columns are reused rather than duplicated, because SNMP's credential is a single community string:

- `api_port` = **161** (the SNMP UDP port), not 8728. Meaningful for both device types, just a different default per type.
- `password` (already `EncryptedString`) holds the **SNMP community string**. It is a credential and belongs encrypted at rest exactly like a router password; a second secret-bearing column for the same purpose would be redundant. The device form labels this field "SNMP community" when the selected `device_type` is `vsol_olt`.
- `username` and `use_tls` are unused for `vsol_olt` rows — the same way `service_name` is already unused/irrelevant for `UpstreamProvider` rows that don't need it.

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

New module built on **`pysnmp`** (7.1.29, already installed — asyncio API: `get_cmd` / `bulk_walk_cmd`, `await UdpTransportTarget.create(...)`).

> **Revised 2026-09-04.** This section originally specified Playwright browser automation against the OLT's web UI, because a probe during the design session found SNMP completely unreachable. The user has since added a permit rule for SNMP on the OLT, and a re-probe confirms it now answers: `sysDescr.0 = V1600D`, `sysName.0 = epon-olt`, enterprise OID `1.3.6.1.4.1.37950` (V-SOL), responding on UDP 161 to both SNMPv1 and v2c. SNMP is not merely viable but strictly better here — see "Why SNMP beats the scrape" below. The Playwright approach is abandoned.

```python
def get_olt_status(server) -> (ok: bool, value)
```

- Walks **one** table — `1.3.6.1.4.1.37950.1.1.5.12.1.12.1.<col>.<row>`, flat row index `0..N-1` — over SNMPv2c, community from `server.password`, host `server.host`, port `server.api_port` (161).
- Columns consumed:

  | col | field |
  |-----|-------|
  | 2 | PON port |
  | 3 | ONU id within that port |
  | 5 | status — `1` = online, `0` = offline |
  | 6 | MAC address, already colon-formatted |
  | 7 | ONU model (`V2801D`, `V2801S`, `unknow`) |
  | 10 | description — staff label, usually the customer name; literal `NULL` when unset |
  | 13 | distance in metres (`0` when offline) |
  | 14 | capability (`1GE`, `1GE+CATV`, `4GE+1POTS+2WiFi`) |

- Returns `(True, [{"pon_port": str, "onu_id": str, "status": "online"|"offline", "mac_address": str, "description": str|None, "model": str, "distance_m": int}, ...])` — a flat list across every PON port. `description` is normalised to `None` when the device reports the literal string `NULL`.
- On failure (timeout, wrong community, unreachable host): `(False, message)` — never raises, same contract as every other connector module in this codebase.

**Deduplication (required — the device reports stale rows).** Verified against the live OLT: 87 ONU rows carry only 75 unique MACs. Twelve MACs appear on two rows each, because moving an ONU between PON ports leaves the old authorization entry behind — 9 of PON8's 10 rows are ghosts of PON1 ONUs (offline, `description = NULL`, `distance = 0m`). Since customer-to-ONU matching is by MAC, an undeduplicated list would let the wrong row decide a customer's status. The connector therefore collapses rows by MAC before returning:

1. Prefer the row with `status = online`.
2. If all candidates are offline, prefer the one with a non-`NULL` description.
3. If still tied, prefer the greatest distance, then the lowest PON port — deterministic, so the tree does not flicker between checks.

**Why SNMP beats the scrape it replaces:** one request returns *offline* ONUs too. The web UI scrape would have had to page through each PON port's ONU list separately, and the offline rows are precisely the ones the Network Tree exists to surface. It also removes the second headless Chromium process entirely, which makes the original memory-ceiling argument for a separate semaphore moot.

**Concurrency:** keep a `threading.Semaphore(1)` (`_olt_check_semaphore`), still independent from `_upstream_sync_semaphore`, but for a narrower reason than before: an SNMP walk against this device is cheap in memory yet not instant, and the device does time out under sustained walking (see below). Serialising checks avoids piling concurrent walks onto one OLT. Same acquire-with-timeout-and-clear-error pattern as `_sync_customer_upstream_status_core`: a manual "Check Now" fails fast with "Too many OLT checks already in progress — try again shortly" rather than hanging.

**Two device quirks the implementation must respect:**

- **Walk only that one table, never the enterprise root.** A full walk of `1.3.6.1.4.1.37950` is ~16k varbinds and timed out partway during the probe.
- **The OLT has no real clock.** Registration/deregistration timestamps read `1970/01/2x` — they are uptime-relative, not wall-clock (no NTP/RTC configured). This spec consumes none of them; if a later slice does, it must not present them as absolute times.

## Seeding `Customer.onu_mac_address` from OLT labels

71 of the 87 ONUs already carry a staff-entered label that is usually the customer's name (`MoussaGhadir`, `villaEid`, `TalebCaffe`, ...). Rather than have staff hand-type ~71 MAC addresses, the build includes a **review-then-apply matcher**:

- A one-off screen runs `get_olt_status()`, fuzzy-matches each ONU `description` against the tenant's `Customer` names, and lists the proposed customer-to-MAC links with their match confidence.
- Nothing is written until the user approves. Each proposed link is individually accept/reject-able, and unmatched ONUs and unmatched customers are both listed so the gaps stay visible.
- Applying writes `Customer.onu_mac_address` for the accepted rows only. The field stays manually editable afterwards; this is a convenience for the initial backfill, not an ongoing sync.

## Network Tree page

New page, consuming Layers 1 and 3 together:

1. Load every `NetworkDevice` for the tenant, build the tree from `parent_device_id` (root = device with `parent_device_id = null`; today that's always the CCR).
2. For each device of `device_type = 'vsol_olt'` in the tree, on click/on-demand (no automatic polling, matching the existing on-demand-only convention), call `vsol_olt.get_olt_status()` to get the live ONU list for that OLT.
3. For each ONU returned (already deduplicated by MAC in the connector), look up every `Customer` row where `onu_mac_address` matches (0, 1, or many). ONUs with no matching customer still render, labelled with the OLT's own `description`, so unlinked ONUs are visible rather than hidden.
4. Render the full tree: CCR → OLT → ONU → customer name(s), each node colored by status (green = online/reachable, red = unreachable/offline, matching the color convention already used for `last_status` chips elsewhere in the app).

The passive/unmanageable links in DeltaNet's real topology (splitter, unmanaged switch) are not represented as nodes — they were never modeled as `NetworkDevice` rows and aren't reported by any connector, consistent with `[[reference_deltanet_network_topology]]`'s note that they're permanent blind spots, only inferable by correlation (a splitter fault would show up as several unrelated ONUs going dark at once — not something this page attempts to detect in this pass).

## Out of scope (explicitly deferred)

- **Layer 2: local on-prem agent + outbound tunnel.** Both connectors in this spec (`mikrotik.py`, `vsol_olt.py`) run directly inside the Flask process, reaching devices over the local network the process itself is on — exactly like the CCR connector already does today. This only works because ServiceBills is being run locally/on-prem for this work right now. Once ServiceBills needs to run on Render for this feature, Render cannot reach DeltaNet's private LAN IPs (192.168.x.x) at all, regardless of credentials — a local agent relaying calls through an outbound tunnel becomes a real, separate prerequisite at that point, not built here.
- **Layer 4: fault-diagnosis engine.** This spec is pure topology + visibility, same as the prior CCR-only slice. No logic here decides "is this actually the customer's fault or ours."
- **EDFA monitoring.** Confirmed out of scope by the user: it's a passive optical amplifier merging two wavelengths, nothing to diagnose.
- **Customer-behind-ONU MAC table** (the ONU's own downstream customer-device MAC table, confirmed available via the OLT's web UI "MAC Info" page). Not pulled in this pass — the Network Tree page only needs ONU-level status, not the deeper per-customer-device MAC list. Worth adding later if a real need for it shows up.
- **Scheduled/periodic polling of the OLT or CCR**, and any history table — on-demand only, matching the existing convention.

## Testing

- Model-level: `NetworkDevice.device_type`/`parent_device_id` and `Customer.onu_mac_address` covered by a small extension to the existing model tests (default value, self-FK round-trip, migration up/down).
- `vsol_olt.py`: unit tests with the pysnmp walk monkeypatched to a fake returning varbinds recorded from the real device — no real OLT involved. Covers: multi-PON-port enumeration, ONU row parsing (online/offline/MAC/description/`NULL` normalisation), the MAC-deduplication rule including the all-offline tie-breaks, and that timeout/wrong-community failures return `(False, message)` without raising.
- Label matcher: unit tests over the fuzzy-match proposal step (exact match, near match, no match, one OLT label colliding with two customers), asserting nothing is written until apply.
- Network Tree endpoint: ad hoc smoke test (created, exercised, then deleted) confirming the tree assembles correctly from `parent_device_id` links and that ONU-to-customer matching via `onu_mac_address` works for the many-customers-to-one-ONU case.
- Concurrency: a `test_olt_semaphore_returns_clear_error_when_exhausted`-style test mirroring the existing `test_concurrency_semaphore_returns_clear_error_when_exhausted`, confirming the new `_olt_check_semaphore` is genuinely independent from `_upstream_sync_semaphore` (exhausting one doesn't block the other).
