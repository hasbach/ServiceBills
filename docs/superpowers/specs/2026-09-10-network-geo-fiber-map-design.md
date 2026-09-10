# Network Geo Fibre Map — Design

## Problem

The Network Tree ([2026-09-05-network-tree-v2-design.md](2026-09-05-network-tree-v2-design.md))
shows DeltaNet's plant as a logical tree: CCR → OLT → PON → ONU → customer. It answers
"what is down" but not "**where** is it down". When a splice fails or a drop is cut, staff
learn that N customers are offline and must then work out, from memory and phone calls, which
physical span to send a technician to.

This spec adds a geographic view of the same plant. Staff trace the real fibre run on a
satellite map once; thereafter every span is drawn between its true endpoints and coloured by
live ONU status, so a fault localises to a specific span on the ground.

It also begins recording per-ONU optical power, which is the input that later makes
degradation alerting possible. Nothing analyses that history in this pass — see Out of Scope.

## Prerequisite: the working checkout is stale

At the time of writing, local `main` is `c78dba5` and is **0 ahead / 108 behind
`origin/main`**. The Layer 2 agent (`agent/servicebills_agent.py`), Network Tree v2 and the
CPE-MAC linking work all exist only on `origin/main`. This spec is written against
`origin/main`, and **the branch for this work must be cut from `origin/main`, not from the
current local `main`.** Local `main` has no commits of its own, so a fast-forward is
sufficient — do not attempt a merge (see `[[project_network_topology_tree]]`).

## Verified device facts this design rests on

All measured read-only against DeltaNet's real V1600D at `192.168.8.100` on 2026-09-10.
Recorded in full in `[[project_network_topology_tree]]`; the load-bearing points:

1. **Per-ONU optical works.** Table `1.3.6.1.4.1.37950.1.1.5.12.2.1.13.1`, indexed
   `<pon>.<onuid>`, returns real values for **every online ONU** (66 of 89 rows at probe
   time): col 3 temperature, col 4 voltage, col 5 bias current, col 6 Tx power, col 7 Rx
   power. Rx spanned roughly **-9 to -27 dBm**. Offline ONUs are simply absent from the table.
   A prior note claiming optical was unsupported was reading **table 10**, a useless
   capability-flag table. Use table 13.

2. **The OLT's LOS threshold is -32 dBm** — table `...12.2.1.14.1` cols 3 and 4, `-32` for
   every row. This makes "margin to LOS" a real figure, not a guess.

3. **There is no deregistration reason in SNMP.** All 23 offline ONUs read byte-identical
   across every candidate column. The reason exists only in the OLT's web UI —
   `ONU Configuration > ONU list > ONU Status`, column `Last Deregister Reason`, with observed
   values `Power Off`, `Wire Down` and `N/A`. The owner has decided it **is** worth having, so
   this spec includes a light HTTP scrape — see "The deregister reason".

4. **The OLT walks at roughly 58 varbinds/sec.** `vsol_olt.py` already warns never to walk the
   enterprise root (~16k varbinds). Table 13 is ~460 varbinds ≈ 8s; a cycle that also needs
   MAC addresses from the main table is ~1,700 varbinds ≈ **30s**.

5. **Duplicate MACs persist.** 89 ONU rows carry 77 unique MACs; all 10 of PON 8's rows are
   offline ghosts of PON 1 ONUs. `vsol_olt._dedupe_by_mac` already resolves this by preferring
   the online row, and this feature must reuse it rather than re-deriving the rule.

6. **The distance figure is far coarser than a percentage can express.** Owner-reported: an ONU
   physically **1 metre** from the OLT reads as **6 metres**. The error is a fixed offset of
   several metres, not proportional — 500% at 1 m, 0.3% at 1,500 m — which invalidates any
   percentage-only tolerance. See "Gross-placement sanity check".

7. **The web UI login is a plain form with no CSRF token** — `POST /action/main.html` with
   `user`, `pass` and hidden `who=100`, pages in `charset=gb2312`. Server-rendered, so a
   `requests`-based scrape suffices and no headless browser is needed. See "The deregister
   reason" for what remains unverified.

## Decisions the owner made during design — do not silently revisit

1. **The map is built by tracing a chain, not by radiating from the OLT.** Place the control
   room, draw to a point, pick the ONU there from a searchable dropdown, then continue from
   that point to the next. Branches come free by starting a new line from any placed node.
