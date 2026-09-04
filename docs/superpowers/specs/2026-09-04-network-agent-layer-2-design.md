# Network Agent (Layer 2) — Design

## Problem

Layers 1 and 3 shipped to production on 2026-09-04 (see [2026-09-01-network-topology-tree-design.md](2026-09-01-network-topology-tree-design.md)). They work, but only from a machine on DeltaNet's LAN.

ServiceBills runs on Render. DeltaNet's devices — the MikroTik CCR at `192.168.8.1` (RouterOS API, TCP 8728) and the V-SOL OLT at `192.168.8.100` (SNMP, UDP 161) — are on a private LAN with no inbound route from the internet. Every device check from production therefore returns "unreachable". The Network Tree page deploys and renders, but can never populate.

This spec covers Layer 2 of the broader vision (see `[[project_network_ai_diagnostic_agent_vision]]` in memory): a local agent on DeltaNet's LAN that performs device calls on the cloud's behalf. Layer 4 (fault diagnosis) remains a separate, later cycle.

## What has to be relayed

Six connector functions touch LAN devices, across eight call sites in `app.py`:

| Function | Kind |
|---|---|
| `mikrotik.test_connection(server)` | read |
| `mikrotik.get_device_health(server)` | read |
| `mikrotik.get_secret_status(server, pppoe_username)` | read |
| `mikrotik.get_active_session(server, pppoe_username)` | read |
| `vsol_olt.get_olt_status(server)` | read |
| `mikrotik.set_secret_enabled(server, pppoe_username, enabled)` | **write** |

**Only the five reads are in scope.** The write — which disables or enables a customer's PPPoE secret on the CCR — is deliberately excluded, so that a compromise of the cloud can read DeltaNet's network but never disconnect a customer. This matches the vision document's own principle of a read-only diagnostic tool layer. Adding the write later is an additive change that must carry its own threat review.

## Decisions taken before design

Four decisions shaped everything below and are recorded here so they are not silently revisited:

1. **A dedicated always-on box exists** on the LAN to host the agent. It runs **Windows**.
2. **Read-only.** The PPPoE write is out of scope (above).
3. **Device credentials live only on the agent — for agent-mode tenants.** The cloud never stores or receives the CCR's RouterOS password or the OLT's SNMP community for a tenant in `agent` mode. Production's `network_device` table currently has zero rows, so there is no legacy to migrate.

   The `NetworkDevice.password` column stays, because `direct` mode still needs it: that is how local development and every other tenant work today, and this design does not change them. The rule is therefore per-mode, not global — in `agent` mode the column is simply left empty and the create/edit form stops requiring it. Making the column's meaning depend on the tenant's mode is a real wrinkle, accepted because the alternative (two credential columns, or a second device table) is worse.
4. **Outbound job queue, agent polls.** Chosen over a VPN overlay and over a tunnel + synchronous HTTPS. See Rejected Alternatives.

## Architecture

The agent reuses the existing connectors verbatim. `mikrotik.py` and `vsol_olt.py` already accept a duck-typed `server` object exposing `host`, `api_port`, `use_tls`, `username`, `password`, `id`, `last_checked_at` and `last_status`. The agent constructs that object from its **own local config**. No connector code is forked, duplicated, or modified.

```
Browser → Cloud (Render)                    Agent (DeltaNet LAN, Windows)
   │         │                                    │
   │  enqueue│  NetworkAgentJob(pending)          │
   │◄────────┤  returns job_id immediately        │
   │         │                            poll ──►│  (agent initiates; nothing inbound)
   │         │◄─── claim job ─────────────────────┤
   │         │                                    │  runs mikrotik/vsol_olt locally
   │         │                                    │  with LOCAL credentials
   │         │◄─── POST result ───────────────────┤
   │  poll   │                                    │
   │◄────────┤  job done + result                 │
```

Nothing connects inbound to the LAN. The agent initiates every connection outbound over HTTPS, so no firewall rule, port forward, or static IP is required — which matters, because a firewall/IP problem is already the open blocker on the Smart Networks upstream (see `[[project_network_enforcement_initiative]]`).

### Polling, not long-polling

The agent **short-polls every 2 seconds**. It must not long-poll.

