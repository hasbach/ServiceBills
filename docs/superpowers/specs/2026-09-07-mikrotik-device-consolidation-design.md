# Consolidating MikrotikServer into NetworkDevice

**Date:** 2026-09-07
**Status:** approved by the owner, ready for an implementation plan
**Base:** `main` = `ce8cb18` (deployed), Alembic head `d9e2b7c4a815`

## Goal

Make a router one row instead of two, and wire up the half of the agent relay
that has never been reachable.

## Why

`AGENT_OPERATIONS` has six entries. Only three are ever created by any cloud
path:

| operation | cloud callers |
|---|---|
| `olt_status` | 3 — check-now, tree refresh, Load ONUs |
| `device_health` | 1 — check-now on non-OLT devices |
| `cpe_locations` | 1 — Locate Customers |
| `test_connection` | **0** |
| `secret_status` | **0** |
| `active_session` | **0** |

The last three are fully implemented on both sides — allowlisted in the agent,
dispatched by `execute_job`, handled by `_run_device_operation_direct`, and
treated as opaque blobs by `_validate_agent_result` — and nothing queues them.

They are dead because all three concern a customer on a PPPoE router, and this
codebase models that router as `MikrotikServer`, while the relay only
understands `NetworkDevice`. The two models are near-duplicates:
`MikrotikServer` is `NetworkDevice` minus `interface_labels` / `device_type` /
`parent_device_id`, plus `service_name`.

The consequence is double entry. DeltaNet's CCR would have to be registered
twice — once on Network Devices for the tree and health checks, once on
Mikrotik Servers for customer PPPoE — as two rows, with two copies of the
password, only one of which the agent can reach.

It also makes the spec's Decision 3 ("the cloud never stores the CCR's RouterOS
password in agent mode") false as written: it holds for `NetworkDevice` and not
for `MikrotikServer`, which is the same physical router. Consolidation makes
that statement true by construction rather than by wording.

## Decisions already made — do not revisit

1. **Consolidate onto `NetworkDevice`.** Chosen over teaching the relay about
   `MikrotikServer`, and over deleting the three dead operations.
2. **Reads now, writes later.** Relay `test_connection`, `secret_status` and
   `active_session`. Do *not* relay `set_secret_enabled` (suspend/unsuspend) in
   this cycle. It stays inline and gets its own design round with its own
   authorization story, because relaying a write breaks the read-only property
   the agent was built around: a cloud compromise must not be able to
   disconnect a customer.
3. **Scope is cleanup, not enablement.** Remove the duplicate model and make the
   relay coherent. Customer→device linking UX and importing the CCR's 64
   `/ppp/secret` entries are explicitly out of scope.

## Hard constraint: the agent binary does not change

Every operation this cycle needs already exists in the shipped agent 1.2.0 —
`ALLOWED_OPERATIONS` lists all six and `execute_job` dispatches all six. This
cycle is therefore **cloud-only**.

This is a requirement, not a coincidence. Changing the agent means the owner
must copy three files onto the on-prem box by hand and restart it, and the
connector-fingerprint check would show their agent stale until they did. Any
design that requires an agent change is wrong for this cycle — which is why
customer status uses two existing operations rather than one new combined one.

## Established facts

Verified in code and against real hardware on 2026-09-07. Do not re-derive.

- `_create_device_job(device, operation, params=None)` is the only job factory.
  Call sites: `app.py` 9324, 10271, 10394, 10554. In direct mode it runs the
  connector inline and returns an already-terminal job, so callers and the
  frontend have one shape.
- `NETWORK_DEVICE_TYPES = ('mikrotik_ccr', 'vsol_olt')` — a closed, validated
  set of exactly two, enforced on device create and update.