2. **Chain points may be plain junctions** with no customer — a pole, a splitter box, a NAP.
3. **The deregister reason IS included** (revised 2026-09-10, after the web UI was confirmed
   scrapable with a plain HTTP client). Online/offline status alone drives path *colour*; the
   reason refines the *explanation* shown for a locally-scoped fault.
4. **No API key for tiles.** Verified over Tripoli: Esri World Imagery serves real satellite
   imagery through z=19 (~0.3 m/px), and OSM standard serves street tiles to z=19, both
   without a key or billing. Google Maps is explicitly not used.
5. **Alerts go to the existing PWA push channel.** The stack is already complete —
   `pywebpush`, VAPID keys, `PushSubscription`, `send_push_notification()`,
   `/api/push-subscribe`, and a service worker handling `push` and `notificationclick`.
6. **Optical: poll every 15 minutes, keep raw 14 days, roll up hourly and keep that a year.**

## Data model: one new table for topology

```python
class NetworkNode(db.Model):
    """One point on the geographic fibre map. Each node's link to its parent IS
    a span -- fibre is a tree, so a node has exactly one parent and no separate
    span table is needed. Mirrors NetworkDevice.parent_device_id."""
    id                = Integer, primary key
    tenant_id         = FK tenant.id, NOT NULL, indexed
    olt_device_id     = FK network_device.id, NOT NULL   # which OLT's plant this belongs to
    kind              = String(10), NOT NULL             # 'root' | 'junction' | 'onu'
    label             = String(100), NOT NULL
    latitude          = Numeric(9, 6), NOT NULL
    longitude         = Numeric(9, 6), NOT NULL
    parent_node_id    = FK network_node.id, NULLABLE     # null only for kind='root'
    onu_mac           = String(20), NULLABLE, indexed    # set only when kind='onu'
    created_at        = DateTime
    updated_at        = DateTime

    # Volatile scraped state, overwritten each fetch, never appended to.
    # Display-only -- the status algorithm must not read these.
    last_dereg_reason     = String(20), NULLABLE   # 'Power Off' | 'Wire Down' | None
    last_dereg_reason_at  = DateTime, NULLABLE     # when WE read it, not the OLT's clock
```

`last_dereg_reason_at` records when ServiceBills fetched the value, deliberately not the OLT's
own timestamp: the OLT has no real clock and reports uptime-relative times like
`1970/01/30 17:52:34`, so its timestamps cannot be stored as absolute times.

`Numeric(9, 6)` gives roughly 11 cm of precision and avoids float drift on round-trips.
Nothing in the schema currently stores coordinates, so this is genuinely new — searching
`origin/main` for latitude or longitude returns nothing.

**Invariants, enforced in the write endpoints and asserted by tests:**

- `kind='root'` if and only if `parent_node_id IS NULL`. Exactly one root per `olt_device_id`.
- `onu_mac IS NOT NULL` if and only if `kind='onu'`. Junctions and the root never carry a MAC.
- `onu_mac` is unique per `olt_device_id` — one ONU cannot be placed in two locations.
- `parent_node_id` may not be the node itself or any of its descendants. **Rejected at write
  time by walking up from the proposed parent.** Tree v2 shipped a tree builder that silently
  dropped whole cyclic components; the read path here must also defend, surfacing unreachable
  nodes as orphans rather than dropping them.

ONU nodes bind by **MAC**, not by `(pon, onuid)`: a MAC survives an ONU being moved between
PON ports, and it is the key `Customer.onu_mac_address` already uses.

## The status algorithm

One rule, evaluated bottom-up over the node tree, given a map of MAC to online/offline taken
from the OLT's most recent completed job result:

> A node's **subtree is alive** if it contains at least one online ONU, itself included.
> A node's **subtree is known** if it contains at least one ONU node whose MAC appears in the
> OLT's data at all, online or offline.

From which:

| element | rule |
|---|---|
| span parent to child | **green** if `alive(child)`; **red** if `known(child)` and not `alive(child)`; **grey** if not `known(child)` |
| ONU node dot | its own online/offline state; grey if its MAC is absent from OLT data |
| junction / root dot | `alive(subtree)` |
| **fault boundary** | a **red span whose parent node is alive** |

The fault boundary is the whole point: everything red below it is consequence, not cause. It
renders distinctly from ordinary red (thicker, animated) because it is where the technician
goes. If the root itself is not alive, there is no boundary and the fault is the OLT or
upstream of it — handled as its own case, not as a missing boundary.