Production runs a single synchronous gunicorn worker (`Dockerfile` CMD uses `-w ${WEB_CONCURRENCY:-1}` and `render.yaml` does not set `WEB_CONCURRENCY`). An agent holding a 25-second long-poll would occupy the only worker and freeze the entire application — the exact failure this design exists to avoid. Each short poll is a fast, unblocking request, and ~2s of added latency is negligible against an SNMP walk that already takes seconds.

This same single-worker constraint is why job creation returns immediately rather than waiting for the result.

## Cloud components

### `NetworkAgent` (new model, tenant-scoped)

```python
id, tenant_id, name,
token_hash,        # the token itself is shown once at creation and never stored
last_seen_at,      # stamped on every poll -- doubles as the heartbeat
agent_version,     # reported by the agent, for support
created_at
```

Added to `TENANT_OWNED_MODELS` **and** to `_TENANT_DELETE_ORDER`. Both — omitting the second is exactly the gap that caused a `ForeignKeyViolation` on Postgres during the Layer 1/3 merge, caught only by `test_lifecycle`.

One agent per tenant. Multiple agents and failover are out of scope.

### `NetworkAgentJob` (new model, tenant-scoped)

```python
id, tenant_id,
device_id,         # FK -> network_device.id
operation,         # String(30): one of the five read operations
params,            # JSON, nullable -- e.g. {"pppoe_username": "..."} where the op needs it
status,            # 'pending' | 'claimed' | 'done' | 'failed' | 'expired'
result,            # JSON, nullable -- the connector's success value
error,             # Text, nullable -- the connector's failure message
requested_by_user_id,
created_at, claimed_at, finished_at
```

Index on `(tenant_id, status, created_at)` — the agent's poll query filters exactly on those.

`operation` is a string discriminator, matching the `device_type` and `UpstreamProvider.product` convention already used in this codebase.

### Agent-facing endpoints

Authenticated by **agent token**, not user JWT: `Authorization: Bearer <token>`, verified against `token_hash`. The tenant is resolved from the token and never read from the request body. The agent also sends `X-Agent-Version`, which is recorded on the `NetworkAgent` row.

- `GET /api/agent/jobs` — returns the oldest `pending` job for this tenant and marks it `claimed`, or `204 No Content`. Stamps `last_seen_at`. The response carries everything the agent needs to act and validate, and **no credentials**:

  ```json
  {
    "job_id": 41,
    "device_id": 2,
    "operation": "olt_status",
    "host": "192.168.8.100",
    "api_port": 161,
    "params": {}
  }
  ```

  `host` and `api_port` come from the `NetworkDevice` row. They are sent so the agent can verify them against its own config — not so it can trust them. See Agent-side validation.

- `POST /api/agent/jobs/<id>/result` — body is `{"ok": bool, "result": <json>|null, "error": <string>|null}`. Rejects a job belonging to another tenant, or one not in `claimed` state.

### Endpoints that change

`check-now`, `network-tree/olt/<id>/refresh` and the label-matcher GET currently call a connector inline. All three become job-based, plus one new endpoint the browser polls:

- `GET /api/network-jobs/<job_id>` — returns `{status, result, error}` for a job in the caller's tenant.

**These endpoints return a `job_id` in both modes.** In `direct` mode the cloud runs the connector inline and stores an already-`done` job before responding. This gives the frontend a single code path — enqueue, poll, render — instead of forked logic, and preserves today's behaviour exactly for local development and for every other tenant.

### Access mode

One new column on `BusinessSettings`:

```python
network_access_mode  # String(10), NOT NULL, default 'direct' -- 'direct' | 'agent'
```

`direct` is today's behaviour and the default, so nothing changes for any existing tenant or for local development. DeltaNet's production tenant is set to `agent`.

An explicit setting is used rather than inferring the mode from "is an agent online", because implicit switching would make an agent outage look like a behaviour change rather than an outage.

### Migration

The two new tables and the one new column ship in **a single Alembic migration**, purely additive — no drops, no type changes. Its `down_revision` must be the head at the time it is generated (`6129b0fb0885` today, unless another migration lands first).

Autogenerate on this codebase has a documented habit of emitting spurious operations for pre-existing schema drift; anything beyond these two `create_table` calls and one `add_column` must be deleted from the generated file.