- Seven `MikrotikServer` call sites run inline from Render today:
  `_maybe_restore_mikrotik_access` (`get_secret_status` 4360,
  `set_secret_enabled(True)` 4365), `test_mikrotik_connection`
  (`test_connection` 9140), `get_customer_mikrotik_status`
  (`get_secret_status` 10612, `get_active_session` 10613),
  `suspend_customer_mikrotik` (`set_secret_enabled(False)` 10629) and its
  unsuspend counterpart (10639).
- `Customer.mikrotik_server_id` is a nullable FK to `mikrotik_server.id`
  (app.py:646), with a `customers` backref at 338.
  `_check_network_link_conflict(exclude_customer_id, mikrotik_server_id,
  pppoe_username, ...)` enforces uniqueness of the pair.
- `MikrotikServer` is registered in the per-tenant table list at app.py:1486.
- Roadmap step 1 is **proven**, both legs, against the real CCR at
  192.168.100.1:8728. Direct: `get_device_health` returned `ok=True`,
  `last_status='online'`, identity MikroTik, uptime 3w2d8h, 15 interfaces.
  Through the relay: check-now returned in 37 ms, the agent executed the job,
  `status=done`, `error=None`, `last_status='online'`, with the cloud-side
  password column empty throughout.
- DeltaNet's CCR terminates PPPoE locally as well as dialling upstream:
  `/ppp/secret` = 64, `/ppp/active` = 2, four PPPoE **server** instances on
  bridge1 (`BCH` enabled; `BCHVPN`, `MYISP`, `SMN-W` disabled), three outbound
  `pppoe-client` interfaces. **This is why `service_name` matters** — with four
  server instances a blank service name can match the wrong one.
- **Production has no data to migrate.** The owner confirmed via super-admin:
  no Mikrotik servers configured on any tenant, the CCR appears only under
  Network Devices, and no customer is linked to a Mikrotik server. The
  migration is nonetheless written to carry rows across if any exist — see
  below for why.

## Data model

**`NetworkDevice` gains one column:**

```python
service_name = db.Column(db.String(100), nullable=True)
```

Optional on any device. Setting it is what makes PPPoE lookups well-defined.

**No new `device_type`.** DeltaNet's CCR is genuinely both an upstream dialler
and a PPPoE server, so a type forcing one or the other would encode a false
fact. `device_type` stays a statement about which connector to use;
`service_name` being set is what says "this router also answers PPPoE
questions".

**`Customer.mikrotik_server_id` becomes `Customer.network_device_id`**, a
nullable FK to `network_device.id`. Renamed rather than re-pointed: the column
is null for every row in production, so the rename is free, and a column named
`mikrotik_server_id` pointing at `network_device` would be a permanent trap.

**`MikrotikServer` is deleted** — the model class, its entry in the per-tenant
table list, and its `customers` backref (which moves to `NetworkDevice`).

## Migration

One new revision, `down_revision = 'd9e2b7c4a815'`.

Production's real schema disagrees with migration history in both directions,
so every step inspects before acting: `inspect(op.get_bind())` to check whether
a table or column is actually present, and skip the step if reality already
matches the target.

**upgrade()**

1. Add `network_device.service_name` if absent.
2. Add `customer.network_device_id` (nullable, FK, indexed) if absent.
3. If the `mikrotik_server` table exists:
   a. Add a temporary `network_device.migrated_from_mikrotik_server_id`
      (Integer, nullable). `INSERT ... RETURNING` cannot report the source row's
      id, so this column is how new rows are correlated back to old ones.
   b. `INSERT INTO network_device (...) SELECT ... FROM mikrotik_server`,
      supplying `device_type = 'mikrotik_ccr'` and `interface_labels = '{}'`
      explicitly — both are `nullable=False` with Python-side defaults that a
      raw SQL insert does not get.
   c. `UPDATE customer SET network_device_id = nd.id FROM network_device nd
      WHERE nd.migrated_from_mikrotik_server_id = customer.mikrotik_server_id`.
   d. Drop the temporary column.
