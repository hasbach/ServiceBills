# Scheduled ONU/CPE-location freshness (agent-mode tenants)

## Context

The Network Tree page's client-side auto-refresh (shipped 2026-09-16) keeps
ONU status fresh for whichever role has the tab open, and additionally
re-runs the CPE-location match ("Locate Customers") every 5 minutes, but
only when an admin/finance viewer's own tab is open (that write action stays
role-gated, by design). This leaves a real gap: if no admin/finance user has
the Network Tree open, CPE-location data (which customer sits behind which
ONU) goes stale indefinitely, even for employee/collector staff who are
actively looking at the tree.

Rather than widen who can trigger the write action (a deliberate boundary
kept closed twice already in this project), this spec moves the freshness
guarantee to the *system* itself: a new cloud-side scheduled job, running
independently of any human viewer or role.

## Decisions from brainstorming

- **Cloud-side scheduled job, not an on-premise-agent self-timer.** The
  on-premise agent (`agent/servicebills_agent.py`) is purely reactive today
  — it only polls "any job for me?" and executes what the cloud already
  created; it cannot self-initiate a job or trigger the write ("apply")
  step. Giving it its own timer would require a new agent capability *and*
  redeploying an updated agent to every tenant's on-premise machine. A
  cloud-side job needs none of that: for agent-mode tenants,
  `_create_device_job` is a cheap DB insert (the connector never runs
  inline), and the already-deployed agent's existing poll loop picks the
  new job up exactly as it does for a button click today.
- **Scoped to `agent`-mode tenants only.** The ~13s inline-connector
  blocking risk that shapes every other design decision in this area only
  applies to `direct` mode. Since only agent-mode tenants have an
  on-premise agent at all, scoping to them sidesteps that risk entirely —
  `direct`-mode tenants keep today's behavior (manual button + the
  per-viewer client-side refresh already shipped).