**Checked against the owner's three cases**, chain `A → B → C`:

```
A up,  B OFF, C up     alive(B) is true, C proves it
                       -> span A→B green, B's dot red
                       -> fault is LOCAL to B: its own drop/splitter, or its power

A up,  B OFF, C OFF    alive(B) false, known(B) true
                       -> A→B red and B→C red; parent A is alive
                       -> fault boundary is A→B: THE SPAN A→B IS CUT

all up                 every span green
```

A branch point behaves correctly without special-casing: a junction with one live child and
one dead child is itself alive, so the span to the dead child becomes the boundary.

**Where the deregister reason fits.** The rules above are deliberately unchanged by it: colour
is decided by status alone, so the map is correct even when the reason is missing, stale or
wrong. What the reason adds is the *explanation* on a locally-scoped fault — the case where an
ONU is dark but its downstream is alive. There, `Power Off` means the customer lost mains and
nobody needs to be dispatched, while `Wire Down` means that customer's own drop is broken and
somebody does. That is the single most common call DeltaNet's staff take, and it is the one
distinction status alone cannot make.

It is an overlay, never an input to the colouring. This matters because the reason is the
least trustworthy datum in the system: it describes the *last* disconnection rather than the
current one, it is `N/A` on at least some currently-offline ONUs, and it comes from a scrape
that can fail. A design that let it decide colour would let any of those turn a correct map
into a wrong one.

## API

Read endpoints must be available to the same roles that are granted the Network Tree page;
**all writes are admin/finance.** Tree v2 shipped a defect where a page granted to
employee/collector called an admin-only endpoint on mount and showed those roles a permanent
red error — so the map page must not call any admin-only endpoint just to render.

```
GET    /api/network-map?olt_device_id=<id>
       -> { nodes: [...], onu_status: {mac: 'online'|'offline'},
            last_result_at, orphans: [...] }
       Cache-first, exactly like the tree: renders from the newest completed
       job result, never contacts a device itself.

GET    /api/network-map/unplaced-onus?olt_device_id=<id>
       -> ONUs present in OLT data with no NetworkNode bound, each with
          mac, pon, onu_id, description, status, and linked customer names.
          This is what feeds the placement dropdown.

POST   /api/network-map/nodes            create (admin/finance)
PUT    /api/network-map/nodes/<id>       move, relabel, rebind, reparent (admin/finance)
DELETE /api/network-map/nodes/<id>       delete (admin/finance)
```

`DELETE` on a node with children is **rejected with a 409** naming the children, rather than
cascading or silently reparenting. Deleting a traced run by accident is expensive to redo.

`NetworkNode` must be added to `TENANT_OWNED_MODELS` **and** to `_TENANT_DELETE_ORDER`.
Tree v2's merge silently dropped `NetworkDevice` from the delete order, which would have
raised `ForeignKeyViolation` on a Postgres tenant delete and was invisible on SQLite; only
`test_lifecycle.py::test_tenant_owned_models_all_in_delete_order` caught it.

## Frontend

New page, `NetworkMapView.js`, plus **Leaflet** as a new dependency (~40 KB, no key). Two base
layers with a toggle, both attributed as their terms require:

- satellite — `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
- street — `https://tile.openstreetmap.org/{z}/{x}/{y}.png`

Neither has an SLA. If a tile source becomes unavailable the map must degrade to a blank
canvas with nodes and spans still drawn and a visible notice — the topology is the data, the
imagery is only backdrop.

**Placement flow.** An "Add node" mode: click the map, choose junction or ONU; if ONU, pick
from the searchable unplaced list showing MAC, the OLT's own label and any linked customer.
To extend a run, select a placed node, "draw from here", then click the next location. Nodes
drag to reposition. A side panel shows how many ONUs remain unplaced — currently 77 unique
MACs, 66 of them online.

Spans render as polylines coloured by the algorithm above; the fault boundary renders thicker
and animated. Clicking a node shows its ONU details and the customers behind it.

## Gross-placement sanity check

The OLT reports each ONU's distance in metres, and summing the great-circle length of the
spans from root to that ONU gives a straight-line lower bound on the fibre. Comparing the two
can catch a pin dropped in the wrong place.