## The agent

A small standalone Python program in a new `agent/` directory of this repository. It imports `mikrotik.py` and `vsol_olt.py` from the repo root. It has no Flask, no database, and no dependency on `app.py` — importing `app.py` would drag in SQLAlchemy, APScheduler and the whole application, which the agent has no use for.

Dependencies: `requests`, plus the connectors' own `librouteros` and `pysnmp>=7,<8`. Shipped as `agent/requirements-agent.txt`, separate from the application's `requirements.txt`.

### Loop

1. Poll `GET /api/agent/jobs`. On `204`, sleep 2s and repeat.
2. On a job: look up the device in local config **by cloud device id**.
3. Validate the job (see Agent-side validation). On any validation failure, POST an error result — never execute.
4. Build a `types.SimpleNamespace` server object from local config and call the connector.
5. POST the result. A connector failure is a normal result (`ok: false` plus its message), not an agent error.
6. Repeat.

### Configuration

`C:\ProgramData\ServiceBillsAgent\agent.toml`:

```toml
cloud_url = "https://servicebills.onrender.com"
token     = "<agent token, shown once at creation>"
poll_seconds = 2

[[device]]
id       = 1                 # the cloud's NetworkDevice.id
host     = "192.168.8.1"     # must match the job's host, or the job is refused
type     = "mikrotik_ccr"
api_port = 8728
use_tls  = false
username = "admin"
password = "<RouterOS password>"

[[device]]
id       = 2
host     = "192.168.8.100"
type     = "vsol_olt"
api_port = 161
password = "<SNMP community>"
```

The file holds credentials, so its ACL must grant read only to `SYSTEM` and `Administrators`. Setting that ACL is part of the install procedure and is verified by the agent at startup: if the file is readable by `Users`, the agent logs a prominent warning and continues (it refuses to start only on a missing or malformed file, so a permissions mistake degrades to a warning rather than an outage).

### Agent-side validation

The agent trusts the cloud for **which of its own devices to check** — never for anything else. Three checks, each refusing the job with an error result rather than executing:

1. **Device allowlist.** The job's `device_id` must exist in local config.
2. **Host match.** The job's host must equal the configured host for that device id. Without this, a compromised cloud could point the agent at an attacker-controlled host and harvest the CCR password on the first connection.
3. **Operation allowlist.** The operation must be one of the five reads. Even though the write is not implemented cloud-side, the agent refuses it independently — defence in depth at the boundary that actually holds the credentials.

### Windows deployment

Run via **Task Scheduler**, which is built into Windows and avoids a third-party service wrapper:

- Trigger: **At startup**
- Run as: **SYSTEM** (no stored user password, and it matches the config file's ACL)
- Settings: *Run whether user is logged on or not*; *If the task fails, restart every 1 minute*; *Do not stop the task*

Requires Python 3.11+ installed on the box. Packaging the agent as a single `.exe` with PyInstaller would remove that requirement and is a reasonable later improvement, but it adds a build step and is not needed for one box.

Task Scheduler does not usefully capture stdout, so the agent logs to `C:\ProgramData\ServiceBillsAgent\agent.log` through a `RotatingFileHandler` (5 files × 2 MB).

**Credential-logging rule.** The agent must never call `logger.exception` on any path whose frame locals can hold a credential. This is not hypothetical: during the Layer 1/3 build, `logger.exception` in `vsol_olt.py` was found to ship the decrypted SNMP community to Sentry, because Sentry's `LoggingIntegration` captures frame locals at ERROR level. `logger.warning(..., exc_info=True)` is the approved form. The agent does not initialise Sentry at all, but the rule holds regardless — a log file with the router password in a traceback is its own problem.

## Failure modes

All resolved **lazily, when `GET /api/network-jobs/<job_id>` is polled**. No scheduled job is added. Beyond avoiding needless load, this deliberately steers clear of the in-process APScheduler, which was found during the production deploy to fire its jobs while `flask db upgrade` is still running.

| Situation | Behaviour |
|---|---|
| No agent has ever connected, or `last_seen_at` is older than 30s | The enqueue endpoint refuses immediately: "Agent offline — last seen X." No job is created, because nothing would run it. |
| Job `pending` for more than 30s | Marked `expired` on read. UI: the agent did not pick it up. |
| Job `claimed` for more than 120s with no result | Marked `failed` on read. The agent probably died mid-walk. |
| Connector failed (OLT unreachable, wrong community, auth failure) | The agent posts `{ok: false, message}`. The job is `done` with `error` set. The UI already renders exactly this shape, so nothing downstream changes. |
| Agent token invalid or revoked | `401`. The agent logs it and backs off to a 30s poll interval rather than hammering. |
| Cloud unreachable from the agent | The agent logs and retries with backoff. No state is lost; jobs simply expire and the user is told the agent is offline. |

## UI

- **Agent status** on the Network Devices and Network Tree pages: online (with `last_seen`) or offline. When offline, the check buttons are disabled with the reason shown, rather than failing on click.
- **Check Now / Load ONUs / Match Labels** enqueue a job and poll `GET /api/network-jobs/<job_id>` every second until it reaches a terminal state, then render exactly as they do today.
- **Agent management** in Settings: create an agent, see its `last_seen_at` and version, and regenerate its token. The token is displayed once on creation and never again.

## Label matcher

Its walk becomes a job like any other, but proposal matching stays in the cloud because it needs `Customer` rows. The job carries the raw ONU list; when the job completes, the cloud computes proposals from the stored result. The matching logic itself — the exact-match rule, `_LABEL_FUZZY_MIN_LENGTH`, the greedy pairing — is unchanged.

## Testing

Entirely offline. No real device, no real network, no real cloud.

**Agent** (`tests/test_network_agent.py`): the HTTP layer monkeypatched to a fake cloud and the connectors monkeypatched. Covers claiming a job and dispatching to the right connector; refusal of an unknown `device_id`; refusal of a host that does not match config; refusal of an operation outside the allowlist; a connector failure being posted as `{ok: false}` rather than crashing the loop; and backoff on `401`.

**Cloud** (`tests/test_network_agent_api.py`): job lifecycle end to end; **tenant isolation on every agent endpoint** — an agent token for tenant A must never see, claim, or post results to tenant B's jobs; enqueue refused when no agent is online; lazy expiry of both `pending` and `claimed` jobs; `direct` mode still returning a completed job inline; and token verification against the stored hash.

**Model tests**: `NetworkAgent` and `NetworkAgentJob` in `TENANT_OWNED_MODELS` and `_TENANT_DELETE_ORDER`, and the `network_access_mode` default.

**Migration**: extends the existing `tests/test_topology_migration.py` pattern, which runs a real Alembic upgrade/downgrade/upgrade against a temporary SQLite file.

## Out of scope

- **The PPPoE write** (`set_secret_enabled`). Decided above.
- **Multiple agents per tenant, or failover.** One agent, one box.
- **Agent auto-update.** Updating means pulling the repo and restarting the task.
- **Push transport** (WebSockets, SSE). Polling is sufficient at this scale and costs far less complexity.
- **Layer 4 fault diagnosis.** Still a separate cycle.
- **PyInstaller packaging.** Noted as a later improvement.
- **Generalising to other tenants' topologies.** The models are tenant-scoped so this is not foreclosed, but no second tenant is being designed for.

## Rejected alternatives

**VPN overlay (Tailscale subnet router).** The cheapest option on paper — the on-prem box advertises `192.168.8.0/24`, a Tailscale client runs in the Render container, and no connector or call-site code changes at all. Rejected because it is incompatible with decision 3: under a VPN the *cloud process* makes the SNMP and RouterOS calls, so the cloud must hold the device credentials. It also leaves the single-worker blocking problem untouched, and puts a third-party network operator in the path to the core router.

**Tunnel plus synchronous HTTPS (Cloudflare Tunnel).** The agent exposes an authenticated endpoint through a tunnel and the cloud calls it inline. Keeps credentials on-prem and needs the least application code, since every call site keeps its current shape. Rejected because it blocks the single gunicorn worker for the full duration of each device call, adds a third-party tunnel to the trust path, and creates a publicly resolvable hostname that must then be separately locked down. The job queue avoids all three and, unlike this option, fixes the blocking problem rather than inheriting it.
