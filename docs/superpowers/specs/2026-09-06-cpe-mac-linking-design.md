# CPE-MAC customer linking, with last-seen-ONU memory — design

**Date:** 2026-09-06
**Status:** approved, not yet implemented
**Builds on:** `2026-09-01-network-topology-tree-design.md` (ONU inventory, label
matcher), `2026-09-04-network-agent-layer-2-design.md` (the on-prem relay), and
`2026-09-05-network-tree-v2-design.md` (the tree that renders the result)

## Why

A customer is linked to the network today by `Customer.onu_mac_address` — the
**ONU's own** MAC. DeltaNet's ONUs are transparent bridges, so that address is
**shared by every customer behind it**. It answers "which ONU" only if a human
already knew the answer and typed it in, and it cannot distinguish the four
customers sitting on one ONU.

The OLT already knows better. Its MAC-learning table records the address of
every device it has seen *behind* each ONU — the customer's own router. That
address is unique per customer, and the OLT will tell us which ONU it sits
behind. So staff never need to know which ONU anyone is on: record the router's
MAC once, and the OLT places the customer.

Confirmed by probing the real V-SOL V1600D on 2026-09-05, not assumed:

- `dot1dTpFdbTable` (`1.3.6.1.2.1.17.4.3.1.1` address, `.1.2` port) returns
  **316 learned MACs**.
- **186 of them sit on bridge port 10 = `GE0/10`, the uplink** — upstream
  traffic, attributable to nobody.
- The remaining ~130 spread across **53 ONU-level bridge ports** plus one
  PON-level port.
- **Bridge port number == `ifIndex`**, and `ifName` for those indexes reads
  `EPON01ONU2 MoussaGhadir` — PON, ONU number and the OLT's own label in one
  string. **54 of 54** non-uplink ports resolved.
- 76 ONUs, 62 online, but only 53 have learned MACs: an idle ONU has none.
- The OLT has no RTC/NTP, so nothing usable comes from its own timestamps.

## Decisions the owner made, which this spec implements

1. **The CPE MAC is the link; the ONU field becomes the remembered location.**
   Staff record only the customer's router MAC. Locate resolves it to an ONU
   and writes that ONU's MAC into the existing `onu_mac_address`.
2. **Locating is a deliberate button, not a side effect of every OLT check.**
   Nothing rewrites customer records in the background.
3. **No matcher UI.** Just the field on the customer form; staff fill it in as
   they install or visit. Backfilling the existing ~300 happens gradually.
4. **Remembered placements are marked**, and the run is dated.

## Scope

**In:** two customer columns and their migration, the CPE MAC on the customer
form, a new SNMP walk in `vsol_olt.py`, the matching agent operation, the
Locate job and its apply step, the tree's remembered-placement marker, and the
timestamp fix below.

**Out:** any bulk assignment UI (decision 3), and any change to how the tree
itself is drawn — `buildTopologyTree` already renders whatever `customers`
array each ONU carries, so this changes what fills that array, not the tree.

## The timestamp fix

Every absolute timestamp in the network pages is **displayed three hours behind**
for this user. The backend emits UTC — `datetime.utcnow().strftime('%Y-%m-%d
%H:%M:%S')` — and the UI prints that string verbatim, so an agent last seen at
14:31 local reads `11:31`. Measured 2026-09-06: local clock `14:31:30`, API
`11:31:30`.

Relative labels are already correct: `describeAge` appends `'Z'` before parsing,
which is exactly the missing step elsewhere.

The fix is a single frontend helper — parse as UTC, render in the viewer's own
zone — applied to every absolute stamp on the network surfaces: the agent chip
and its offline tooltip in both `NetworkTreeView` and
`NetworkDeviceManagementView`, and any device or run stamp shown as a date
rather than an age.

**Deliberately scoped to the network pages.** The same raw-UTC pattern almost
certainly appears on payments, receipts and elsewhere; sweeping the whole app
is a separate, larger change that should not ride along with a feature. Noted
as a follow-up.

## Data model

Two nullable columns on `Customer`, added by one hand-written migration (the
Alembic chain cannot replay on SQLite; tests build schema with `create_all`):

- **`cpe_mac_address`** `String(20)`, indexed, **unique per tenant**. The
  customer's own router. A MAC identifies one physical device, so two customers
  claiming it is a data error and is rejected at entry with a message naming the
  other customer. Enforced by a application check for the friendly error and a
  `UniqueConstraint(tenant_id, cpe_mac_address)` as the backstop — the same
  belt-and-braces shape `NetworkAgent.tenant_id` uses.
- **`onu_last_seen_at`** `DateTime`, nullable. When this customer's CPE was last
  located. Null means never.

`onu_mac_address` is unchanged in type and name. Its **meaning** becomes "the
ONU we last saw this customer behind": written by Locate, still editable by
hand, and never cleared automatically.

## The connector

New in `vsol_olt.py`, alongside `get_olt_status` and following its contract
exactly — `(ok, value)`, never raises:

```
get_cpe_locations(server) -> (True, {cpe_mac: {"pon_port", "onu_id", "onu_mac"}})
                          |  (False, "human-readable message")
```

Three walks, each capped the way `get_olt_status` caps its own, because walking
the enterprise root on this device times out:

1. `dot1dTpFdbPort` → `{mac: bridge_port}`
2. `ifName` → `{ifIndex: name}`
3. the existing ONU table → `{(pon, onu): onu_mac}`

**The uplink exclusion is load-bearing.** Only ports whose `ifName` matches the
ONU shape `EPON<pon>ONU<n>` are kept. Without it, the 186 MACs on `GE0/10`
would resolve to nothing meaningful and could place a majority of customers on
a phantom node. A port that resolves to a PON interface (`EPON0/3`) rather than
an ONU is dropped for the same reason: it identifies a tree, not a leaf.

MACs are canonicalised through the existing `_canonical_mac`, so a CPE recorded
with hyphens still matches one the OLT reports with colons.

## Relay and authorization

A new agent operation, `cpe_locations`, added to `AGENT_OPERATIONS` and to the
agent's own `ALLOWED_OPERATIONS`. It is a read, so it fits the agent's
read-only charter unchanged.

**The agent never writes customer records.** It authenticates as a device relay,
not as a person; letting it rewrite customer rows would hand it a privilege it
was deliberately not given. It returns the map only.

So Locate is two steps, mirroring the existing label matcher:

1. `POST /api/network-tree/olt/<id>/locate-customers` — creates the job.
   `admin_or_finance_required`.
2. `POST /api/network-tree/olt/<id>/locate-customers/apply` with the completed
   `job_id` — reads that job's stored result and writes the placements, acting
   as the logged-in user. `admin_or_finance_required`.

Employee and collector keep read access to the tree and are refused both — the
same boundary the label matcher already draws.

## Applying a locate

For each `(cpe_mac, onu)` in the job's result, find the tenant's customer whose
`cpe_mac_address` canonicalises to that MAC, then set `onu_mac_address` to the
ONU's MAC and `onu_last_seen_at` to now.

- A CPE matching no customer is ignored — plenty of devices are on the network
  that nobody has recorded.
- A customer whose CPE was **not** seen is left completely alone. That is the
  memory: their previous placement and its timestamp both stand.
- A customer whose CPE now resolves to a **different** ONU is moved. That is the
  point — it is how the tree follows a customer who was physically relocated.

The response reports counts: located, moved, and unmatched — so the operator
learns something from pressing the button.

## What the operator sees

*Locate Customers* sits beside *Match Labels* on the OLT card, admin/finance
only, disabled with the existing tooltip when the agent is offline.

In the tree, a customer located in the most recent run renders as today. One
whose CPE was not seen carries a quiet `last seen 4 Sep`. That distinction is
**derived, not stored on the device**: compare the customer's `onu_last_seen_at`
against the `finished_at` of the newest completed `cpe_locations` job for that
OLT. Older means remembered.

## Failure behaviour

- The walk fails: the job carries the error, exactly as an OLT refresh does. No
  customer record is touched — apply is a separate call and simply is not made.
- A malformed stored result (agent mode posts whatever it posts): apply
  validates the shape and skips entries it cannot read, rather than raising.
  The existing `_validate_agent_result` boundary gains the new operation.
- Applying twice is harmless: it is idempotent for an unchanged network, only
  refreshing timestamps.

## Testing

**Connector** — against varbinds recorded from the real OLT: an uplink MAC is
excluded, a PON-level port is excluded, an ONU with several CPEs yields several
entries, an unresolvable port is skipped, and a timeout returns `(False, msg)`
without raising.

**Apply** — a CPE matching no customer is ignored; an unseen customer keeps both
their placement and their timestamp; a moved CPE updates the placement; a
second apply of the same result changes nothing but timestamps; malformed
entries are skipped.

**Authorization** — employee and collector get 403 on both endpoints; the tree
itself stays readable to them.

**Uniqueness** — a second customer given a CPE already held by another is
rejected with a message naming the holder, and the constraint holds if the
check is raced.

**Timestamps** — the helper renders a known UTC string in a known zone, and the
agent chip shows local time rather than UTC.

## Risks

- **The MAC table is a live snapshot.** A customer whose router has been off
  since before the last locate has never been placed at all, and will show as
  unlinked rather than remembered. Only recording their CPE and locating while
  they are online fixes that; nothing in this design can infer it.
- **A CPE behind a customer's own router is invisible.** The OLT learns the
  MAC facing it. If a customer puts their own switch or a second router in
  front, the address recorded on the customer record must be the one the OLT
  actually sees, not whatever is printed on the box they were handed.
- **Locate is admin/finance only**, so the field staff most likely to know a
  router's MAC may not be the ones able to press the button. If that turns out
  wrong in practice, widening it is a one-line change — but widening writes to
  customer records deserves its own decision, not a default.