**An earlier draft of this spec proposed a 15% tolerance. That was wrong, and not merely
mistuned — wrong in shape.** The owner reports an ONU physically 1 metre from the OLT reading
as 6 metres. That is a ~5 m fixed offset (internal patch cords plus ranging and processing
delay), so the error is *absolute*, not proportional. At 1 m it is 500%; at 1,500 m it is
0.3%. A percentage tolerance is therefore meaningless at the short end and uselessly slack at
the long end, and any single percentage is wrong at one end or the other.

**So this check is demoted to what it can actually do: catch gross errors only.** A pin in the
wrong village, not a pin on the wrong side of a street.

```
skip entirely when reported_distance < DISTANCE_CHECK_MIN_METRES (100)
    -- below this, ranging error swamps the signal completely

otherwise flag only when BOTH:
    chain_length > reported_distance * DISTANCE_CHECK_FACTOR (2.0)
    chain_length - reported_distance > DISTANCE_CHECK_MIN_GAP_METRES (100)
```

Requiring both a proportional and an absolute overshoot is what makes it safe at every range:
the factor stops it firing on long runs with legitimate slack, and the absolute gap stops it
firing on short runs where the offset dominates. All three values are named module constants,
tunable once real placements exist.

Consequences worth stating plainly: this will **not** catch a pin that is merely somewhat
wrong, and it never fires below 100 m — so an ONU near the control room gets no validation at
all. It is a cheap backstop against a data-entry blunder, not a guarantee of accuracy. It
needs no extra device call, and it renders as an advisory badge on the node, never as an error
that blocks saving. Staff eyes on satellite imagery remain the real check.

## The deregister reason

Added at the owner's request once the web UI was confirmed scrapable without a browser.

**What is already verified** (read-only, no credentials): the login page is a plain HTML form,
`POST /action/main.html` with fields `user`, `pass`, and a hidden `who=100`. **No CSRF token.**
Pages declare `charset=gb2312` and must be decoded as such, not as UTF-8. jQuery 1.7.1 is
present but the pages are server-rendered, so `requests` plus an HTML parser is sufficient —
no Playwright, and none of the cost the original topology-tree spec was going to pay for it.

**What is NOT yet verified, and must be a first task before anything is built on it:**

1. What the login POST returns on success, and how the session is carried — a cookie, or
   something worse like source-IP binding.
2. The exact address and parameters of the ONU Status page per PON port. The screenshot shows
   a `Port ID` dropdown plus a Refresh button, so it is one request per PON — four in total
   for PON 1, 3, 4 and 8.
3. **Whether the OLT permits more than one concurrent admin session.** This is the real risk,
   not the parsing. Many OLT web UIs of this vintage allow exactly one, in which case the
   scraper and DeltaNet's own staff will repeatedly evict each other from the web UI. If that
   turns out to be true, this component must be reconsidered rather than shipped — being
   logged out of your own OLT every few minutes is a worse problem than a missing reason.

Because of point 3, the reason is fetched **only alongside an OLT status walk**, never on a
schedule of its own, and never on page render. It is also the only part of this spec that can
be dropped late without touching anything else, precisely because colour never depends on it.

**Credentials.** A `vsol_olt` device row's `password` column already holds the SNMP community,
so the web credentials need somewhere else to live: add `web_username` and `web_password`
(the latter `EncryptedString`, like the existing column) to `NetworkDevice`, both nullable.
Null means "do not scrape", which is also the correct default for any tenant that has not
supplied them and the safe state if point 3 goes badly.

**Storage.** The reason is per-ONU volatile state, not history: store `last_dereg_reason` and
`last_dereg_reason_at` on the ONU's `NetworkNode` row, overwritten each fetch. It is not
appended to a table — nothing in this pass reads a reason older than the current one, and the
field's own unreliability makes a permanent record of it actively misleading.

**Display rule.** Shown only on a node whose ONU is currently offline *and* whose downstream is
alive — the locally-scoped fault case where it changes what staff do. Suppressed everywhere
else, because on an online ONU it describes an outage that has already ended, and inside a
dead subtree the span diagnosis already outranks it. When it is `N/A` or the scrape failed,
the node simply shows the unrefined "fault local to this point" and the UI says the reason is
unavailable — it must never silently imply `Power Off`.

## Optical logging

