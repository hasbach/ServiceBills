# Relaying PPPoE writes through the on-prem agent

**Date:** 2026-09-08
**Status:** approved by the owner, ready for an implementation plan
**Base:** `main` = `471cb00` (deployed), branch `network-relay-writes`

## Goal

Let staff suspend and unsuspend a customer's PPPoE secret on the tenant's own
Mikrotik through the on-prem agent, without handing a compromised cloud the
ability to disconnect everyone.

## Why this needed its own cycle

Every read operation the agent relays was safe to add because reading cannot
hurt anyone. `mikrotik.set_secret_enabled` disconnects a paying customer, and
its absence from the agent's `ALLOWED_OPERATIONS` has been a deliberate,
load-bearing property since Layer 2 shipped: **a compromised cloud can read the
tenant's network but can never disconnect anyone.**

Relaying writes breaks that property by construction. The question this cycle
answers is not "how do we add an operation" — it is "what compensating control
makes giving the cloud a disconnect primitive acceptable."

## Owner decisions — do not revisit

1. **Scope is the CCR, not the upstream portals.** The owner's day-to-day
   suspension work happens on upstream RADIUS portals, which
   `upstream_portal.py` and `upstream_portal_krypton.py` can only *read*
   (`get_subscriber_status` is their sole public function). Teaching those
   scrapers to write is a separate, larger, more fragile project, and is
   blocked on async-worker infrastructure that does not exist. Out of scope.
2. **Compensating control: an agent-side rate limit on suspends.** Chosen over
   relaying plainly with audit only, and over a maintenance window the owner
   opens on the box. The window was rejected as a bad fit: the owner suspends
   reactively, the moment a customer ignores a reminder, so requiring a trip to
   the machine first would not survive contact with the workflow.
3. **The cap is 5 suspends per rolling hour.** The owner chose tighter than the
   10 proposed. Suspension is one-at-a-time and reactive, so 5 is well clear of
   real use, and 5 of the CCR's 64 locally-authenticated subscribers is a much
   smaller blast radius than 10.
4. **Unsuspends are never rate-limited.** Restoring service is not the attack,
   and `_maybe_restore_mikrotik_access` fires automatically after settling
   payments — capping it would break billing, not protect anyone.

The property this leaves is weaker than before and stated honestly: a
compromised cloud **can never disconnect everyone**, rather than can never
disconnect anyone.

## Established facts

Verified 2026-09-08. Do not re-derive.

- `ALLOWED_OPERATIONS` in `agent/servicebills_agent.py` is six reads:
  `test_connection`, `device_health`, `secret_status`, `active_session`,
  `olt_status`, `cpe_locations`. `set_secret_enabled` is in neither that tuple
  nor either row of `DEVICE_TYPE_OPERATIONS` in `app.py`.
- `mikrotik.set_secret_enabled(server, pppoe_username, enabled)` returns
  `(ok: bool, message: str)`.
- Exactly three cloud call sites: `_maybe_restore_mikrotik_access`
  (`app.py:4361`, automatic after a settling payment),
  `suspend_customer_network` (`app.py:10595`) and
  `unsuspend_customer_network` (`app.py:10607`). All three currently refuse in
  agent mode with a 501 and `AGENT_WRITE_UNSUPPORTED_MESSAGE`, before opening
  any connection. That placeholder is what this cycle replaces.
- `_prune_stale_agent_jobs` (`app.py:10014`) deletes terminal jobs older than
  `NETWORK_AGENT_JOB_RETENTION_DAYS`. **Job rows are therefore not an audit
  trail.**
- The agent already enforces a host allowlist: it trusts the cloud for *which
  of its own devices* to act on, never for *where to connect*. This cycle
  extends that same "the agent does not fully trust the cloud" model.
- Agent is at 1.2.0 and reports a per-file content fingerprint.

## This cycle cannot be cloud-only

Every previous cycle avoided touching `agent/servicebills_agent.py`,
`mikrotik.py` and `vsol_olt.py`, so the owner never had to touch the on-prem
box. Adding an operation makes that impossible. The owner must hand-copy files
onto a Windows machine and restart the agent, and until they do, Settings will
report their connectors stale.

That cost shapes two decisions: **everything needing an agent edit lands in one
hand-copy**, and **the rate-limit cap is configuration rather than a constant**,
so changing it later does not cost another trip.

Riding along: `mikrotik.py`'s module docstring still names the deleted
`MikrotikServer` model. It was deferred from the previous cycle precisely to
travel with this one.

## Two operations, not one flag

The relayed operations are **`suspend_secret`** and **`unsuspend_secret`**, not
a single `set_secret_enabled` carrying an `enabled` boolean. Both map to the
same connector call on the agent side.

This is a security decision, not cosmetics. The rate limiter classifies by
**operation name**, which the agent has already validated against its own
allowlist, rather than by reading a parameter the cloud supplies. A compromised
cloud cannot disguise a suspend as an unsuspend by flipping a boolean the
limiter happens to trust. It also makes the audit trail legible.

Params for both: `{'pppoe_username': <str>}`. The direction is the operation.

## Agent changes

`AGENT_VERSION` goes to **1.3.0**.

`ALLOWED_OPERATIONS` gains `suspend_secret` and `unsuspend_secret`. The
dispatch in `execute_job` maps them to
`mikrotik.set_secret_enabled(server, params['pppoe_username'], False)` and
`... True)` respectively.

**The rate limiter.** A module-level deque of timestamps of accepted suspends.
Before dispatching a `suspend_secret`, the agent drops entries older than one
hour and refuses if the remaining count is at or above the cap, returning the
same `(ok=False, error, status)` shape any other refusal uses. The refusal is
logged at WARNING to `agent.log`.