4. Drop `customer.mikrotik_server_id` if present.
5. **Drop `mikrotik_server` only if it was empty.** If it had rows, the data has
   been copied forward and the old table is left in place as a backup, with a
   printed note saying so.

Step 5 is the important one. The deploy runs `flask db upgrade && exec
gunicorn`, so a migration that raises takes the entire application down rather
than merely misbehaving — refusing to proceed on an unexpected row is not an
available option. Copying the rows forward and keeping the original satisfies
both constraints: the deploy succeeds, and nothing is destroyed.

**downgrade()**

Recreates `mikrotik_server`, re-adds `customer.mikrotik_server_id`, drops
`network_device.service_name` and `customer.network_device_id`.

It does **not** restore rows into `mikrotik_server`. That is lossless for the
zero-row case, which is the case that exists. It is stated plainly in the
migration's docstring rather than left for someone to discover.

No `logger.exception` or `exc_info=True` anywhere in the migration: the copied
rows carry encrypted device passwords, and Sentry's `LoggingIntegration`
captures frame locals at ERROR.

## API

**Deleted** — all five `MikrotikServer` endpoints:

- `GET /api/mikrotik-servers`
- `POST /api/mikrotik-servers`
- `PUT /api/mikrotik-servers/<id>`
- `DELETE /api/mikrotik-servers/<id>`
- `POST /api/mikrotik-servers/<id>/test-connection`

**Changed** — network device endpoints accept and return `service_name` on
create, update and `to_dict`.

**New** — `POST /api/network-devices/<id>/test-connection`, which calls
`_create_device_job(device, 'test_connection')`. This is the one genuinely
useful thing the deleted page had; it moves rather than disappearing.

**Changed** — `GET /api/customers/<id>/mikrotik-status` becomes
`POST /api/customers/<id>/network-status`. It creates **two** jobs and returns
their ids:

```json
{"ok": true, "jobs": {"secret": 41, "session": 42}}
```

POST because it now creates state. Two jobs rather than one combined operation
because a new operation would require an agent change — see the hard
constraint. The frontend polls both through the existing
`GET /api/network-jobs/<id>` and merges the results. In direct mode both jobs
come back already terminal, so the first poll answers immediately and the
frontend needs only one code path.

Cost in agent mode: the agent handles one job per 2-second poll, so a status
check resolves in up to ~4 seconds. Acceptable for a manual click.

**Renamed and repointed, unchanged in shape** —
`POST /api/customers/<id>/mikrotik-suspend` becomes `.../network-suspend`, and
`mikrotik-unsuspend` becomes `.../network-unsuspend`. Renamed for the same
reason as the status route: leaving `mikrotik-` prefixed paths behind after the
`MikrotikServer` model is gone is exactly the stale naming this cycle removes,
and all three are being edited anyway.

**Internal, repointed** — `_maybe_restore_mikrotik_access` resolves a
`NetworkDevice`. `_check_network_link_conflict`'s `mikrotik_server_id`
parameter becomes `network_device_id`; it keeps enforcing uniqueness of
(device, `pppoe_username`).

**Customer payload** — the `mikrotik_server_id` key in the customer JSON
(app.py 3113 and 3623) and in create/update input (3227, 3273, 3508-3529)
becomes `network_device_id`.

## Suspend and unsuspend fail honestly

These stay inline. In agent mode today they would call the router from Render,
find it unreachable, and hang until the connector times out.

They will instead refuse immediately, before opening a connection, with a
message saying the action needs a direct connection to the router and is not
yet available through the on-prem agent. An honest, fast refusal beats a
13-second timeout that looks like a network fault, and it names the gap the
writes cycle will close.

`_maybe_restore_mikrotik_access` takes the same guard. It already swallows its
own exceptions and returns a status dict, so the billing path it runs after is
unaffected either way — but it should skip cleanly rather than burn a timeout
on every payment.

## Operation guard

