# Network Tree Scheduled Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep ONU status and CPE-location (customer-ONU) data fresh for every agent-mode tenant's OLTs every 15 minutes, regardless of viewer role or whether anyone has the Network Tree page open, without widening any permission and without touching the shared, JWT-scoped `_create_device_job`/`tenant_query()` machinery.

**Architecture:** A new APScheduler job (registered alongside the existing 6, on a 15-minute interval) iterates agent-mode tenants' OLTs and enqueues `olt_status`/`cpe_locations` jobs via a new, small, scheduler-only job-creation function that mirrors just the agent-mode path of `_create_device_job` using explicit `tenant_id`/`device_id` values instead of JWT-derived ones. Each such job is marked `params={'_scheduled': True}`. When the on-premise agent (unchanged) posts a completed, marked `cpe_locations` job's result back, `agent_post_result` auto-applies it via `_apply_cpe_locations`, which gains one new optional `tenant_id` parameter for exactly this non-JWT caller.

**Tech Stack:** Flask + SQLAlchemy + APScheduler (`BackgroundScheduler`, already running in-process), pytest.

**Spec:** `docs/superpowers/specs/2026-09-17-network-tree-scheduled-freshness-design.md`

## Global Constraints

- **Never modify `_create_device_job` or `tenant_query()`/`current_tenant_id()`.** Both are shared, security-relevant (multi-tenant-isolation) code with many existing call sites; this feature adds new, separate functions instead. `_apply_cpe_locations` is the one exception, and only by adding a new *optional* parameter that defaults to today's exact behavior.
- **Every per-tenant scheduled function takes an explicit `tenant_id` parameter and queries with plain `Model.query.filter_by(tenant_id=...)`** — never `tenant_query()`. This project has a real regression test for this exact class of bug: `tests/test_iso_scheduler.py::test_scheduler_runs_without_request_and_is_tenant_scoped` documents that `generate_missing_payments` used to call `tenant_query()` and abort 401 when run from the scheduler; it was fixed to take an explicit `tenant_id`. Follow that same shape, and add an equivalent regression test for this feature's own new function(s).
- **Per-tenant failure isolation, not the gap.** `check_pro_plan_expirations_with_context` (`app.py:3214-3221`) wraps each tenant's work in its own `try/except` with `db.session.rollback()` on failure, so one tenant never aborts the rest of the run — follow that pattern exactly, not `auto_sync_upstream_status_with_context`'s (`app.py:3160-3163`), which has no such guard.
- **The scheduler fires twice on every deploy.** `Dockerfile:38` runs `flask db upgrade && ... && exec gunicorn ...` — both the `flask db upgrade` CLI invocation and the subsequent `gunicorn` process import `app.py`, and `RUN_SCHEDULER` defaults to `"1"` (`Dockerfile:32`), so every registered job (with `next_run_time=datetime.now()`) fires once during the migration step and again moments later under gunicorn. This is pre-existing behavior for all 6 current jobs, not something to fix — it's why this feature's duplicate-job guard (Task 3) matters, not just for an offline agent.
- Test command: `python -m pytest tests/<file>.py -q` from the repo root (no special flags needed for this backend suite, unlike the frontend's CRA quirk).
- No database migration needed — the `_scheduled` marker lives in `NetworkAgentJob.params`, an existing `db.JSON` column.

---

## File Structure

All changes are in `app.py` (no new files):

- `_apply_cpe_locations` (`app.py:11069`): add optional `tenant_id=None` parameter.
- New function `_create_scheduled_device_job(device, operation)`, placed immediately after `_create_device_job` (`app.py:10607-10672`).
- New functions `refresh_agent_mode_network_status_for_tenant(tenant_id)` and `refresh_agent_mode_network_status_with_context()`, placed near the other scheduled-job functions (immediately after `check_pro_plan_expirations_with_context`, `app.py:3214-3221`, before the `scheduler.add_job(...)` registration block).
- One new `scheduler.add_job(...)` line in the existing registration block (`app.py:3244-3258`).
- `agent_post_result` (`app.py:10424-10501`): add the auto-apply hook at the end, before the final `return`.

Tests: `tests/test_cpe_linking_api.py` (Task 1's new test), a new `tests/test_scheduled_network_freshness.py` (Tasks 2 and 3's tests), `tests/test_network_agent_api.py` (Task 4's new tests).

---

### Task 1: `_apply_cpe_locations` gains an optional `tenant_id` parameter

**Files:**
- Modify: `app.py:11069-11117`
- Test: `tests/test_cpe_linking_api.py`

**Interfaces:**
- Produces: `_apply_cpe_locations(result, tenant_id=None)` — identical return shape (`{'located': int, 'moved': int, 'unmatched': int}`) and identical behavior to today when `tenant_id` is omitted. When given, scopes the customer lookup to that tenant via `Customer.query.filter_by(tenant_id=tenant_id)` instead of `tenant_query(Customer)`, and requires no Flask-JWT/request context to run.
- Consumes: nothing new. `Customer`, `_normalize_mac`, `_canonical_mac`, `db` already in scope in `app.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cpe_linking_api.py` (check the file's existing imports/helpers first — it already has fixtures for creating a tenant, a customer with `cpe_mac_address`, and calling `_apply_cpe_locations`; mirror those rather than re-deriving them). Add:

```python
def test_apply_cpe_locations_with_explicit_tenant_id_needs_no_request_context(app):
    """Mirrors tests/test_iso_scheduler.py's regression test for the same
    class of bug: a caller with no Flask-JWT (the scheduler, or
    agent_post_result's agent-token auth) must be able to scope this by an
    explicit tenant_id instead of tenant_query()'s JWT-derived one."""
    with app.app_context():
        tenant_a = appmod.Tenant(name="Api Explicit A", slug="api-explicit-a")
        tenant_b = appmod.Tenant(name="Api Explicit B", slug="api-explicit-b")
        appmod.db.session.add_all([tenant_a, tenant_b])
        appmod.db.session.commit()

        customer_a = appmod.Customer(
            tenant_id=tenant_a.id, name="A Customer", phone="1", address="a",
            cpe_mac_address="aa:aa:aa:aa:aa:aa")
        customer_b = appmod.Customer(
            tenant_id=tenant_b.id, name="B Customer", phone="2", address="b",
            cpe_mac_address="bb:bb:bb:bb:bb:bb")
        appmod.db.session.add_all([customer_a, customer_b])
        appmod.db.session.commit()

        # No request context anywhere in this test -- tenant_query() would
        # abort 401 here if this code path still used it.
        result = appmod._apply_cpe_locations(
            {"aa:aa:aa:aa:aa:aa": {"onu_mac": "11:22:33:44:55:66"}},
            tenant_id=tenant_a.id,
        )
        assert result == {"located": 1, "moved": 1, "unmatched": 0}

        appmod.db.session.refresh(customer_a)
        appmod.db.session.refresh(customer_b)
        assert customer_a.onu_mac_address == "11:22:33:44:55:66"
        assert customer_b.onu_mac_address is None  # untouched, different tenant
```

Add this import near the top of the file if not already present: `import app as appmod`.

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_cpe_linking_api.py::test_apply_cpe_locations_with_explicit_tenant_id_needs_no_request_context -v`
Expected: FAIL — `TypeError: _apply_cpe_locations() got an unexpected keyword argument 'tenant_id'`.

- [ ] **Step 3: Implement**

In `app.py`, change:

```python
def _apply_cpe_locations(result):
```

to:

```python
def _apply_cpe_locations(result, tenant_id=None):
```

and add one line to the docstring (after the existing final paragraph, before the closing `"""`):

```
    `tenant_id`: when given, scopes the customer lookup explicitly to this
    tenant via a plain filter_by, instead of tenant_query()'s JWT-derived
    current_tenant_id() -- required for a caller with no Flask-JWT in scope
    (the scheduler, or agent_post_result's agent-token auth). Omitted (every
    existing call site), behavior is identical to before this parameter
    existed.
```

Then change:

```python
    by_cpe = {}
    for customer in tenant_query(Customer).filter(
            Customer.cpe_mac_address.isnot(None)).all():
        by_cpe[_normalize_mac(customer.cpe_mac_address)] = customer
```

to:

```python
    customers_query = (
        Customer.query.filter_by(tenant_id=tenant_id)
        if tenant_id is not None else tenant_query(Customer)
    )
    by_cpe = {}
    for customer in customers_query.filter(
            Customer.cpe_mac_address.isnot(None)).all():
        by_cpe[_normalize_mac(customer.cpe_mac_address)] = customer
```

Nothing else in the function body changes.

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_cpe_linking_api.py::test_apply_cpe_locations_with_explicit_tenant_id_needs_no_request_context -v`
Expected: PASS

- [ ] **Step 5: Run the full existing `test_cpe_linking_api.py` suite to confirm the one existing call site (`apply_customer_locations`, `app.py:11162`) is unaffected**

Run: `python -m pytest tests/test_cpe_linking_api.py -q`
Expected: PASS, same count as before this task plus the one new test.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_cpe_linking_api.py
git commit -m "Add optional tenant_id parameter to _apply_cpe_locations for non-JWT callers"
```

---

### Task 2: `_create_scheduled_device_job` — scheduler-only job creation

**Files:**
- Modify: `app.py`, insert immediately after `_create_device_job` ends (after `app.py:10672`, before the blank lines preceding `_record_write_audit`)
- Test: new file `tests/test_scheduled_network_freshness.py`

**Interfaces:**
- Consumes: `AGENT_OPERATIONS`, `DEVICE_TYPE_OPERATIONS` (`app.py:454-470`), `NetworkAgent`, `NetworkAgentJob`, `_prune_stale_agent_jobs(tenant_id)` (`app.py:10591-10604`, already takes an explicit `tenant_id` — reuse unmodified), `db`.
- Produces: `_create_scheduled_device_job(device, operation)` → `(job, None)` on success or `(None, message)` on failure — same two-tuple shape as `_create_device_job`. Every created job has `requested_by_user_id=None` and `params={'_scheduled': True}`. Task 3 calls this directly; Task 4's `agent_post_result` hook checks the `_scheduled` marker this function stamps.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduled_network_freshness.py`:

```python
"""Tests for the scheduled ONU/CPE-location freshness job. See
docs/superpowers/specs/2026-09-17-network-tree-scheduled-freshness-design.md."""
import app as appmod
from tests.conftest import make_tenant


def _make_agent_mode_tenant_with_olt(app, client, business_name, username, agent_online=True):
    """Returns (tenant_id, device_id). Sets network_access_mode='agent' and
    creates one online (or offline) NetworkAgent plus one vsol_olt device."""
    make_tenant(client, business_name, username)
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=business_name).first()
        settings = appmod.BusinessSettings(tenant_id=tenant.id, network_access_mode='agent')
        appmod.db.session.add(settings)
        agent = appmod.NetworkAgent(
            tenant_id=tenant.id, name="Box", token_hash="x",
            last_seen_at=appmod.datetime.utcnow() if agent_online else None)
        appmod.db.session.add(agent)
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="EPON OLT", host="192.168.8.100",
            username="", password="unused", device_type="vsol_olt", api_port=161)
        appmod.db.session.add(device)
        appmod.db.session.commit()
        return tenant.id, device.id


def test_create_scheduled_device_job_stamps_scheduled_marker_and_no_user(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(app, client, "Sched A", "sched_a_admin")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        # No request context anywhere in this test -- this must not touch
        # tenant_query()/current_tenant_id() at all.
        job, error = appmod._create_scheduled_device_job(device, 'olt_status')
        assert error is None
        assert job.requested_by_user_id is None
        assert job.params == {'_scheduled': True}
        assert job.tenant_id == tenant_id
        assert job.status == 'pending'


def test_create_scheduled_device_job_reports_agent_offline(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(
        app, client, "Sched B", "sched_b_admin", agent_online=False)
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        job, error = appmod._create_scheduled_device_job(device, 'olt_status')
        assert job is None
        assert 'Agent offline' in error


def test_create_scheduled_device_job_rejects_unsupported_operation_for_device_type(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(app, client, "Sched C", "sched_c_admin")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        # olt devices cannot perform device_health (that's a mikrotik_ccr operation)
        job, error = appmod._create_scheduled_device_job(device, 'device_health')
        assert job is None
        assert 'cannot perform' in error
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_scheduled_network_freshness.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute '_create_scheduled_device_job'` (or `AttributeError: ... no attribute 'BusinessSettings'`/similar if those model names differ slightly — check `app.py`'s exact model/import names first if this fails for an unexpected reason before writing the implementation).

- [ ] **Step 3: Implement**

In `app.py`, immediately after `_create_device_job`'s closing (right after `app.py:10672`'s `return job, None` and before the blank lines preceding `_record_write_audit`), add:

```python
def _create_scheduled_device_job(device, operation):
    """Agent-mode-only sibling of _create_device_job for the periodic
    freshness scheduler (see docs/superpowers/specs/
    2026-09-17-network-tree-scheduled-freshness-design.md). Never called
    from a request -- there is no JWT to derive a tenant or current user
    from, so this uses device.tenant_id directly instead of
    _tenant_access_mode()/tenant_query(NetworkAgent), and always stamps
    requested_by_user_id=None with params={'_scheduled': True} so
    agent_post_result knows to auto-apply a completed cpe_locations job's
    result instead of waiting for a human's separate apply click.

    Returns (job, None) on success or (None, message) -- same shape as
    _create_device_job -- when the device can't perform this operation or
    its tenant's agent is offline. Only ever called for an agent-mode
    tenant's OLT (the scheduler already filters to that before calling
    this), so there is no 'direct' mode branch here at all.
    """
    if operation not in AGENT_OPERATIONS:
        return None, 'Unsupported operation: {}'.format(operation)
    permitted = DEVICE_TYPE_OPERATIONS.get(device.device_type, ())
    if operation not in permitted:
        return None, 'A {} device cannot perform {}.'.format(
            device.device_type, operation)

    agent = NetworkAgent.query.filter_by(tenant_id=device.tenant_id).first()
    if not agent or not agent.is_online():
        last = agent.last_seen_at.strftime('%Y-%m-%d %H:%M:%S') if (agent and agent.last_seen_at) else 'never'
        return None, 'Agent offline (last seen {}). Start the agent on your network and try again.'.format(last)

    job = NetworkAgentJob(
        tenant_id=device.tenant_id, device_id=device.id,
        operation=operation, params={'_scheduled': True},
        requested_by_user_id=None,
    )
    _prune_stale_agent_jobs(device.tenant_id)
    db.session.add(job)
    db.session.commit()
    return job, None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_scheduled_network_freshness.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_scheduled_network_freshness.py
git commit -m "Add _create_scheduled_device_job for agent-mode-only scheduler job creation"
```

---

### Task 3: The scheduled job itself

**Files:**
- Modify: `app.py`, new functions placed immediately after `check_pro_plan_expirations_with_context` (`app.py:3214-3221`), new `scheduler.add_job(...)` line added to the registration block (`app.py:3244-3258`)
- Test: `tests/test_scheduled_network_freshness.py` (same file as Task 2)

**Interfaces:**
- Consumes: `_create_scheduled_device_job(device, operation)` from Task 2, `BusinessSettings`, `NetworkDevice`, `NetworkAgentJob`, `Tenant`, `db`, `logging`.
- Produces: `refresh_agent_mode_network_status_for_tenant(tenant_id)` (per-tenant, explicit `tenant_id`) and `refresh_agent_mode_network_status_with_context()` (the scheduler-registered wrapper). No other task depends on these.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scheduled_network_freshness.py`:

```python
def test_refresh_creates_both_jobs_for_an_agent_mode_olt(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(app, client, "Sched D", "sched_d_admin")
    with app.app_context():
        appmod.refresh_agent_mode_network_status_for_tenant(tenant_id)
        jobs = appmod.NetworkAgentJob.query.filter_by(tenant_id=tenant_id, device_id=device_id).all()
        operations = sorted(j.operation for j in jobs)
        assert operations == ['cpe_locations', 'olt_status']
        assert all(j.params == {'_scheduled': True} for j in jobs)


def test_refresh_skips_a_device_operation_with_an_outstanding_job(app, client):
    tenant_id, device_id = _make_agent_mode_tenant_with_olt(app, client, "Sched E", "sched_e_admin")
    with app.app_context():
        existing = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation='olt_status',
            status='pending')
        appmod.db.session.add(existing)
        appmod.db.session.commit()

        appmod.refresh_agent_mode_network_status_for_tenant(tenant_id)

        olt_status_jobs = appmod.NetworkAgentJob.query.filter_by(
            tenant_id=tenant_id, device_id=device_id, operation='olt_status').all()
        assert len(olt_status_jobs) == 1  # no duplicate created
        cpe_jobs = appmod.NetworkAgentJob.query.filter_by(
            tenant_id=tenant_id, device_id=device_id, operation='cpe_locations').all()
        assert len(cpe_jobs) == 1  # this one still gets created


def test_refresh_is_a_noop_for_a_direct_mode_tenant(app, client):
    make_tenant(client, "Sched F", "sched_f_admin")  # network_access_mode defaults to 'direct'
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Sched F").first()
        device = appmod.NetworkDevice(
            tenant_id=tenant.id, name="EPON OLT", host="192.168.8.101",
            username="", password="unused", device_type="vsol_olt", api_port=161)
        appmod.db.session.add(device)
        appmod.db.session.commit()

        appmod.refresh_agent_mode_network_status_for_tenant(tenant.id)

        assert appmod.NetworkAgentJob.query.filter_by(tenant_id=tenant.id).count() == 0


def test_refresh_with_context_isolates_one_tenants_failure(app, client, monkeypatch):
    tenant_a_id, _ = _make_agent_mode_tenant_with_olt(app, client, "Sched G", "sched_g_admin")
    tenant_b_id, device_b_id = _make_agent_mode_tenant_with_olt(app, client, "Sched H", "sched_h_admin")

    original = appmod.refresh_agent_mode_network_status_for_tenant

    def boom(tenant_id):
        if tenant_id == tenant_a_id:
            raise RuntimeError("simulated failure")
        return original(tenant_id)

    monkeypatch.setattr(appmod, "refresh_agent_mode_network_status_for_tenant", boom)

    with app.app_context():
        appmod.refresh_agent_mode_network_status_with_context()
        # Tenant B's job still got created despite tenant A's failure.
        jobs = appmod.NetworkAgentJob.query.filter_by(tenant_id=tenant_b_id, device_id=device_b_id).all()
        assert len(jobs) == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_scheduled_network_freshness.py -v`
Expected: FAIL on the 4 new tests — `AttributeError: module 'app' has no attribute 'refresh_agent_mode_network_status_for_tenant'`.

- [ ] **Step 3: Implement**

In `app.py`, immediately after `check_pro_plan_expirations_with_context` (right after `app.py:3221`, before the `# Start the scheduler in ONE runner only.` comment block), add:

```python
# --- Scheduled ONU/CPE-location freshness for agent-mode tenants ------------
# See docs/superpowers/specs/2026-09-17-network-tree-scheduled-freshness-design.md.
# The Network Tree page's own client-side auto-refresh only runs while
# someone has the page open, and its CPE-location write only for admin/
# finance viewers -- this keeps both fresh for agent-mode tenants regardless
# of who (if anyone) is looking.
NETWORK_FRESHNESS_OPERATIONS = ('olt_status', 'cpe_locations')


def refresh_agent_mode_network_status_for_tenant(tenant_id):
    """Enqueue a fresh olt_status + cpe_locations job for every OLT this
    tenant owns, skipping a device+operation pair that already has a
    pending/claimed job outstanding -- an agent offline for a while (or the
    scheduler firing twice in quick succession on deploy, see Dockerfile)
    must not accumulate an unbounded backlog of duplicate jobs. A no-op for
    a tenant not in 'agent' mode: _create_scheduled_device_job's own
    offline check would refuse anyway, but skipping the query entirely here
    avoids the wasted work for direct-mode tenants on every 15-minute tick."""
    settings = BusinessSettings.query.filter_by(tenant_id=tenant_id).first()
    if not settings or settings.network_access_mode != 'agent':
        return
    devices = NetworkDevice.query.filter_by(tenant_id=tenant_id, device_type='vsol_olt').all()
    for device in devices:
        for operation in NETWORK_FRESHNESS_OPERATIONS:
            outstanding = NetworkAgentJob.query.filter(
                NetworkAgentJob.tenant_id == tenant_id,
                NetworkAgentJob.device_id == device.id,
                NetworkAgentJob.operation == operation,
                NetworkAgentJob.status.in_(('pending', 'claimed')),
            ).first()
            if outstanding:
                continue
            _create_scheduled_device_job(device, operation)


def refresh_agent_mode_network_status_with_context():
    with app.app_context():
        for t in Tenant.query.filter_by(status="active").all():
            try:
                refresh_agent_mode_network_status_for_tenant(t.id)
            except Exception as e:
                db.session.rollback()
                logging.error(f"Scheduled network freshness refresh failed for tenant {t.id}: {e}")
```

Then, in the registration block (`app.py:3244-3258`), add one line alongside the others, before `scheduler.start()`:

```python
    scheduler.add_job(func=refresh_agent_mode_network_status_with_context, trigger="interval", minutes=15, next_run_time=datetime.now())
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_scheduled_network_freshness.py -v`
Expected: PASS — 7 tests total (3 from Task 2, 4 from this task).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_scheduled_network_freshness.py
git commit -m "Add scheduled ONU/CPE-location freshness job for agent-mode tenants"
```

---

### Task 4: `agent_post_result` auto-applies a completed scheduled `cpe_locations` job

**Files:**
- Modify: `app.py:10424-10501` (`agent_post_result`)
- Test: `tests/test_network_agent_api.py`

**Interfaces:**
- Consumes: `_apply_cpe_locations(result, tenant_id=None)` from Task 1.
- Produces: no new function — behavioral addition only to the existing route.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_network_agent_api.py` (reuse its existing `make_agent_and_device`, `make_job`, `auth` helpers — do not redefine them):

```python
def test_a_scheduled_cpe_locations_result_auto_applies(app, client):
    make_tenant(client, "Api Auto A", "api_auto_a_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api Auto A")
    with app.app_context():
        customer = appmod.Customer(
            tenant_id=tenant_id, name="Auto Customer", phone="1", address="a",
            cpe_mac_address="aa:aa:aa:aa:aa:aa")
        appmod.db.session.add(customer)
        job = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation="cpe_locations",
            params={'_scheduled': True})
        appmod.db.session.add(job)
        appmod.db.session.commit()
        job_id = job.id
    client.get("/api/agent/jobs", headers=auth(token))  # claims the job

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token), json={
        "ok": True,
        "result": {"aa:aa:aa:aa:aa:aa": {"onu_mac": "11:22:33:44:55:66"}},
        "error": None,
    })
    assert r.status_code == 200
    with app.app_context():
        customer = appmod.Customer.query.filter_by(tenant_id=tenant_id).first()
        assert customer.onu_mac_address == "11:22:33:44:55:66"


def test_a_human_triggered_cpe_locations_result_does_not_auto_apply(app, client):
    make_tenant(client, "Api Auto B", "api_auto_b_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api Auto B")
    with app.app_context():
        customer = appmod.Customer(
            tenant_id=tenant_id, name="Manual Customer", phone="2", address="b",
            cpe_mac_address="bb:bb:bb:bb:bb:bb")
        appmod.db.session.add(customer)
        # No _scheduled marker -- same as locate_customers's own job creation.
        job = appmod.NetworkAgentJob(
            tenant_id=tenant_id, device_id=device_id, operation="cpe_locations")
        appmod.db.session.add(job)
        appmod.db.session.commit()
        job_id = job.id
    client.get("/api/agent/jobs", headers=auth(token))

    client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token), json={
        "ok": True,
        "result": {"bb:bb:bb:bb:bb:bb": {"onu_mac": "11:22:33:44:55:66"}},
        "error": None,
    })
    with app.app_context():
        customer = appmod.Customer.query.filter_by(tenant_id=tenant_id).first()
        # Existing behavior unchanged: no auto-apply without the marker --
        # a human must still call apply_customer_locations separately.
        assert customer.onu_mac_address is None


def test_a_scheduled_olt_status_result_never_auto_applies(app, client):
    make_tenant(client, "Api Auto C", "api_auto_c_admin")
    token, device_id, tenant_id = make_agent_and_device(app, "Api Auto C")
    job_id = make_job(app, tenant_id, device_id, operation="olt_status")
    with app.app_context():
        job = appmod.NetworkAgentJob.query.get(job_id)
        job.params = {'_scheduled': True}
        appmod.db.session.commit()
    client.get("/api/agent/jobs", headers=auth(token))

    r = client.post(f"/api/agent/jobs/{job_id}/result", headers=auth(token), json={
        "ok": True, "result": [], "error": None,
    })
    # Just confirms no crash/side effect from the new hook on a non-cpe_locations
    # operation -- olt_status has its own, pre-existing handling, untouched.
    assert r.status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_network_agent_api.py -k auto_applies -v`
Expected: FAIL on the first two new tests (customer's `onu_mac_address` stays `None` when it should have been set by the first test) — the third should already pass since it only asserts no crash, but run all three together to confirm the intended RED state on the two that matter.

- [ ] **Step 3: Implement**

In `app.py`, in `agent_post_result` (`app.py:10424-10501`), change the ending from:

```python
    _complete_write_audit(job)
    db.session.commit()
    return jsonify({'message': 'Recorded'}), 200
```

to:

```python
    _complete_write_audit(job)
    db.session.commit()
    if job.operation == 'cpe_locations' and not job.error and (job.params or {}).get('_scheduled'):
        # Scheduler-created jobs (see _create_scheduled_device_job) have no
        # human waiting to click a separate "apply" button -- auto-apply
        # here, in the agent's own result-POST request. A human-triggered
        # cpe_locations job (no _scheduled marker) is unaffected: it still
        # requires the separate, JWT-gated apply_customer_locations call the
        # frontend already makes. Any failure here must not turn into a
        # failure response to the agent -- the job itself already completed
        # successfully; only the auto-apply step's own outcome is at risk.
        try:
            _apply_cpe_locations(job.result, tenant_id=job.tenant_id)
        except Exception as e:
            logging.error(f"Auto-apply of scheduled cpe_locations job {job.id} failed: {e}")
    return jsonify({'message': 'Recorded'}), 200
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_network_agent_api.py -k auto_applies -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Run the full `test_network_agent_api.py` suite**

This route is heavily tested already (700+ lines) — confirm nothing regressed.

Run: `python -m pytest tests/test_network_agent_api.py -q`
Expected: PASS, same count as before this task plus the 3 new tests.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_network_agent_api.py
git commit -m "Auto-apply a completed scheduled cpe_locations job's result"
```

---

## Post-implementation verification (not automatable from this environment)

1. Run the full backend suite once (`python -m pytest -q`) to confirm no cross-file regression.
2. Deploy. Confirm via Render logs that `refresh_agent_mode_network_status_with_context` fires (look for its tenant-scoped log lines only on failure — success is silent by design, so absence of the error log across a 15-minute window is itself the signal) for DeltaNet's own tenant (agent mode).
3. Confirm a customer's `onu_mac_address`/`onu_last_seen_at` updates on its own within roughly one 15-minute cycle, without any admin/finance browser tab open — e.g. check `Customer.onu_last_seen_at` for a known customer before and after a 15-20 minute wait.
4. Confirm an employee/collector viewing the Network Tree page sees the same updated placement (no code changes needed there — this is exactly the gap this feature closes).
