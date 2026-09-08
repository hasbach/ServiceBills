# Relaying PPPoE Writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let staff suspend and unsuspend a customer's PPPoE secret through the on-prem agent, without handing a compromised cloud the ability to disconnect everyone.

**Architecture:** Two new relayed operations, `suspend_secret` and `unsuspend_secret`, both mapping to `mikrotik.set_secret_enabled`. The agent enforces a rolling-hour cap on suspends only, on the box, where the cloud cannot lift it. The cloud refuses to queue a write for an agent too old to perform one, and records every write in a durable audit table that survives job pruning.

**Tech Stack:** Python 3.11+ (agent), Flask + SQLAlchemy, hand-written Alembic, React 18 + MUI (CRA), pytest, Jest.

## Global Constraints

- **This cycle is NOT cloud-only.** It edits `agent/servicebills_agent.py` and `mikrotik.py`, so the owner must hand-copy files onto a Windows box and restart the agent. Everything needing an agent edit lands in ONE hand-copy — do not defer an agent-side change to a later cycle.
- **`mikrotik.py`'s module docstring is the ONLY change permitted to `mikrotik.py`.** Do not touch `vsol_olt.py` at all.
- **The relayed operations are `suspend_secret` and `unsuspend_secret` — never one operation carrying an `enabled` boolean.** This is a security decision: the rate limiter classifies by an operation name the agent has already validated against its own allowlist, not by a parameter the cloud supplies.
- **The suspend counter increments on accepted attempts, not successes.** Counting successes would let a compromised cloud burn unlimited failed probes.
- **Unsuspends are never counted and never rate-limited.** Restoring service is not the attack, and the payment-triggered restore must keep working unattended.
- **Never** `logger.exception` or `exc_info=True` on any path whose frame locals can hold a device credential — Sentry's `LoggingIntegration` captures frame locals at ERROR. This includes every new write path.
- **NO TEST MAY OPEN A REAL SOCKET.** The hosts in these tests are DeltaNet's live addresses and `192.168.100.1` answers from the dev machine's LAN. `stub_connectors(monkeypatch)` in `tests/test_mikrotik_consolidation.py` already stubs `set_secret_enabled`; any test creating a write job must use it.
- **Rate-limiter tests must inject or monkeypatch time. Never `sleep`.**
- Backend tests: `python -m pytest -q` from the worktree root. Baseline **707**; you must beat it and end green.
- Frontend tests need an explicit pattern: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/*.test.js"`. The bare command reports "No tests found", indistinguishable from passing. Baseline **75**.
- Build check is `cd frontend && npx react-scripts build` **without** `CI=true`. Afterwards revert artifacts: `git checkout -- frontend/build build` and `git clean -fdxq frontend/build build` (note `-x`), then confirm `git status --short` shows neither.
- Migration must inspect before acting — production's real schema disagrees with migration history in both directions. The container runs `flask db upgrade && exec gunicorn`, so a raising migration is a full outage.
- Alembic head is `f2b6c9d4e703`; the new revision must become the only head. Migration tests bootstrap with `create_all` + `stamp`, never walking the chain — six examples in `tests/test_topology_migration.py`.
- `Customer` has NOT NULL columns beyond `name`: `phone`, `address`, `subscription_plan_id` (needs a real `SubscriptionPlan` row), `subscription_expiry_date`. `BusinessSettings` requires `business_name`, `address`, `mobile`.
- Do not use `git stash` — the stack is shared across worktrees.
- Nothing under `frontend/build/` or `build/` may be committed.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `agent/servicebills_agent.py` | Two write operations, the rolling-hour suspend cap, config parsing, local logging. | 1 |
| `agent/agent.example.toml` | Document `max_suspends_per_hour`. | 1 |
| `agent/README.md` | Document the setting and what a refusal looks like. | 1 |
| `mikrotik.py` | Module docstring only. | 1 |
| `tests/test_network_agent_program.py` | Agent-side tests, including the limiter. | 1 |
| `app.py` | Allowlists, version gate, audit model, the three call sites. | 2–4 |
| `migrations/versions/a4e17c92f88b_add_network_write_audit.py` | **Create.** The audit table. | 3 |
| `tests/test_relay_writes.py` | **Create.** Cloud-side tests for this cycle. | 2–4 |
| `tests/test_topology_migration.py` | **Append** one migration test. | 3 |
| `frontend/src/components/SubscriptionsView.js` | Poll the job in agent mode. | 5 |

---

### Task 1: The agent performs and caps writes

The whole agent-side change, in one hand-copy. Independently testable with no cloud change at all.

**Files:**
- Modify: `agent/servicebills_agent.py`
- Modify: `agent/agent.example.toml`, `agent/README.md`, `mikrotik.py` (docstring only)
- Modify: `tests/test_network_agent_program.py`

**Interfaces:**
- Produces: `AGENT_VERSION == "1.3.0"`; `ALLOWED_OPERATIONS` including `"suspend_secret"` and `"unsuspend_secret"`; `_claim_suspend_slot(cap, now=None) -> bool`; `_recent_suspends` (a `collections.deque` of float timestamps, cleared by tests); `config["max_suspends_per_hour"]`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing limiter tests**

Append to `tests/test_network_agent_program.py`:

```python
def test_the_suspend_window_admits_up_to_the_cap(monkeypatch):
    """The cap is the whole compensating control for relaying writes, so it is
    tested against an injected clock rather than a sleep."""
    agent._recent_suspends.clear()
    now = 1_000_000.0
    assert [agent._claim_suspend_slot(3, now=now + i) for i in range(5)] == [
        True, True, True, False, False]


def test_the_window_rolls_forward(monkeypatch):
    agent._recent_suspends.clear()
    now = 1_000_000.0
    for i in range(3):
        assert agent._claim_suspend_slot(3, now=now + i) is True
    assert agent._claim_suspend_slot(3, now=now + 10) is False
    # One hour after the first slot, exactly one slot frees up.
    assert agent._claim_suspend_slot(3, now=now + 3601) is True
    assert agent._claim_suspend_slot(3, now=now + 3601) is False


def test_a_refused_suspend_does_not_consume_a_slot():
    """Otherwise a compromised cloud could lock the window open by hammering
    it -- the refusal itself would keep pushing the window forward."""
    agent._recent_suspends.clear()
    now = 1_000_000.0
    for i in range(2):
        agent._claim_suspend_slot(2, now=now + i)
    for i in range(10):
        assert agent._claim_suspend_slot(2, now=now + 100 + i) is False
    assert len(agent._recent_suspends) == 2


def test_suspend_is_rate_limited_but_unsuspend_is_not(monkeypatch):
    """Restoring service is not the attack, and the payment-triggered restore
    must keep working unattended."""
    agent._recent_suspends.clear()
    calls = []
    monkeypatch.setattr(agent.mikrotik, "set_secret_enabled",
                        lambda s, u, enabled: calls.append((u, enabled)) or (True, "ok"))
    config = dict(CONFIG, max_suspends_per_hour=2)

    outcomes = [agent.execute_job(job(operation="suspend_secret",
                                      params={"pppoe_username": "bach1"}), config)[0]
                for _ in range(4)]
    assert outcomes == [True, True, False, False]

    for _ in range(6):
        ok, _, error, _ = agent.execute_job(
            job(operation="unsuspend_secret", params={"pppoe_username": "bach1"}), config)
        assert (ok, error) == (True, None)

    assert [enabled for _, enabled in calls] == [False, False] + [True] * 6


def test_a_rate_limited_suspend_never_reaches_the_router(monkeypatch):
    """The refusal must happen before the connector, not after -- the point is
    that the customer stays connected."""
    agent._recent_suspends.clear()
    called = []
    monkeypatch.setattr(agent.mikrotik, "set_secret_enabled",
                        lambda s, u, enabled: called.append(u) or (True, "ok"))
    config = dict(CONFIG, max_suspends_per_hour=1)
    suspend = job(operation="suspend_secret", params={"pppoe_username": "bach1"})

    agent.execute_job(suspend, config)
    called.clear()
    ok, _, error, _ = agent.execute_job(suspend, config)

    assert ok is False
    assert "rate limit" in error.lower()
    assert called == [], "the connector must not be reached once the cap is hit"


def test_both_write_operations_are_permitted_and_dispatch_correctly(monkeypatch):
    agent._recent_suspends.clear()
    seen = []
    monkeypatch.setattr(agent.mikrotik, "set_secret_enabled",
                        lambda s, u, enabled: seen.append((u, enabled)) or (True, "done"))
    config = dict(CONFIG, max_suspends_per_hour=5)

    agent.execute_job(job(operation="suspend_secret",
                          params={"pppoe_username": "bach1"}), config)
    agent.execute_job(job(operation="unsuspend_secret",
                          params={"pppoe_username": "bach1"}), config)
    assert seen == [("bach1", False), ("bach1", True)]


def test_the_cap_defaults_when_absent_or_unparseable():
    """Degrade, don't outage -- the same rule _configure_logging follows."""
    base = {"cloud_url": "https://x.test", "token": "1.s",
            "device": [{"id": 1, "host": "10.0.0.1"}]}
    assert agent.parse_config(base)["max_suspends_per_hour"] == 5
    assert agent.parse_config(dict(base, max_suspends_per_hour="nonsense"))[
        "max_suspends_per_hour"] == 5
    assert agent.parse_config(dict(base, max_suspends_per_hour=0))[
        "max_suspends_per_hour"] == 5
    assert agent.parse_config(dict(base, max_suspends_per_hour=12))[
        "max_suspends_per_hour"] == 12


def test_the_agent_reports_the_write_capable_version():
    assert agent.AGENT_VERSION == "1.3.0"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_network_agent_program.py -q`
Expected: FAIL — `AttributeError: module 'servicebills_agent' has no attribute '_recent_suspends'`, and the dispatch tests refuse both operations as not permitted.

- [ ] **Step 3: Add the imports, version bump and cap constants**

In `agent/servicebills_agent.py`, add to the import block:

```python
import collections
```

Change the version and extend the allowlist:

```python
AGENT_VERSION = "1.3.0"
```

```python
# Read-only except for the two PPPoE writes below, which are relayed under an
# agent-side rate limit -- see _claim_suspend_slot. Relaying a write gives the
# cloud a disconnect primitive it deliberately did not have before; the cap is
# what keeps a compromised cloud from disconnecting everyone.
ALLOWED_OPERATIONS = (
    "test_connection", "device_health", "secret_status",
    "active_session", "olt_status", "cpe_locations",
    "suspend_secret", "unsuspend_secret",
)

# The rolling-hour cap on suspends. Configurable in agent.toml so changing your
# mind later does not cost another hand-copy onto this box.
MAX_SUSPENDS_PER_HOUR_DEFAULT = 5
SUSPEND_WINDOW_SECONDS = 3600
```

- [ ] **Step 4: Add the limiter**

After the `logger = logging.getLogger(...)` line:

```python
# Timestamps of suspends this process has ADMITTED, newest last. Module-level
# and therefore per-process: restarting the agent clears it. That is an
# accepted weakness -- restarting requires access to the box, and an attacker
# with that has no need of the cloud's disconnect primitive.
_recent_suspends = collections.deque()


def _claim_suspend_slot(cap, now=None):
    """Take a slot in the rolling window, or refuse. True means go ahead.

    Counts ADMITTED attempts, not successful ones. Counting successes would let
    a compromised cloud probe indefinitely for free: every failure -- a
    username that does not exist, a router that is down -- would cost it
    nothing and the cap would never bite.

    A refusal deliberately does NOT record a timestamp. If it did, hammering
    the endpoint would keep pushing the window forward and the cap would never
    recover.
    """
    now = time.time() if now is None else now
    cutoff = now - SUSPEND_WINDOW_SECONDS
    while _recent_suspends and _recent_suspends[0] <= cutoff:
        _recent_suspends.popleft()
    if len(_recent_suspends) >= cap:
        return False
    _recent_suspends.append(now)
    return True
```

- [ ] **Step 5: Parse the cap from config**

In `parse_config`, beside `poll_seconds`:

```python
        "max_suspends_per_hour": _positive_int(
            raw.get("max_suspends_per_hour"), MAX_SUSPENDS_PER_HOUR_DEFAULT),
```

And the helper, above `parse_config`:

```python
def _positive_int(value, default):
    """A positive int from agent.toml, or the default.

    Anything unusable -- missing, a string, zero, negative -- falls back rather
    than refusing to start. Same "degrade, don't outage" rule _configure_logging
    and _warn_if_world_readable already follow: a typo in an optional setting
    must not take an unattended box offline. Note zero falls back rather than
    meaning "never allow", which would be an outage dressed as a setting.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
```

- [ ] **Step 6: Dispatch the two operations**

In `execute_job`, replace the final `else` branch of the dispatch chain:

```python
        elif operation == "secret_status":
            ok, value = mikrotik.get_secret_status(server, params.get("pppoe_username"))
        elif operation == "active_session":
            ok, value = mikrotik.get_active_session(server, params.get("pppoe_username"))
        elif operation == "suspend_secret":
            username = params.get("pppoe_username")
            if not _claim_suspend_slot(config.get("max_suspends_per_hour",
                                                  MAX_SUSPENDS_PER_HOUR_DEFAULT)):
                logger.warning(
                    "REFUSED suspend of %r: rate limit of %s per hour reached. "
                    "If this was you, wait for the window to roll; if it was not, "
                    "the cloud may be compromised.",
                    username, config.get("max_suspends_per_hour",
                                         MAX_SUSPENDS_PER_HOUR_DEFAULT))
                return (False, None,
                        "Refused by the on-prem agent: suspend rate limit of {} per "
                        "hour reached.".format(
                            config.get("max_suspends_per_hour",
                                       MAX_SUSPENDS_PER_HOUR_DEFAULT)),
                        None)
            logger.info("WRITE suspend %r", username)
            ok, value = mikrotik.set_secret_enabled(server, username, False)
        else:  # unsuspend_secret -- the only remaining allowed operation
            username = params.get("pppoe_username")
            logger.info("WRITE unsuspend %r", username)
            ok, value = mikrotik.set_secret_enabled(server, username, True)
```

The `logger.info` lines are the on-box record. A PPPoE username identifies a customer, not a credential, so logging it does not touch the credentials rule — and a record the cloud cannot reach is the entire point.

- [ ] **Step 7: Run the agent tests**

Run: `python -m pytest tests/test_network_agent_program.py -q`
Expected: all pass.

- [ ] **Step 8: Document the setting**

In `agent/agent.example.toml`, after `poll_seconds`:

```toml
poll_seconds = 2

# How many customers this agent will let the cloud suspend per rolling hour.
# Unsuspends are never limited. This cap is enforced HERE, on this machine, so
# that a compromised cloud cannot disconnect your whole customer base -- it is
# the reason relaying suspend is safe at all. Raise it only if you genuinely
# suspend in batches. Default 5 if omitted.
max_suspends_per_hour = 5
```

In `agent/README.md`, under the Updating section, add:

```markdown
## Suspend rate limit

From 1.3.0 the agent can suspend and unsuspend a customer's PPPoE secret on
behalf of ServiceBills. Reads were always safe to relay; a write can disconnect
a paying customer, so this one is capped.

`max_suspends_per_hour` in `agent.toml` (default 5) is the most suspensions the
agent will perform in any rolling hour. Unsuspends are never limited — restoring
service is not the risk, and ServiceBills restores automatically when a
customer pays.

The cap is enforced on this machine, not in the cloud, so nothing ServiceBills
does can raise it. If it is reached you will see a line in `agent.log` starting
`REFUSED suspend`, and ServiceBills will show the refusal. Every write the agent
performs is logged there too, as `WRITE suspend` or `WRITE unsuspend`.

Changing the cap needs only an `agent.toml` edit and a restart — not a re-copy
of the program files.
```

- [ ] **Step 9: Fix the `mikrotik.py` docstring**

The first sentence still names `MikrotikServer`, a model deleted in the previous cycle. Replace the opening of the module docstring so it describes what the module now serves — `NetworkDevice`, covering both device-health monitoring and customer PPPoE — keeping the existing explanation of the duck-typed connection fields. Do not change anything else in this file.

- [ ] **Step 10: Run the full backend suite and commit**

Run: `python -m pytest -q`
Expected: all pass, ≥ 707 + 8.

```bash
git add agent/ mikrotik.py tests/test_network_agent_program.py
git commit -m "feat: relay PPPoE writes from the agent under a rolling-hour suspend cap"
```

---

### Task 2: The cloud knows which agents can write

**Files:**
- Modify: `app.py` (`AGENT_OPERATIONS` ~357, `DEVICE_TYPE_OPERATIONS` ~363)
- Create: `tests/test_relay_writes.py`

**Interfaces:**
- Consumes: the operation names from Task 1.
- Produces: `MIN_AGENT_VERSION_FOR_WRITES = (1, 3, 0)`; `_parse_agent_version(raw) -> tuple[int, int, int] | None`; `_agent_can_write(agent) -> (bool, str | None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_relay_writes.py`:

```python
"""Relaying suspend/unsuspend through the on-prem agent.

The agent deliberately had no write primitive: a compromised cloud could read
the network but never disconnect anyone. These tests cover the machinery that
makes giving it one acceptable -- a version gate so a write is never queued for
an agent that cannot perform it, and an audit trail that outlives job pruning.
See docs/superpowers/specs/2026-09-08-relay-pppoe-writes-design.md.
"""
import app as appmod


def test_both_write_operations_are_relayable():
    assert 'suspend_secret' in appmod.AGENT_OPERATIONS
    assert 'unsuspend_secret' in appmod.AGENT_OPERATIONS


def test_only_a_mikrotik_can_be_asked_to_write():
    """An OLT has no PPPoE secrets. Offering the pairing would queue a job
    nobody can serve."""
    for operation in ('suspend_secret', 'unsuspend_secret'):
        assert operation in appmod.DEVICE_TYPE_OPERATIONS['mikrotik_ccr']
        assert operation not in appmod.DEVICE_TYPE_OPERATIONS['vsol_olt']


def test_the_guard_actually_rejects_a_write_aimed_at_an_olt(app, client):
    """The mapping above is a claim; this is the guard enforcing it. Asserting
    only the dict would pass even if _create_device_job never consulted it."""
    from tests.conftest import make_tenant
    from tests.test_mikrotik_consolidation import make_device
    make_tenant(client, "Guard W", "guard_w_admin")
    device_id = make_device(app, "Guard W", device_type="vsol_olt",
                            api_port=161, username="")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        job, error = appmod._create_device_job(
            device, "suspend_secret", {"pppoe_username": "bach1"})
        assert job is None
        assert "vsol_olt" in error and "suspend_secret" in error


def test_version_parsing_accepts_a_three_part_version():
    assert appmod._parse_agent_version('1.3.0') == (1, 3, 0)
    assert appmod._parse_agent_version(' 1.10.2 ') == (1, 10, 2)


def test_version_parsing_refuses_anything_it_cannot_read():
    """Refusing to parse means refusing to write. An agent we cannot identify
    is treated as too old, never as new enough."""
    for raw in (None, '', '1.3', 'one.three.zero', '1.3.x', 'v1.3.0'):
        assert appmod._parse_agent_version(raw) is None, raw


def test_an_agent_below_the_floor_cannot_write():
    class Stub:
        agent_version = '1.2.0'
    allowed, message = appmod._agent_can_write(Stub())
    assert allowed is False
    assert 'updat' in message.lower()
    assert '1.3.0' in message


def test_an_agent_at_or_above_the_floor_can_write():
    for version in ('1.3.0', '1.4.0', '2.0.0'):
        class Stub:
            agent_version = version
        assert appmod._agent_can_write(Stub())[0] is True, version


def test_an_agent_with_no_version_cannot_write():
    class Stub:
        agent_version = None
    assert appmod._agent_can_write(Stub())[0] is False


def test_a_missing_agent_cannot_write():
    """tenant_query(NetworkAgent).first() returns None when none is registered;
    the caller must not have to special-case that before asking."""
    assert appmod._agent_can_write(None)[0] is False
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_relay_writes.py -q`
Expected: FAIL — `AttributeError: module 'app' has no attribute '_parse_agent_version'`.

- [ ] **Step 3: Extend the allowlists**

In `app.py`, add both names to `AGENT_OPERATIONS` and to the `mikrotik_ccr` row of `DEVICE_TYPE_OPERATIONS`. Update the comment above `AGENT_OPERATIONS`, which currently claims every operation is a read:

```python
# What the agent will relay. Six reads, plus two PPPoE writes added in the
# writes cycle -- see docs/superpowers/specs/2026-09-08-relay-pppoe-writes-design.md.
# The writes are capped on the agent's own side (max_suspends_per_hour in
# agent.toml), because relaying them hands the cloud a disconnect primitive it
# deliberately did not have. Suspends are capped; unsuspends are not.
AGENT_OPERATIONS = (
    'test_connection', 'device_health', 'secret_status',
    'active_session', 'olt_status', 'cpe_locations',
    'suspend_secret', 'unsuspend_secret',
)
```

```python
DEVICE_TYPE_OPERATIONS = {
    'vsol_olt': ('olt_status', 'cpe_locations'),
    'mikrotik_ccr': ('device_health', 'test_connection',
                     'secret_status', 'active_session',
                     'suspend_secret', 'unsuspend_secret'),
}
```

- [ ] **Step 4: Add the version gate**

Beside `DEVICE_TYPE_OPERATIONS`:

```python
# An agent older than this cannot perform a write at all -- its
# ALLOWED_OPERATIONS predates suspend_secret/unsuspend_secret, so it would
# refuse the job. Checking here means the user is told to update their agent,
# instead of clicking Suspend and waiting for a refusal from the box.
MIN_AGENT_VERSION_FOR_WRITES = (1, 3, 0)


def _parse_agent_version(raw):
    """'1.3.0' -> (1, 3, 0). None for anything this cannot read confidently.

    Deliberately strict: exactly three integer parts, nothing else. An agent
    whose version we cannot parse is treated as too old rather than given the
    benefit of the doubt, because the failure mode of guessing wrong is a
    queued job that will be refused on the box with no explanation.
    """
    parts = (raw or '').strip().split('.')
    if len(parts) != 3:
        return None
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def _agent_can_write(agent):
    """(True, None) if this agent can perform a relayed write, else
    (False, message). Accepts None for 'no agent registered'."""
    version = _parse_agent_version(getattr(agent, 'agent_version', None)) if agent else None
    if version is None or version < MIN_AGENT_VERSION_FOR_WRITES:
        return False, (
            'This action needs on-prem agent {} or newer. Copy the current '
            'agent files onto your agent machine and restart it, then try '
            'again.'.format('.'.join(str(part) for part in MIN_AGENT_VERSION_FOR_WRITES)))
    return True, None
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_relay_writes.py -q`
Expected: 8 passed.

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: all pass.

```bash
git add app.py tests/test_relay_writes.py
git commit -m "feat: allow write operations and gate them on agent version"
```

---

### Task 3: A durable audit trail

**Files:**
- Modify: `app.py` (model near `NetworkAgentJob`; `TENANT_OWNED_MODELS` import ~1453; `_TENANT_DELETE_ORDER` ~2285)
- Create: `migrations/versions/a4e17c92f88b_add_network_write_audit.py`
- Modify: `tests/test_relay_writes.py`, `tests/test_topology_migration.py`

**Interfaces:**
- Produces: `NetworkWriteAudit` model with `to_dict()`; Alembic revision `a4e17c92f88b`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_relay_writes.py`:

```python
from tests.conftest import make_tenant


def test_an_audit_row_records_who_did_what_to_whom(app, client):
    make_tenant(client, "Audit A", "audit_a_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Audit A").first()
        row = appmod.NetworkWriteAudit(
            tenant_id=tenant.id, customer_id=None, network_device_id=None,
            pppoe_username="bach1", action="suspend",
            requested_by_user_id=None, job_id=None, outcome="queued")
        appmod.db.session.add(row)
        appmod.db.session.commit()
        stored = appmod.db.session.get(appmod.NetworkWriteAudit, row.id)
        assert stored.action == "suspend"
        assert stored.outcome == "queued"
        assert stored.created_at is not None


def test_the_audit_row_outlives_the_job_that_created_it(app, client):
    """The whole reason this table exists: _prune_stale_agent_jobs deletes
    terminal jobs, so a job row is not an audit trail."""
    make_tenant(client, "Audit B", "audit_b_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Audit B").first()
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="CCR", host="192.168.100.1", api_port=8728,
            username="admin", password="pw", device_type="mikrotik_ccr")
        appmod.db.session.add(device)
        appmod.db.session.commit()
        job = appmod.NetworkAgentJob(
            tenant_id=tenant.id, device_id=device.id, operation="suspend_secret",
            status="done")
        appmod.db.session.add(job)
        appmod.db.session.commit()
        row = appmod.NetworkWriteAudit(
            tenant_id=tenant.id, pppoe_username="bach1", action="suspend",
            job_id=job.id, outcome="ok", message="disabled")
        appmod.db.session.add(row)
        appmod.db.session.commit()
        row_id, job_id = row.id, job.id

        appmod.db.session.delete(appmod.db.session.get(appmod.NetworkAgentJob, job_id))
        appmod.db.session.commit()

        survivor = appmod.db.session.get(appmod.NetworkWriteAudit, row_id)
        assert survivor is not None, "the audit row must outlive its job"
        assert survivor.pppoe_username == "bach1"
        assert survivor.action == "suspend"


def test_the_audit_model_is_tenant_owned_and_deleted_first(app):
    """It has FKs to tenant, customer, network_device, user and
    network_agent_job, so it must be deleted before every one of them."""
    assert appmod.NetworkWriteAudit in appmod.TENANT_OWNED_MODELS
    order = appmod._TENANT_DELETE_ORDER
    assert appmod.NetworkWriteAudit in order
    audit_at = order.index(appmod.NetworkWriteAudit)
    for parent in (appmod.Customer, appmod.NetworkDevice,
                   appmod.NetworkAgentJob):
        assert audit_at < order.index(parent), (
            "NetworkWriteAudit must be deleted before {}".format(parent.__name__))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_relay_writes.py -q`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'NetworkWriteAudit'`.

- [ ] **Step 3: Add the model**

In `app.py`, immediately after the `NetworkAgentJob` class:

```python
class NetworkWriteAudit(db.Model):
    """One record per PPPoE write ServiceBills performed or asked for.

    Job rows cannot serve as the audit trail: _prune_stale_agent_jobs deletes
    terminal jobs after NETWORK_AGENT_JOB_RETENTION_DAYS, and a disconnect is
    exactly the thing you want to be able to look up months later.

    In agent mode this pairs with a line in the agent's own agent.log, giving
    two records of every relayed write -- one of which an attacker who owns the
    cloud cannot edit. A direct-mode write has only this row, which is correct:
    there the cloud IS the party making the connection, so there is no second
    witness to have.
    """
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    network_device_id = db.Column(db.Integer, db.ForeignKey('network_device.id'), nullable=True)
    # Recorded as sent, not looked up later: the point of the audit is what was
    # actually acted on, which survives the customer row being edited or deleted.
    pppoe_username = db.Column(db.String(100), nullable=False)
    action = db.Column(db.String(10), nullable=False)   # 'suspend' | 'unsuspend'
    # Null for the automatic restore after a settling payment -- nobody clicked
    # it, and recording that honestly matters more than filling the column.
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # Nullable and expected to dangle: the job it names will be pruned.
    job_id = db.Column(db.Integer, db.ForeignKey('network_agent_job.id'), nullable=True)
    outcome = db.Column(db.String(10), nullable=False, default='queued')  # queued|ok|failed
    message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'pppoe_username': self.pppoe_username,
            'action': self.action,
            'requested_by_user_id': self.requested_by_user_id,
            'outcome': self.outcome,
            'message': self.message,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
        }
```

- [ ] **Step 4: Register it for tenant ownership and deletion**

Add `NetworkWriteAudit` to the `TENANT_OWNED_MODELS` import list (`app.py` ~1453), beside `NetworkAgentJob`.

In `_TENANT_DELETE_ORDER`, place it **before** `Customer` — it has inbound-FK dependencies on `customer`, `network_device`, `user` and `network_agent_job`, so it must go first. Put it at the head of the list with a comment:

```python
_TENANT_DELETE_ORDER = [
    # First: it holds FKs to customer, network_device, user and
    # network_agent_job, so every one of those must still exist when it goes.
    # SQLite does not enforce FKs, so getting this wrong is invisible locally
    # and only fails against production Postgres.
    NetworkWriteAudit,
    UpgradeRequest, BillingPaymentAttempt, PaymentReminder, GeneratedReceipt,
```

- [ ] **Step 5: Run the model tests**

Run: `python -m pytest tests/test_relay_writes.py -q`
Expected: all pass.

- [ ] **Step 6: Write the migration**

Create `migrations/versions/a4e17c92f88b_add_network_write_audit.py`:

```python
"""add network_write_audit

Revision ID: a4e17c92f88b
Revises: f2b6c9d4e703
Create Date: 2026-09-08 17:00:00.000000

Hand-written rather than autogenerated, for the reason 5f65a6fd6e8d already
records: this repo's local SQLite database cannot reach the real head at all
(origin's bd054e2e7cf9 calls op.create_unique_constraint outside batch mode,
which SQLite rejects), so autogenerate has nothing valid to diff against.

Purely additive -- one new table, no change to any existing one. Inspects
before creating because production's real schema disagrees with migration
history in both directions, and because the container runs
`flask db upgrade && exec gunicorn`: a migration that raises is not a warning,
it is a full outage.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4e17c92f88b'
down_revision = 'f2b6c9d4e703'
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    if _has_table('network_write_audit'):
        return
    op.create_table(
        'network_write_audit',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('network_device_id', sa.Integer(), nullable=True),
        sa.Column('pppoe_username', sa.String(length=100), nullable=False),
        sa.Column('action', sa.String(length=10), nullable=False),
        sa.Column('requested_by_user_id', sa.Integer(), nullable=True),
        sa.Column('job_id', sa.Integer(), nullable=True),
        sa.Column('outcome', sa.String(length=10), nullable=False,
                  server_default='queued'),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id']),
        sa.ForeignKeyConstraint(['network_device_id'], ['network_device.id']),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['job_id'], ['network_agent_job.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_network_write_audit_tenant_id', 'network_write_audit',
                    ['tenant_id'], unique=False)
    op.create_index('ix_network_write_audit_created_at', 'network_write_audit',
                    ['created_at'], unique=False)


def downgrade():
    if _has_table('network_write_audit'):
        op.drop_table('network_write_audit')
```

- [ ] **Step 7: Confirm a single head**

Run: `JWT_SECRET_KEY=x RUN_SCHEDULER=0 DATABASE_PATH=":memory:" python -m flask db heads`
Expected: one line ending `a4e17c92f88b (head)`.

- [ ] **Step 8: Write the migration test**

Append to `tests/test_topology_migration.py`, copying the bootstrap shape from the tests already in that file:

```python

WRITE_AUDIT_REVISION = "a4e17c92f88b"


def test_write_audit_migration_upgrade_downgrade_upgrade():
    """Bootstrap with create_all + stamp rather than walking the chain, which
    cannot replay on SQLite. Also covers the second-run case: upgrade() must be
    a no-op when the table is already there, because production's schema and
    the migration history disagree in both directions."""
    tmpdir = tempfile.mkdtemp(prefix="write_audit_migration_test_")
    db_path = os.path.join(tmpdir, "write_audit.db")
    mig_app = Flask("test_write_audit_migration")
    mig_app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + db_path.replace("\\", "/"))
    mig_db = SQLAlchemy(mig_app)
    Migrate(mig_app, mig_db, directory=MIGRATIONS_DIR, render_as_batch=True)

    try:
        with mig_app.app_context():
            engine = mig_db.engine
            appmod.db.metadata.create_all(bind=engine)
            stamp(directory=MIGRATIONS_DIR, revision=WRITE_AUDIT_REVISION)

            downgrade(directory=MIGRATIONS_DIR, revision="-1")
            assert "network_write_audit" not in _table_names(engine)

            upgrade(directory=MIGRATIONS_DIR, revision=WRITE_AUDIT_REVISION)
            assert "network_write_audit" in _table_names(engine)
            cols = _table_columns(engine, "network_write_audit")
            for expected in ("tenant_id", "customer_id", "network_device_id",
                             "pppoe_username", "action", "requested_by_user_id",
                             "job_id", "outcome", "message", "created_at"):
                assert expected in cols, expected

            # Second run against a database that already matches.
            upgrade(directory=MIGRATIONS_DIR, revision=WRITE_AUDIT_REVISION)
            assert "network_write_audit" in _table_names(engine)

            engine.dispose()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
```

- [ ] **Step 9: Run the migration tests, then the full suite, and commit**

Run: `python -m pytest tests/test_topology_migration.py -q`
Expected: 7 passed.

Run: `python -m pytest -q`
Expected: all pass.

```bash
git add app.py migrations/versions/a4e17c92f88b_add_network_write_audit.py tests/
git commit -m "feat: add a durable audit trail for PPPoE writes"
```

---

### Task 4: Wire the three write call sites

**Files:**
- Modify: `app.py` (`_maybe_restore_mikrotik_access` ~4346, `agent_post_result` ~9938, `suspend_customer_network` ~10585, `unsuspend_customer_network` ~10597, remove `AGENT_WRITE_UNSUPPORTED_MESSAGE`)
- Modify: `tests/test_relay_writes.py`

**Interfaces:**
- Consumes: `_agent_can_write` (Task 2), `NetworkWriteAudit` (Task 3), `_create_device_job`.
- Produces: `_record_write_audit(...)`; `suspend`/`unsuspend` endpoints returning `{'ok', 'message', 'job_id'}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_relay_writes.py`. Reuse `stub_connectors`, `make_device`, `_admin`, `_set_agent_mode` and `_linked_customer` from `tests/test_mikrotik_consolidation.py` by importing them:

```python
from tests.test_mikrotik_consolidation import (
    stub_connectors, make_device, _admin, _set_agent_mode, _linked_customer)


def _set_agent_version(app, tenant_name, version):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant.id).first()
        if agent is None:
            agent = appmod.NetworkAgent(tenant_id=tenant.id, name="Box", token_hash="x")
            appmod.db.session.add(agent)
        agent.agent_version = version
        agent.last_seen_at = appmod.datetime.utcnow()
        appmod.db.session.commit()


def test_suspend_still_writes_inline_in_direct_mode(app, client, monkeypatch):
    calls = []
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda d, u, enabled: calls.append((u, enabled)) or (True, "disabled"))
    hdr = _admin(client, "Wr A", "wr_a_admin")
    device_id = make_device(app, "Wr A")
    customer_id = _linked_customer(app, "Wr A", device_id)

    r = client.post("/api/customers/{}/network-suspend".format(customer_id), headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert calls == [("bach1", False)]

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(customer_id=customer_id).one()
        assert (row.action, row.outcome, row.job_id) == ("suspend", "ok", None)


def test_suspend_queues_a_job_in_agent_mode(app, client, monkeypatch):
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr B", "wr_b_admin")
    device_id = make_device(app, "Wr B")
    customer_id = _linked_customer(app, "Wr B", device_id)
    _set_agent_mode(app, "Wr B")
    _set_agent_version(app, "Wr B", "1.3.0")

    r = client.post("/api/customers/{}/network-suspend".format(customer_id), headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["job_id"] is not None

    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, body["job_id"])
        assert job.operation == "suspend_secret"
        assert job.params == {"pppoe_username": "bach1"}
        row = appmod.NetworkWriteAudit.query.filter_by(customer_id=customer_id).one()
        assert (row.action, row.outcome, row.job_id) == ("suspend", "queued", job.id)


def test_an_old_agent_is_refused_before_a_job_is_created(app, client, monkeypatch):
    """Otherwise the user clicks Suspend, waits, and gets a refusal from the
    box with no idea why."""
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr C", "wr_c_admin")
    device_id = make_device(app, "Wr C")
    customer_id = _linked_customer(app, "Wr C", device_id)
    _set_agent_mode(app, "Wr C")
    _set_agent_version(app, "Wr C", "1.2.0")

    r = client.post("/api/customers/{}/network-suspend".format(customer_id), headers=hdr)
    assert r.get_json()["ok"] is False
    assert "1.3.0" in r.get_json()["message"]
    with app.app_context():
        assert appmod.NetworkAgentJob.query.count() == 0
        assert appmod.NetworkWriteAudit.query.count() == 0


def test_unsuspend_queues_the_unsuspend_operation(app, client, monkeypatch):
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr D", "wr_d_admin")
    device_id = make_device(app, "Wr D")
    customer_id = _linked_customer(app, "Wr D", device_id)
    _set_agent_mode(app, "Wr D")
    _set_agent_version(app, "Wr D", "1.3.0")

    r = client.post("/api/customers/{}/network-unsuspend".format(customer_id), headers=hdr)
    job_id = r.get_json()["job_id"]
    with app.app_context():
        assert appmod.db.session.get(appmod.NetworkAgentJob, job_id).operation == "unsuspend_secret"
        assert appmod.NetworkWriteAudit.query.one().action == "unsuspend"


def test_the_audit_row_is_completed_when_the_agent_reports_back(app, client, monkeypatch):
    stub_connectors(monkeypatch)
    hdr = _admin(client, "Wr E", "wr_e_admin")
    device_id = make_device(app, "Wr E")
    customer_id = _linked_customer(app, "Wr E", device_id)
    _set_agent_mode(app, "Wr E")
    _set_agent_version(app, "Wr E", "1.3.0")

    job_id = client.post("/api/customers/{}/network-suspend".format(customer_id),
                         headers=hdr).get_json()["job_id"]
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Wr E").first()
        agent = appmod.NetworkAgent.query.filter_by(tenant_id=tenant.id).first()
        token = appmod._issue_agent_token(agent)
        job = appmod.db.session.get(appmod.NetworkAgentJob, job_id)
        job.status = "claimed"
        appmod.db.session.commit()

    client.post("/api/agent/jobs/{}/result".format(job_id),
                headers={"Authorization": "Bearer " + token},
                json={"ok": True, "result": "disabled"})

    with app.app_context():
        row = appmod.NetworkWriteAudit.query.filter_by(job_id=job_id).one()
        assert row.outcome == "ok"


def test_the_payment_restore_queues_without_blocking_in_agent_mode(app, client, monkeypatch):
    """Runs after every settling payment on a single synchronous worker, so it
    must queue and return, never wait."""
    stub_connectors(monkeypatch)
    never = []
    monkeypatch.setattr(appmod.mikrotik, "get_secret_status",
                        lambda d, u: never.append(u) or (True, "disabled"))
    _admin(client, "Wr F", "wr_f_admin")
    device_id = make_device(app, "Wr F")
    customer_id = _linked_customer(app, "Wr F", device_id)
    _set_agent_mode(app, "Wr F")
    _set_agent_version(app, "Wr F", "1.3.0")

    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        result = appmod._maybe_restore_mikrotik_access(customer)
        assert result is not None and result.get("queued") is True
        job = appmod.NetworkAgentJob.query.filter_by(operation="unsuspend_secret").one()
        assert job.params == {"pppoe_username": "bach1"}
        row = appmod.NetworkWriteAudit.query.one()
        assert row.requested_by_user_id is None, "nobody clicked it"
    assert never == [], "agent mode must not spend a round trip reading status first"


def test_the_payment_restore_still_checks_status_first_in_direct_mode(app, client, monkeypatch):
    """The check avoids a pointless write, and in direct mode it costs nothing."""
    monkeypatch.setattr(appmod.mikrotik, "get_secret_status", lambda d, u: (True, "enabled"))
    wrote = []
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda d, u, enabled: wrote.append(u) or (True, "ok"))
    _admin(client, "Wr G", "wr_g_admin")
    device_id = make_device(app, "Wr G")
    customer_id = _linked_customer(app, "Wr G", device_id)

    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        assert appmod._maybe_restore_mikrotik_access(customer) is None
    assert wrote == [], "an already-enabled secret needs no write in direct mode"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_relay_writes.py -q`
Expected: FAIL — the agent-mode cases get the 501 refusal, and `NetworkWriteAudit.query.one()` raises `NoResultFound`.

- [ ] **Step 3: Add the audit helper**

In `app.py`, beside `_create_device_job`:

```python
def _record_write_audit(customer, device, action, outcome, message=None,
                        job_id=None, user_id=None):
    """Record one PPPoE write. Added to the session, not committed -- the
    caller's own commit carries it, so the audit and the job land together or
    not at all.

    pppoe_username is copied in rather than looked up later: the audit must say
    what was actually acted on even if the customer row is edited afterwards.
    """
    db.session.add(NetworkWriteAudit(
        tenant_id=customer.tenant_id,
        customer_id=customer.id,
        network_device_id=device.id if device else None,
        pppoe_username=customer.pppoe_username or '',
        action=action,
        requested_by_user_id=user_id,
        job_id=job_id,
        outcome=outcome,
        message=message,
    ))
```

- [ ] **Step 4: Rewrite the two staff endpoints**

Replace both, and delete the `AGENT_WRITE_UNSUPPORTED_MESSAGE` constant and its comment block (its three uses all disappear in this task):

```python
def _perform_customer_write(customer_id, action):
    """Shared body of network-suspend and network-unsuspend.

    Direct mode runs the connector inline and records a terminal audit row.
    Agent mode queues the matching operation and records a 'queued' row that
    agent_post_result completes. Both return the same shape so the frontend has
    one code path.
    """
    customer, device, err = _customer_network_context(customer_id)
    if err:
        return jsonify(err[0]), err[1]

    enable = action == 'unsuspend'
    operation = 'unsuspend_secret' if enable else 'suspend_secret'
    username = get_jwt_identity()
    user = User.query.filter_by(username=username).first() if username else None
    user_id = user.id if user else None

    if _tenant_access_mode() == 'agent':
        allowed, why = _agent_can_write(tenant_query(NetworkAgent).first())
        if not allowed:
            return jsonify({'ok': False, 'message': why, 'job_id': None}), 200
        job, error = _create_device_job(
            device, operation, {'pppoe_username': customer.pppoe_username})
        if error:
            return jsonify({'ok': False, 'message': error, 'job_id': None}), 200
        _record_write_audit(customer, device, action, 'queued',
                            job_id=job.id, user_id=user_id)
        db.session.commit()
        return jsonify({'ok': True, 'message': None, 'job_id': job.id}), 200

    ok, message = mikrotik.set_secret_enabled(device, customer.pppoe_username, enable)
    _record_write_audit(customer, device, action, 'ok' if ok else 'failed',
                        message=message, user_id=user_id)
    db.session.commit()
    return jsonify({'ok': ok, 'message': message, 'job_id': None}), (200 if ok else 502)


@app.route('/api/customers/<int:customer_id>/network-suspend', methods=['POST'])
@jwt_required()
def suspend_customer_network(customer_id):
    return _perform_customer_write(customer_id, 'suspend')


@app.route('/api/customers/<int:customer_id>/network-unsuspend', methods=['POST'])
@jwt_required()
def unsuspend_customer_network(customer_id):
    return _perform_customer_write(customer_id, 'unsuspend')
```

- [ ] **Step 5: Rewrite the automatic restore**

Replace the agent-mode early return in `_maybe_restore_mikrotik_access` with a queue, keeping everything else:

```python
    if not customer.network_device_id:
        return None
    try:
        device = tenant_query(NetworkDevice).filter_by(id=customer.network_device_id).first()
        if not device or not customer.pppoe_username:
            return None
        if _tenant_access_mode() == 'agent':
            # Deliberately does NOT read secret_status first, unlike direct
            # mode below. This runs after every settling payment on a single
            # synchronous worker, and the read would cost a second agent round
            # trip on the billing path. Re-enabling an already-enabled secret
            # is a no-op on RouterOS, so the pointless write is the cheaper of
            # the two. requested_by_user_id stays null -- nobody clicked this.
            allowed, why = _agent_can_write(tenant_query(NetworkAgent).first())
            if not allowed:
                return {'attempted': False, 'ok': False, 'message': why}
            job, error = _create_device_job(
                device, 'unsuspend_secret', {'pppoe_username': customer.pppoe_username})
            if error:
                return {'attempted': False, 'ok': False, 'message': error}
            _record_write_audit(customer, device, 'unsuspend', 'queued', job_id=job.id)
            db.session.commit()
            return {'attempted': True, 'queued': True, 'ok': True, 'message': None}
        ok, status = mikrotik.get_secret_status(device, customer.pppoe_username)
        if not ok:
            return {'attempted': True, 'ok': False, 'message': status}
        if status != 'disabled':
            return None  # already enabled (or not_found) -- nothing to restore
        ok, message = mikrotik.set_secret_enabled(device, customer.pppoe_username, True)
        _record_write_audit(customer, device, 'unsuspend', 'ok' if ok else 'failed',
                            message=message)
        db.session.commit()
        return {'attempted': True, 'ok': ok, 'message': message}
    except Exception as e:
        logging.error(f"Mikrotik re-enable check failed for customer {customer.id}: {e}")
        return {'attempted': True, 'ok': False, 'message': str(e)}
```

Leave the `except` exactly as it is. It logs the message only, never a traceback, which is what keeps the device credential out of Sentry.

- [ ] **Step 6: Complete the audit row when the agent reports**

In `agent_post_result`, immediately before the final `db.session.commit()`:

```python
    # Close out the audit row for a relayed write. The job row itself will be
    # pruned; this is the record that lasts.
    if job.operation in ('suspend_secret', 'unsuspend_secret'):
        audit = NetworkWriteAudit.query.filter_by(job_id=job.id).first()
        if audit is not None:
            audit.outcome = 'ok' if job.error is None else 'failed'
            audit.message = job.error if job.error else (
                job.result if isinstance(job.result, str) else None)
```

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_relay_writes.py -q`
Expected: all pass.

- [ ] **Step 8: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: all pass. Note the three old 501 tests in `tests/test_mikrotik_consolidation.py` will now fail — they assert the behaviour this task replaces. Update them to assert the new behaviour rather than deleting them: an agent-mode suspend with a current agent queues a job; with an old agent it is refused.

```bash
git add app.py tests/
git commit -m "feat: relay suspend and unsuspend, audited in both modes"
```

---

### Task 5: The frontend polls a relayed write

**Files:**
- Modify: `frontend/src/components/SubscriptionsView.js` (`handleNetworkAction` ~754)

**Interfaces:**
- Consumes: `{'ok', 'message', 'job_id'}` from Task 4; `pollNetworkJob` from `./pollNetworkJob`.

- [ ] **Step 1: Update the handler**

`handleNetworkAction` currently assumes the response is terminal, because agent mode used to refuse with a 501 that landed in the catch. Now agent mode returns a job id. Replace it:

```javascript
    const handleNetworkAction = async (customerId, action) => {
        setMikrotikActionLoading(true);
        try {
            const call = action === 'suspend' ? apiService.suspendCustomerNetwork : apiService.unsuspendCustomerNetwork;
            const { data } = await call(customerId);
            if (!data.ok) {
                // A refusal the backend chose to return as 200: an agent too
                // old to perform writes, or a job it declined to create. The
                // message says which.
                setSnackbar({ open: true, message: data.message, severity: 'warning' });
                return;
            }
            if (data.job_id) {
                // Agent mode: the router has not been touched yet. Wait for the
                // box to report, so the toast describes what actually happened
                // rather than that we asked.
                const job = await pollNetworkJob(data.job_id);
                setSnackbar({
                    open: true,
                    message: job.error || `Customer ${action}ed.`,
                    severity: job.error ? 'error' : 'success',
                });
            } else {
                setSnackbar({ open: true, message: data.message, severity: 'success' });
            }
            await fetchNetworkStatus(customerId);
        } catch (err) {
            setSnackbar({
                open: true,
                message: err.response?.data?.message || `Failed to ${action} connection`,
                severity: 'error',
            });
        } finally {
            setMikrotikActionLoading(false);
        }
    };
```

Add the import at the top of the file:

```javascript
import pollNetworkJob from './pollNetworkJob';
```

The `data.ok === false` branch is now reachable, unlike the dead `'warning'` branch removed in the previous cycle: the version gate and a declined job both return 200 with `ok: false`.

- [ ] **Step 2: Run the frontend suite**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/*.test.js"`
Expected: 75 passed. `src/App.test.js` fails to resolve `@testing-library/react` — pre-existing and unrelated.

- [ ] **Step 3: Build**

Run: `cd frontend && npx react-scripts build`
Expected: compiles; no new warnings naming `SubscriptionsView.js`.

Then: `git checkout -- frontend/build build && git clean -fdxq frontend/build build`, and confirm `git status --short` shows neither path.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat: report the real outcome of a relayed suspend"
```

---

## Final verification

- [ ] `python -m pytest -q` — all pass, ≥ 730
- [ ] `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/*.test.js"` — 75 pass
- [ ] `cd frontend && npx react-scripts build` — compiles, then revert `frontend/build` and `build`
- [ ] `JWT_SECRET_KEY=x RUN_SCHEDULER=0 DATABASE_PATH=":memory:" python -m flask db heads` — one head, `a4e17c92f88b`
- [ ] `git diff --stat origin/main..HEAD -- vsol_olt.py` is empty, and `mikrotik.py`'s diff is the docstring only
- [ ] `grep -rn "AGENT_WRITE_UNSUPPORTED_MESSAGE" app.py tests/ frontend/src` returns nothing
- [ ] `grep -n "set_secret_enabled" agent/servicebills_agent.py` shows it reached only through `suspend_secret` / `unsuspend_secret`, with the cap checked before the suspend branch calls it