**Connector.** Add `get_olt_optical(server)` to `vsol_olt.py`, walking only
`1.3.6.1.4.1.37950.1.1.5.12.2.1.13.1` plus the main table's MAC column for the join. It must
follow the module's existing rules: never walk the enterprise root, close the SNMP engine in a
`finally` (an earlier review caught an unclosed engine leaking UDP transports), and never log
the decrypted community — Sentry's `LoggingIntegration` captures frame locals at ERROR, which
is how a credential leak was caught in the topology-tree build.

Values arrive as strings like `0.12 mW (-9.34 dBm)`; the connector parses out the dBm figure
and stores a number. A value that fails to parse is stored as NULL, not zero — zero dBm is a
valid reading and would corrupt every later trend.

**Agent operation.** Add `'olt_optical'` to `AGENT_OPERATIONS` and to
`DEVICE_TYPE_OPERATIONS['vsol_olt']`. A test asserts that mapping is total, so omitting the
second placement fails loudly rather than making the operation quietly unreachable. Adding a
connector function changes the connector fingerprint, so agents running an older copy will
correctly report `stale` in Settings until updated.

**Schedule.** One APScheduler job, `trigger="interval", minutes=15`. Every existing job in the
app is `days=1` with `next_run_time=datetime.now()`, which fires immediately at startup —
**this job must not do that.** `NetworkAgentJob`'s own docstring records why: the in-process
scheduler fires during `flask db upgrade` on deploy, which is precisely when the schema may be
half-migrated. Omitting `next_run_time` lets the first run wait a full interval. Note also
that the scheduler's threadpool is `max_workers=1`, so a 30-second walk occupies the only
worker; at 30s per 900s that is fine, but it is shared with the daily jobs.

**Ingest.** The scheduled job only *creates* the agent job. The readings are written by the
existing agent job-completion path, which recognises `operation='olt_optical'` and inserts the
rows. No second scheduled job scans for finished work — fewer moving parts, and it keeps the
write in the one place that already knows a result arrived.

**Tables.**

```python
class OnuOpticalReading(db.Model):     # raw, pruned at 14 days
    id, tenant_id (FK, indexed), olt_device_id (FK)
    onu_mac    = String(20), NOT NULL, indexed
    pon_port   = Integer
    onu_id     = Integer
    rx_dbm     = Numeric(6, 2), NULLABLE
    tx_dbm     = Numeric(6, 2), NULLABLE
    temp_c     = Numeric(6, 2), NULLABLE
    voltage_v  = Numeric(5, 2), NULLABLE
    bias_ma    = Numeric(7, 2), NULLABLE
    read_at    = DateTime, NOT NULL, indexed

class OnuOpticalHourly(db.Model):      # rollup, kept 365 days
    id, tenant_id (FK, indexed), olt_device_id (FK)
    onu_mac      = String(20), NOT NULL, indexed
    hour_start   = DateTime, NOT NULL, indexed
    rx_min, rx_avg, rx_max = Numeric(6, 2)
    tx_avg                 = Numeric(6, 2)
    sample_count           = Integer
    unique (tenant_id, onu_mac, hour_start)
```

Steady state at 66 online ONUs: roughly 89k raw rows and 578k rollup rows. Both optical models
go into `TENANT_OWNED_MODELS` and `_TENANT_DELETE_ORDER`, joining `NetworkNode` — three new
models in total across this spec, all three of which must appear in both lists.

Rollup and pruning run inside the same 15-minute job, guarded so they do real work at most
once an hour — not as separate scheduled jobs, for the deploy-timing reason above.

## Out of scope (explicitly deferred)

- **Alerting on optical degradation.** This pass only records. The threshold and trend logic,
  the margin-to-LOS at-risk list, and the push notification itself are the next slice. The
  owner chose this split so history begins accumulating now: a trend cannot be computed
  retroactively over data that was never stored.
- **An SSH client for the OLT.** SSH is open (`OpenSSH_7.3`) and would be a lighter transport
  than HTML scraping, but the CLI's command set is unknown and the HTTP form is already
  characterised. Worth revisiting only if the single-session problem above proves real.
- **A history of deregister reasons.** Only the current value is stored — see that section.
- **Real traced fibre routes** following poles and streets. Spans are straight lines between
  their endpoints. A true outside-plant GIS was considered and rejected as disproportionate.
- **The PWA manifest.** `frontend/public/index.html:17` links a `manifest.json` that does not
  exist anywhere in the repo, so the app is not installable and iOS cannot receive web push at
  all. Tracked separately; it becomes a hard prerequisite for the alerting slice, not for this
  one.