- **Refreshes both ONU status and CPE-location, every 15 minutes.** Unlike
  the client-side timer (tied to a single viewer's own tab), this job runs
  unconditionally for every agent-mode tenant regardless of who's watching,
  so a longer, tenant-wide-scale interval is appropriate.
- **The write step runs as trusted system code, not through the human-only
  route.** No permission widening anywhere. When the on-premise agent posts
  a completed, system-triggered `cpe_locations` job's result back to the
  cloud, the cloud calls the existing `_apply_cpe_locations` function
  directly (the same function the human-only `apply_customer_locations`
  route already calls) — server-side, no JWT, no route change, no new
  endpoint.

## Design

### Marking a job as system-triggered

`NetworkAgentJob.params` (an existing JSON column, already used for
operation-specific data like `secret_status`'s `pppoe_username`) gets one
additional key when this scheduled job creates a job:
`params={'_scheduled': True}` (merged with any real operation params, though
neither `olt_status` nor `cpe_locations` has any today). This is an explicit,
unambiguous marker — deliberately not inferred from `requested_by_user_id is
None`, since that could in principle also be `None` for an edge case on the
human path (e.g. a valid JWT whose user record was deleted between token
issue and this call), and an explicit marker costs nothing extra to add.

### The scheduler cannot call `_create_device_job` or `_apply_cpe_locations` as-is

Both functions depend on `tenant_query()` (`tenancy.py:27-29`), which derives
the tenant from the current Flask-JWT (`current_tenant_id()` calls
`get_jwt()`, which requires `verify_jwt_in_request()` to have already run).
A scheduler thread has no JWT at all. This isn't limited to the
`requested_by_user_id` stamp: `_create_device_job`'s access-mode check
(`_tenant_access_mode()`) and its agent lookup (`tenant_query(NetworkAgent)`,
`app.py:10661`) both go through the same JWT-derived path, and
`_apply_cpe_locations`'s customer lookup (`tenant_query(Customer)`,
`app.py:11095`) does too. Calling either function unmodified from the
scheduler — or from `agent_post_result`, which authenticates via a separate
agent-token mechanism (`agent_token_required()`) that also carries no JWT —
would throw.

Every existing scheduled job in this file already solves the equivalent
problem the same way: the per-tenant work function takes an explicit
`tenant_id` parameter and queries with plain `Model.query.filter_by(tenant_id=
tenant_id)`, never `tenant_query()` (see `check_pro_plan_expirations_for_tenant`,
`app.py:3179-3211`, called with `t.id` by its `_with_context` wrapper). This
spec follows the same pattern, and deliberately does **not** modify
`_create_device_job` or `_apply_cpe_locations` themselves — both are shared,
heavily-used, security-relevant (multi-tenant-isolation) functions, and every
existing call site of each must stay exactly as it behaves today:

- A new, scheduler-only function covers just the agent-mode job-creation path
  (the only path this feature needs, since it's already scoped to
  agent-mode tenants) using explicit `device.tenant_id`-based queries instead
  of `_tenant_access_mode()`/`tenant_query(NetworkAgent)`. `_create_device_job`
  itself is untouched.
- `_apply_cpe_locations` gains one new optional parameter,
  `tenant_id=None` — when provided, it queries
  `Customer.query.filter_by(tenant_id=tenant_id, ...)` directly instead of
  `tenant_query(Customer)`; when omitted (every existing call site), behavior
  is byte-for-byte identical to today. This is the one small, additive,
  backward-compatible touch to shared code this feature needs — a new
  scheduler/agent-token call site can now pass its own known tenant_id
  explicitly, since there is no JWT to derive one from.

### The new scheduled job

Registered the same way as the existing 6 jobs (`app.py:3245-3257`), but on
a 15-minute interval rather than daily. On each run:

1. Select every tenant whose `BusinessSettings.network_access_mode ==
   'agent'` (mirroring the active-tenant selection the existing 6 jobs
   already use).
2. For each such tenant, for each of its `vsol_olt` `NetworkDevice` rows:
   a. Skip creating a new job for an operation if one is already
      `pending`/`claimed` for that same device+operation — an agent that's
      been offline for a while must not accumulate an unbounded backlog of
      duplicate jobs every 15 minutes.
   b. Otherwise, create an `olt_status` job and a `cpe_locations` job via
      the new scheduler-only job-creation function, both with
      `params={'_scheduled': True}`.
3. **Failure isolation, following the correct existing precedent, not the
   gap.** `check_pro_plan_expirations_with_context` (`app.py:3217-3221`)
   wraps each tenant's work in its own `try/except` so one tenant's failure
   doesn't abort the rest of the run; `auto_sync_upstream_status_with_context`
   (`app.py:3160-3163`) does not, and a single uncaught exception there
   aborts every tenant after it. The new job follows the first pattern: one
   tenant (or one device)'s failure is logged and skipped, never aborting
   the run for other tenants/devices.

This job never waits for a result — enqueuing is fire-and-forget, matching
how `_create_device_job` already behaves for agent-mode operations (the new
function mirrors only that same insert-and-return behavior).

### Auto-applying a completed system-triggered CPE-location job

In `agent_post_result` (`app.py:10424`), immediately after a `cpe_locations`
job is marked `done` with a valid result (`app.py:10490-10491`, the
`job.result = result; job.error = None` branch): if
`job.params.get('_scheduled')` is true, call
`_apply_cpe_locations(job.result, tenant_id=job.tenant_id)` directly and let
its own result be logged (there's no human waiting on an
HTTP response to show a snackbar to — this runs inside the agent's own
result-POST request, not a browser's). A human-triggered `cpe_locations` job
(no `_scheduled` marker) is completely unaffected: it still requires the
separate, JWT-gated `apply_customer_locations` call the frontend already
makes.

An `olt_status` job needs no equivalent special-casing: its result already
gets folded into `NetworkDevice.last_status`/`last_checked_at` on completion
regardless of who created it, and any tree viewer (or the CS-agent's own
5-minute job cache) picks it up automatically.

### Non-goals

- No change to the client-side auto-refresh shipped 2026-09-16 — it
  continues to run per-viewer, independent of this job. Some redundant job
  creation between the two mechanisms is possible (a viewer's own tab and
  this scheduled job both requesting a check around the same time) and is
  accepted as harmless — the agent just processes whichever job it claims
  first from its queue.
- No change to `direct`-mode tenants' behavior.
- No change to the on-premise agent (`agent/servicebills_agent.py`) at all
  — it already knows how to execute both operation types and already POSTs
  results to the same endpoint regardless of who created the job.
- No new API endpoint, no new database migration (the `_scheduled` marker
  lives in the existing `params` JSON column).

## Testing

- Unit tests for the new scheduled-job function: creates jobs for
  agent-mode tenants' OLTs only (not direct-mode), skips a device with an
  already-outstanding pending/claimed job for that operation, isolates one
  tenant's failure from the rest, stamps `params={'_scheduled': True}`.
- Unit tests for `_apply_cpe_locations`'s new `tenant_id` parameter: an
  explicit `tenant_id` scopes the customer lookup to that tenant with no
  JWT/request context required; omitting it (every existing call site)
  behaves exactly as today.
- Unit tests for `agent_post_result`: a `_scheduled` `cpe_locations` job's
  successful result triggers `_apply_cpe_locations(..., tenant_id=job.tenant_id)`;
  a non-`_scheduled` one does not (existing behavior unchanged); an
  `olt_status` job never triggers it regardless of the marker.
- Live verification (this project's established pattern): after deploy,
  confirm via Render logs that the new job fires on its 15-minute interval
  for DeltaNet's own tenant (agent mode), and that `Customer.onu_mac_address`
  updates without any admin/finance browser tab being open.