Counting happens on **accepted attempts, not successes**. Counting successes
would let a compromised cloud burn unlimited failed attempts probing for a
username that exists.

`unsuspend_secret` is never counted and never limited.

The cap is read from `agent.toml` as `max_suspends_per_hour`, defaulting to 5,
parsed in `parse_config` alongside `poll_seconds`. A missing or unparseable
value falls back to the default rather than refusing to start — the same
"degrade, don't outage" rule `_configure_logging` and `_warn_if_world_readable`
already follow.

**Local logging.** Every write attempt and every rate-limit refusal is logged
to `agent.log`, naming the operation and the `pppoe_username`. A PPPoE username
is a customer identifier, not a credential, so this does not touch the standing
rule about credentials in logs — and the whole point is a record the cloud
cannot reach. No `logger.exception` or `exc_info=True`: the frame locals on
this path hold the device password.

## Cloud changes

`AGENT_OPERATIONS` gains both names. `DEVICE_TYPE_OPERATIONS['mikrotik_ccr']`
gains both; `vsol_olt` gains neither.

**Version gate.** The cloud refuses to queue a write for an agent that cannot
perform one, rather than queueing a job the box will reject. A
`MIN_AGENT_VERSION_FOR_WRITES = (1, 3, 0)` is compared against the agent's
reported `agent_version`, parsed into a tuple of ints. An agent reporting no
version, or a version that does not parse, is treated as too old. The refusal
names the problem: the on-prem agent needs updating.

**The two staff endpoints.** `suspend_customer_network` and
`unsuspend_customer_network` keep their paths and their direct-mode behaviour.
In agent mode they create a job through `_create_device_job` and return its id,
the same shape every other relayed call uses, so the frontend polls it exactly
as it polls a status check.

**The automatic unsuspend.** `_maybe_restore_mikrotik_access` runs after every
settling payment. In direct mode it keeps its current behaviour: read the
secret's status first, and only write if it is actually disabled. In agent mode
it **queues an unconditional `unsuspend_secret`** instead. The status check
exists to avoid a pointless write; in agent mode honouring it would cost a
second round trip on the billing path, and re-enabling an already-enabled
secret is a no-op on RouterOS, so the pointless write is the cheaper of the
two. It queues and returns immediately — it must never block payment
processing on the single synchronous worker.

## Audit

A new additive table records every relayed write. Job rows cannot serve: they
are pruned.

| column | purpose |
|---|---|
| `tenant_id` | scoping |
| `customer_id` | who was affected |
| `network_device_id` | which router |
| `pppoe_username` | what was actually acted on, as sent |
| `action` | `'suspend'` or `'unsuspend'` |
| `requested_by_user_id` | who asked; null for the automatic payment restore |
| `job_id` | the relaying job, nullable — it will be pruned later |
| `outcome` | `'queued'`, `'ok'` or `'failed'` |
| `message` | the connector's message on completion |
| `created_at` | when |

**Both modes are audited, and they fill the row differently.**

In **agent mode** the row is written when the job is created, with
`outcome='queued'` and the job's id, then updated to `'ok'` or `'failed'` with
the connector's message when the result posts back — the update belongs in
`agent_post_result`, the single place a relayed result enters the cloud.

In **direct mode** the connector has already run by the time the endpoint
returns, so the row is written once, terminal, with `job_id` null. A direct-mode
write is still a write and still gets audited; only the shape differs.

`_maybe_restore_mikrotik_access` writes a row in both modes, with
`requested_by_user_id` null — nobody clicked it, and recording that honestly
matters more than filling the column.

Together with the agent's own log line this gives two records of every relayed
write, one of which an attacker who owns the cloud cannot edit. A direct-mode
write has only the cloud-side record, which is correct: in direct mode the
cloud *is* the thing making the connection, so there is no second party to
corroborate it.

## Testing

- **Rate limiter:** the cap is reached, refuses, and recovers as the window
  rolls; unsuspends are unaffected at any volume; accepted-but-failed attempts
  still count. Time must be injectable or monkeypatched — no `sleep`.
- **Operation guard:** `suspend_secret` against a `vsol_olt` is rejected.
- **Version gate:** an agent at 1.2.0, and one reporting no version, are both
  refused before a job is created; 1.3.0 is accepted.
- **Both endpoints:** direct mode still writes inline and is unchanged; agent
  mode creates a job with the right operation and params.
- **Automatic restore:** direct mode still checks status first; agent mode
  queues unconditionally and does not block.
- **Audit:** a row per write, updated on result; survives a prune that removes
  the job.
- **No test may open a real socket.** `stub_connectors` in
  `tests/test_mikrotik_consolidation.py` already stubs `set_secret_enabled`;
  any new test creating a write job must use it.

Baseline to beat: 707 backend, 75 frontend.

## Out of scope

- Upstream portal writes. Different subsystem, blocked on async-worker infra.
- Auto-renew and auto-suspend policy — deciding *when* to suspend. That is a
  separate project the owner raised in the same conversation; this cycle only
  makes the action available to a human who has already decided.
- Any change to how expiry drift between ServiceBills and the upstream is
  modelled.

## Risks

**The owner does not hand-copy, and thinks the feature is broken.** Mitigated
by the version gate: the refusal says the agent needs updating rather than
failing obscurely.

**The cap fires during legitimate use.** At one-at-a-time reactive suspension
this should never happen, and the refusal is logged locally and returned to the
UI as a clear message rather than a generic error. If it does become a nuisance,
`agent.toml` is editable without a re-copy.

**A compromised cloud disconnects up to 5 customers an hour.** Accepted,
explicitly, as the cost of the feature. It is bounded, logged on the box, and
recorded in the audit table.
