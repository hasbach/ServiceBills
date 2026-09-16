# Network Tree: periodic auto-refresh for ONU/CPE status

## Context

`NetworkTreeView.js` (the Network Tree page) already has a one-shot
auto-refresh: on mount, any OLT device whose cached result is older than
`STALE_AFTER_MS` (30 minutes) gets a single background "Load ONUs" check,
one device at a time. That 30-minute threshold was deliberately chosen over
5 minutes because in `direct` access mode (the default for every tenant) an
OLT check runs the connector *inline* in the request, blocking the single
production gunicorn worker for ~13s -- at 5 minutes, "almost every visit to
this page cost the whole app a stall."

The request driving this spec: make the "Locate Customers" (location-pin)
button visible to `employee`/`collector` roles, and add an automated ~5-min
refresh of ONU and CPE status so the tree stays current without a human
clicking "Load ONUs" repeatedly.

## Decisions from brainstorming

- **Permissions are unchanged.** "Locate Customers" and "Match Labels" stay
  `admin_or_finance_required()` on the backend
  (`app.py:11122`/`app.py:11140`) and gated by `canEditLinks` on the
  frontend (`NetworkTreeView.js:115-117`), exactly as today. That boundary
  (documented at `app.py:1935-1946`) exists specifically so widening the
  page's *read* access to field roles never widens who can rewrite
  `Customer.onu_mac_address`. This spec does not touch it. The only thing
  that changes is that the automated timer *may* trigger the existing write
  action on behalf of a viewer who could already click the button
  themselves (admin/finance) -- never on behalf of employee/collector.
- **DeltaNet's own tenant runs in `agent` mode**, confirmed by the user.
  OLT/CCR checks go through the on-premise agent's async `NetworkAgentJob`
  queue, not an inline blocking call -- so a 5-minute interval does not
  reintroduce the gunicorn-worker-stall risk that shaped the existing
  30-minute constant. The residual cost is job/hardware load (SNMP walks
  via the on-prem agent), not web-server blocking.
- **Trigger: client-side, only while the page is open.** A `setInterval`
  inside `NetworkTreeView` keeps running only as long as the component is
  mounted. No new backend scheduled job. This bounds the extra load to
  actual usage and requires no new infrastructure.
- **Both actions run, gated by existing role logic:**
  - "Load ONUs" (`refreshOlt`, read-only ONU/PON status) runs for every
    viewer, every tick -- everyone can already trigger this manually today.
  - "Locate Customers" (`locateCustomers`, the CPE-location write cycle)
    *also* runs every tick, but only when `canEditLinks` is true
    (admin/finance) -- mirroring the existing pattern for `checkDevice` at
    `NetworkTreeView.js:489` ("an employee or collector's JWT gets a real
    403 here ... so without this gate every such user would see an
    undismissable error"). Employee/collector viewers get the ONU status
    refresh only; they never trigger the write action or see a 403 from a
    background timer.
  - The user explicitly confirmed this means a customer's `onu_mac_address`
    can now change automatically, without a human clicking "Locate
    Customers" that day, whenever an admin/finance viewer has the tree open.
- **Interval: 5 minutes**, as one named constant
  (`AUTO_REFRESH_PERIOD_MS`), trivially bumped to 15 minutes (the user's
  own fallback) later if OLT/on-prem-agent load turns out to be a problem
  in practice. No code beyond that constant needs to change to retune it.

## Design

### New constant and effect

```js
// How often the page re-checks every OLT while it stays open, independent
// of STALE_AFTER_MS (which only governs the one-shot "catch up on mount if
// very stale" effect above). DeltaNet's own tenant runs in 'agent' mode, so
// this does not block the shared web worker -- see the design doc. Bump to
// 15 * 60 * 1000 if on-prem-agent/OLT load becomes a concern; this is the
// only place the interval is defined.
const AUTO_REFRESH_PERIOD_MS = 5 * 60 * 1000;
```

A new `useEffect`, separate from the existing one-shot stale-catchup effect,
owns a `setInterval(tick, AUTO_REFRESH_PERIOD_MS)` and clears it on unmount.
`tick` is an async function that:

1. Calls a new exported pure function, `collectAutoRefreshOltDevices(tree,
   refreshingIds)`, which walks the tree (same recursive `children` walk the
   existing stale-effect uses) and returns every OLT device that is not
   currently mid-refresh (`!refreshingIds[device.id]`). Pure and exported so
   it is unit-tested the same way `describeAge`/`toggleExpansion` already
   are, without mounting the component or touching real hardware.
2. For each returned device, sequentially (one device at a time -- same
   "don't fire every OLT's walk simultaneously" reasoning as the existing
   stale-effect, now aimed at not flooding the on-prem agent's queue rather
   than the web worker):
   - `await refreshOlt(device, { auto: true })`
   - `if (canEditLinks) await locateCustomers(device)`
   - Each call individually wrapped in try/catch, same as the existing
     stale-effect loop -- one device's failure (agent offline mid-flight,
     etc.) must not stop the rest, and `refreshOlt`/`locateCustomers`
     already record their own failures into `errorByDevice`.