`_create_device_job` currently accepts any operation in `AGENT_OPERATIONS` for
any device. A `secret_status` job against an OLT is a job nobody can serve, and
with the three read operations becoming reachable this stops being theoretical.

```python
DEVICE_TYPE_OPERATIONS = {
    'vsol_olt': ('olt_status', 'cpe_locations'),
    'mikrotik_ccr': ('device_health', 'test_connection',
                     'secret_status', 'active_session'),
}
```

`NETWORK_DEVICE_TYPES` is a closed set of exactly two, so this mapping is
total — there is no fallback branch to get wrong. An unsupported pairing is
rejected with a message naming both the device type and the operation.

## Frontend

**Deleted:** `MikrotikServerManagementView.js` and the `mikrotik-servers` nav
entry in `App.js`.

**`NetworkDeviceManagementView.js` gains:** a **Service name** field on the
device form (shown only for `mikrotik_ccr`, with helper text explaining it
disambiguates when a router runs more than one PPPoE server instance), and a
**Test connection** action on each Mikrotik device row.

**`SubscriptionsView.js`:** the customer form's device dropdown now lists
network devices instead of Mikrotik servers, and the field is
`network_device_id`. It stays gated on `network_mode === 'local_mikrotik'`, as
does the customer status panel.

**`network_mode` keeps its meaning.** It no longer gates a menu entry, because
Network Devices is always visible to admin/finance. It continues to gate the
customer form's device and `pppoe_username` fields and the status panel — that
is, it still answers "do my customers authenticate on my own router?"

**Nothing visible changes for DeltaNet.** They are on `upstream_bridge`, so the
Mikrotik Servers entry is not in their sidebar today. The only change they will
see is the two additions to the Network Devices form.

## Testing

- **Migration:** upgrade/downgrade/upgrade in the style of
  `tests/test_topology_migration.py` — bootstrap with `create_all` + `stamp`,
  never walk the chain (origin's `bd054e2e7cf9` calls
  `op.create_unique_constraint` outside batch mode, which SQLite rejects).
  Cover both branches explicitly: the empty-table path drops
  `mikrotik_server`; a seeded row is copied into `network_device`, its
  customer's FK is repointed to the new id, and the old table survives.
- **Operation guard:** every valid pairing is accepted and at least one invalid
  pairing per device type is rejected with a message naming both.
- **Relay wiring:** each of the three newly-reachable operations creates a job
  in agent mode and returns a terminal job in direct mode.
- **Customer status:** returns two job ids; both resolve; the direct-mode
  response is already terminal.
- **Honest refusal:** suspend and unsuspend in agent mode refuse without
  attempting a connection.
- **Regression:** the deleted endpoints return 404, and no test still imports
  `MikrotikServer`.

Frontend tests must be run with an explicit pattern —
`cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch
"**/<file>.test.js"` — because the dot-prefixed `.worktrees` path segment makes
the plain command report "No tests found", which is indistinguishable from
passing. Build check is `cd frontend && npx react-scripts build` **without**
`CI=true`.

Baseline to beat: 684 backend, 61 frontend.

## Out of scope

- Relaying `set_secret_enabled` (suspend/unsuspend). Its own cycle.
- Customer→device linking UX, and importing the CCR's 64 `/ppp/secret` entries.
- Any change to `agent/servicebills_agent.py`, `mikrotik.py` or `vsol_olt.py`.
- The wrapped-level lighting gap in the network tree.

## Risks

**The table is not actually empty.** Mitigated by design: rows are copied
forward and the original is kept. The failure mode is a leftover table, not
lost data or a failed deploy.

**Another tenant is mid-flight using Mikrotik Servers.** The owner confirmed no
tenant uses the feature. If one did, their configuration migrates and their
customers' links follow; what they lose is the dedicated page, which is
replaced by Network Devices.

**Renaming the customer FK breaks an external consumer.** There is none — a
single first-party SPA consumes this API.
