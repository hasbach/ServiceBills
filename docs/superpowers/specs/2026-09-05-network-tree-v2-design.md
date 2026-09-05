# Network Tree v2 — design

**Date:** 2026-09-05
**Status:** approved, not yet implemented
**Supersedes the tree rendering in:** `2026-09-01-network-topology-tree-design.md` (that spec's
data model is unchanged; only the page's presentation and its data-loading strategy change here)

## Why

Layers 1–3 and the on-prem agent (Layer 2) all shipped to production on 2026-09-04/05. What
exists today is a flat indented list: CCR, then OLT nested under it, then — only after clicking
*Load ONUs* — a long unstructured run of ONU rows with their customers.

Three things are missing for the page to be the "full view of the network" it is meant to be:

1. **The PON port is invisible.** DeltaNet's ONUs hang off eight PON ports. The connector already
   reports `pon_port` per ONU and the page throws it away, so "is PON8 dark?" — the single most
   useful question during an outage — cannot be answered.
2. **The CCR's own ports are invisible.** `mikrotik.get_device_health` returns every interface with
   `running`/`disabled`, and the device row stores staff-assigned labels ("MYISP", "TO OLT"), but
   the tree page never fetches or shows them. Which upstream is down is not visible here.
3. **The page opens empty.** Nothing renders until a device check completes, so the common case —
   glance at the network — costs a 13-second SNMP walk.

## Scope

**In:** the tree's structure and rendering, the CCR ports branch, instant-render from the last
known result, search, and widening the MAC-address validator to accept hyphen and dot separators.

**Out, deliberately:** linking customers by their CPE MAC (the OLT's MAC-learning table) and
remembering the last ONU a customer was seen behind. That is a full-stack feature — new customer
field, two new SNMP walks, agent changes, a migration, and a matcher UI — and gets its own spec.
This tree renders whatever `customers` each ONU carries, so it will not need rebuilding when that
lands.

## Structure

Five levels, top-down:

```
CCR ─┬─ Ports ─── interface (up/down, staff label)
     └─ OLT ───── PON port ─── ONU ─── customer
```

- **Ports** is a synthetic node, a sibling of the OLT under its CCR. It exists only when that CCR
  has a device-health result to show.
- **PON** nodes are derived by grouping the ONU list on `pon_port`. Only PON ports that actually
  have ONUs appear — no new SNMP walk, which is what keeps this change almost entirely frontend.
  An ONU whose `pon_port` is missing or malformed is grouped under a single "Unassigned" node
  rather than dropped; the existing rule that hardware must never silently vanish from this page
  applies to the new levels too.
- Devices with no parent remain roots. `_build_device_tree`'s orphan- and cycle-promotion
  guarantees are untouched.

## Backend

Two changes. Neither touches the schema; there is no migration.

### 1. `GET /api/network-tree` returns each device's last known result

Today the endpoint returns the device skeleton only. It gains, per device node:

```
last_result:      the most recent completed job's result, enriched
last_result_at:   that job's finished_at
```

Completed jobs already persist their full result and are retained for
`NETWORK_AGENT_JOB_RETENTION_DAYS = 7`, so this is a query against data that already exists, not
new storage. The lookup is one indexed query per tenant over `network_agent_job`
(`tenant_id`, `status`, `created_at` — the `ix_network_agent_job_poll` index), taking the newest
`done` job per device.

Enrichment reuses the existing helpers so the shapes match what the page already consumes:
`_resolve_onu_customers` for `olt_status` results, `_with_interface_labels` for `device_health`.
Both are already defensive about malformed stored results and stay that way.

This endpoint keeps `network_view_required()` — employee and collector can see the tree.

### 2. `_validate_mac_address` accepts `-` and `.` separators

Currently `^[0-9a-f]{2}(:[0-9a-f]{2}){5}$` — colons only. Windows displays MACs with hyphens, the
OLT's own web UI uses them in places, and a rejected save reads as a broken feature. The validator
normalises `-` and `.` to `:` before matching, then stores the colon form, so exactly one
representation ever reaches the database.

`_normalize_mac` (the comparison helper) applies the same separator normalisation, so a value
written by some other path — an agent-posted result, a row created before this change — still
compares correctly.

Rejection behaviour is unchanged for anything that is not twelve hex digits in six pairs.

## Frontend

### Components

- **`buildTopologyTree(devices)`** — a pure function turning the endpoint's device list plus each
  device's `last_result` into a node array of `{kind, id, label, meta, status, children}`, where
  `kind` is one of `device | ports | interface | pon | onu | customer`. Pure, so it unit-tests
  without rendering, and it is the only place the level rules live.
- **`TreeNode`** — one recursive presentational component. Draws the card, the status ring, the
  connector into its parent, and the chevron.
- **`NetworkTreeView`** — owns expansion state, search state, and the refresh lifecycle.

### Layout

Top-down. Each node is a card; children sit in a row beneath their parent with orthogonal
connectors fanning down into them.

A level with many siblings **wraps into a grid** rather than growing horizontally: PON3's 31 ONUs
flow into rows of four or five beneath their parent, with a single link into the block. This keeps
the whole tree on one screen with no panning, at the cost of strict one-node-per-column geometry
at the widest level. Below 900px the grid collapses to one card per row.

Collapsed by default. The initial view is CCR → {Ports, OLT} → PON ports with counts
("PON1 — 18 ONUs, 15 up"), around eleven nodes.

### Motion

Light travels down a link as an animated dash — but **only along links whose far end is up**, so a
dark PON or a downed port is visibly dark and still. The motion carries status rather than
decorating the page.

Three constraints, all deliberate:

- Only expanded, on-screen links animate. Never more than about a dozen at once.
- Animation is `stroke-dashoffset` on an SVG path — compositor-friendly, no layout work.
- Everything stops under `prefers-reduced-motion`; status is still fully legible from the ring
  colour and the status dot, because colour is never the only signal.

Cards lift slightly on hover (transform only, no layout shift) and scale to 0.98 on press.

### Data loading

On open: render the cached `last_result` immediately, each device showing how old its data is.
Then, **only if** that device's data is older than **5 minutes** and the agent is online, create a
refresh job and merge the result in when it lands. Without that staleness cap, every visit to the
page would fire a 13-second OLT walk.

Manual *Load ONUs* / *Check Ports* buttons remain, for a deliberate refresh.

### Search

One input filters to matching customers, ONU labels, ONU MACs and interface labels, and
auto-expands the path down to each hit — so a match shows *where* it sits, not merely that it
exists. This is what makes collapse-by-default workable across ~390 nodes.

## Failure behaviour

- **Agent offline:** cached data still renders. The existing offline banner shows and the refresh
  buttons are disabled with the existing explanatory tooltip.
- **A check fails:** the cached tree stays on screen; the error appears on that device's node
  only, via the existing `errorByDevice` mechanism.
- **Malformed stored results:** already tolerated by `_resolve_onu_customers` and
  `_with_interface_labels`; `buildTopologyTree` adds the same tolerance for the new levels (a
  non-dict ONU, a missing `pon_port`, an interface with no name).
- **Out-of-order responses:** the existing per-device sequence guards are kept as they are. They
  exist because a superseded response overwriting a newer one was a real bug here.

## Testing

**Backend**

- `GET /api/network-tree` attaches the newest completed job result per device, and ignores
  pending, failed and expired jobs.
- A device with no jobs returns `last_result: null` rather than being omitted.
- Results stay tenant-scoped: another tenant's job never attaches.
- `olt_status` results arrive with customers resolved; `device_health` results with interface
  labels applied.
- `_validate_mac_address` accepts colon, hyphen and dot forms, stores the colon form, and still
  rejects short, long, non-hex and non-string input.
- `_normalize_mac` matches across separator styles.

**Frontend**

- `buildTopologyTree`: PON grouping; ONUs with a missing or malformed `pon_port` land under
  "Unassigned"; a device with no result yields no Ports node; customers attach under their ONU;
  an empty device list yields an empty tree.
- Search filters and auto-expands the path to each hit.
- Reduced-motion: no animation classes applied.

## Risks

- **The 5-minute staleness cap is a guess.** Too low and the page is chatty; too high and it feels
  stale. It is one constant, easy to change once it has been used in anger.
- **Grid wrapping softens the diagram** at the widest level. Accepted deliberately over horizontal
  panning, which hides most of a PON's ONUs off-screen.
- **`last_result` grows the tree payload.** A 76-ONU result is roughly 20KB of JSON per OLT. Fine
  for one OLT; if a tenant ever has several, this endpoint should paginate or summarise. Noted,
  not solved.