- **EDFA, splitter and unmanaged-switch telemetry.** Permanently unmonitorable by device type;
  they appear on the map as junctions whose state is inferred, which is the correct treatment.

## Testing

- **Status algorithm** — a pure function over (tree, status map), so it is tested directly with
  no device and no HTTP: each of the three owner cases; a branch point with one live and one
  dead child; an all-dead tree yielding no boundary; unknown MACs producing grey rather than
  red; and a deliberately cyclic `parent_node_id` producing orphans rather than a silent drop.
- **Invariants** — root/parent exclusivity, MAC-only-on-ONU, MAC uniqueness per OLT,
  self-parent and descendant-parent rejection, and 409 on deleting a node with children.
- **Tenant lifecycle** — `test_tenant_owned_models_all_in_delete_order` must pass with all
  three new models. This is the test that caught the equivalent omission last time.
- **Connector** — `get_olt_optical` against recorded varbind fixtures, including the
  `0.12 mW (-9.34 dBm)` parse, an unparseable value becoming NULL, and offline ONUs being
  absent rather than zero.
- **Distance check** — the owner's own 1 m-reads-as-6 m case must NOT flag; a reported distance
  under 100 m never flags whatever the chain says; a chain over double the reported distance
  with a gap above 100 m does flag; and a case satisfying only one of the two conditions does
  not.
- **Deregister-reason scrape** — parsing a saved copy of a real ONU Status page, including the
  gb2312 decode, an `N/A` value, and a row whose reason refers to an outage that has ended.
  Plus the display rule: suppressed on an online ONU, suppressed inside a dead subtree, shown
  only on a locally-scoped fault, and never defaulting to `Power Off` when absent.
- **Colour independence** — the status algorithm's output must be byte-identical with the
  reason present, absent, `N/A`, and stale. This is the test that keeps an unreliable input
  from corrupting a reliable map.
- **Frontend** — `@testing-library/react` is **not installed**, which is why every
  refresh-lifecycle guarantee in Tree v2 is verified by reading only, and is the top follow-up
  from that build. Installing it is in scope here: the status colouring is the whole feature,
  and it should not ship with the same blind spot.

Two environment traps that have cost time before: a worktree path containing a dot-prefixed
segment corrupts CRA's default Jest `testMatch`, so `react-scripts test` reports "No tests
found" for every file — indistinguishable from passing. Always pass `--testMatch` explicitly.
And `CI=true npx react-scripts build` fails on roughly 30 pre-existing warnings in unrelated
files while the Dockerfile's bare build succeeds; use the bare command.

## Risks

- **The scraper may evict staff from their own OLT web UI.** The highest-consequence unknown in
  this spec. If the V1600D allows only one concurrent admin session — common for this
  generation of web UI — then the scraper logging in will log staff out, and staff logging in
  will break the scraper. Mitigated structurally rather than by hope: the scrape runs only
  alongside a status walk, colour never depends on it, credentials default to null meaning
  "off", and the component can be dropped entirely without touching the rest of the feature.
  **Verifying this is the first task, before any code is written against it.**
- **Scraping is brittle in a way SNMP is not.** A firmware update can rename a column or
  restructure the table, and the failure mode is a silently missing reason rather than an
  error. The parser must treat an unrecognised table shape as "reason unavailable" and say so
  in the UI, never guess a column by position alone.
- **Tile sources have no SLA and could change terms.** Mitigated by degrading to a blank
  canvas rather than a broken page, and by the base layer being one line to swap.
- **Placement is manual and unvalidated at entry.** 77 ONUs is a real data-entry effort, and a
  wrong pin produces a confidently wrong map. The distance cross-check is the only automatic
  guard, and it catches only pins that are too far, not pins that are plausibly wrong.
- **Status is only as fresh as the last OLT walk.** The map renders from cache. In direct mode
  a walk blocks the single sync gunicorn worker; in agent mode it does not. A map that people
  leave open makes agent mode materially more important than the tree did.
- **A failed walk must not erase the map.** Tree v2 shipped a defect where a failed connector
  run stored `status='done'` with `result=None`, which blanked the cached tree *and* advanced
  its freshness stamp so it never retried. The map reads the same job rows and must treat a
  null result as "no new data", never as "everything is down" — the difference between a stale
  map and a map that claims the whole town is offline.