3. Skipped entirely (effect returns early) when `accessMode === 'agent' &&
   !agentOnline` -- same guard the existing stale-effect uses, since a job
   would just be refused with the agent offline.

**Implementation note -- the interval must not depend on `tree`.** `tree`
changes on essentially every action on this page (a manual refresh's
optimistic merge, its own `loadTree(false)` resync, another viewer's
`checkDevice`, etc.). If the effect that owns the `setInterval` depended on
`tree`, every one of those changes would tear down and recreate the
interval, perpetually restarting the 5-minute countdown -- on a page with
any regular activity, the timer could effectively never fire. Instead:

- A small ref (`latestRef.current = { tree, refreshingIds, canEditLinks }`,
  kept current by a plain, dependency-driven `useEffect` with no cleanup)
  holds the latest values.
- The interval-owning effect depends only on `[accessMode, agentOnline]` --
  set up once per mount and only torn down/recreated if the agent's
  online-ness changes (at which point re-evaluating whether to run at all is
  correct anyway). Its `tick` callback reads `latestRef.current` fresh each
  time it fires, so it always acts on current data despite the stable
  interval.
- `clearInterval` runs in the effect's cleanup exactly once, on unmount or
  on an `accessMode`/`agentOnline` change.

This guarantees a true "every `AUTO_REFRESH_PERIOD_MS`, for as long as the
tab stays open" cadence, independent of how often anything else on the page
changes `tree`.

### Non-goals

- No backend changes: no new scheduled job, no cache/dedup added to
  `_create_device_job`, no change to `admin_or_finance_required()` on any
  route.
- No change to `STALE_AFTER_MS` or the existing mount-time stale-catchup
  effect -- they keep working exactly as today, independent of this new
  timer.
- No visibility change to any button. "Locate Customers" remains rendered
  only for admin/finance, exactly as today; this spec only changes what an
  already-permitted viewer's open tab does automatically in the background.
- No `document.visibilityState` handling for backgrounded tabs -- out of
  scope; the plain mount-lifetime interval is enough for what was asked.

## Testing

- Unit tests (Jest, same mock-`AppContext` pattern as
  `NetworkTreeView.toggleExpansion.test.js`) for
  `collectAutoRefreshOltDevices`: returns only OLT devices, excludes
  non-OLT devices, excludes a device present in `refreshingIds`, walks
  nested `children` correctly, returns `[]` for an empty tree.
- No live-hardware test is possible from this environment. Per this
  session's established workflow, final verification is a live production
  check: open the Network Tree page as admin/finance and confirm "Load
  ONUs"/"Locate Customers" both fire again ~5 minutes later without a
  manual click, then open it as an employee/collector test account and
  confirm only the ONU status (never a 403 alert) auto-refreshes.
