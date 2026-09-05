# Network Tree v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Network Tree page from a flat indented list into a top-down visual tree — CCR → {Ports, OLT} → PON → ONU → customer — that renders instantly from cached results, animates light only along links that are actually up, and can be searched.

**Architecture:** Two small backend changes (the tree endpoint returns each device's most recent completed job result; the MAC validator accepts hyphen/dot separators), then the page is rebuilt around one pure function, `buildTopologyTree`, that turns devices-plus-results into a typed node array. A single recursive `TreeNode` renders it. No schema change, no migration.

**Tech Stack:** Flask + SQLAlchemy, pytest. React 18 (CRA) + MUI, Jest via `react-scripts test`.

**Spec:** `docs/superpowers/specs/2026-09-05-network-tree-v2-design.md`

## Global Constraints

- **Never** use `logger.exception` or `exc_info=True` on any path whose frame locals can hold a device credential. Sentry's `LoggingIntegration` is live and captures frame locals at ERROR. Log `exc.__class__.__name__` and `str(exc)` only.
- No schema change and no Alembic migration in this plan. If you believe you need one, stop and raise it.
- The Alembic chain cannot replay on SQLite (origin's `bd054e2e7cf9` calls `op.create_unique_constraint` outside batch mode). Tests build schema via `create_all` in `tests/conftest.py`. Do not "fix" this here.
- `GET /api/network-tree` keeps `network_view_required()` — employee and collector must retain read access.
- Hardware must never silently vanish from this page. Every existing orphan/cycle guard in `_build_device_tree` stays; the new levels get the same tolerance.
- Do not use `git stash` — the stash stack is shared with other worktrees. Use a WIP commit if you must set work aside.
- Run the full suite (`python -m pytest -q -p no:warnings`) before each commit. It is 606 passing at the start of this plan.

---

## File Structure

| File | Responsibility |
|---|---|
| `app.py` | `_canonical_mac` (new), `_validate_mac_address`, `_normalize_mac`, `_latest_results_by_device` (new), `_build_device_tree`, `get_network_tree` |
| `tests/test_mac_address_formats.py` | **new** — separator acceptance and rejection |
| `tests/test_network_tree_endpoint.py` | extended — `last_result` attachment |
| `frontend/src/components/buildTopologyTree.js` | **new** — pure devices+results → node array |
| `frontend/src/components/buildTopologyTree.test.js` | **new** — unit tests for the above |
| `frontend/src/components/TreeNode.js` | **new** — one recursive presentational node |
| `frontend/src/components/networkTree.css` | **new** — layout, connectors, motion |
| `frontend/src/components/NetworkTreeView.js` | rebuilt around the above; keeps existing refresh/guard logic |

---

## Task 1: MAC addresses accept hyphen and dot separators

**Files:**
- Modify: `app.py` (near `_MAC_ADDRESS_RE`, currently line 2814, and `_normalize_mac`, currently line 9198)
- Test: `tests/test_mac_address_formats.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_canonical_mac(raw) -> str` — returns the colon-lowercase form of any MAC written with `:`, `-`, `.`, or no separator, or `''` if it is not twelve hex digits. Used by `_validate_mac_address` and `_normalize_mac`. No later task depends on it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mac_address_formats.py`:

```python
"""A MAC may be typed with colons, hyphens, dots or nothing at all.

Windows displays MACs with hyphens and the OLT's own web UI uses them in
places, so a colon-only validator turned an ordinary paste into a 400 that
read as a broken feature. Exactly one form -- colon-lowercase -- is ever
stored, so comparison stays a plain string match.
"""
import app as appmod
from tests.conftest import make_tenant


def _plan(client, hdr):
    return client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 10,
                             "billing_cycle": "monthly"}).get_json()["plan"]["id"]


def _customer(client, hdr, plan_id, mac):
    return client.post("/api/customers", headers=hdr,
                       json={"name": "C", "phone": "1", "address": "a",
                             "subscription_plan_id": plan_id,
                             "subscription_start_date": "2026-01-01",
                             "onu_mac_address": mac})


def test_canonical_mac_accepts_every_common_separator():
    for raw in ("dc:8e:8d:61:b0:61", "DC-8E-8D-61-B0-61", "dc.8e.8d.61.b0.61",
                "dc8e8d61b061", "DC8E8D61B061", "  dc:8E:8d:61:B0:61  "):
        assert appmod._canonical_mac(raw) == "dc:8e:8d:61:b0:61", raw


def test_canonical_mac_rejects_anything_that_is_not_twelve_hex_digits():
    for raw in ("", "dc:8e:8d:61:b0", "dc:8e:8d:61:b0:61:99",
                "zz:8e:8d:61:b0:61", "not a mac", None, 12345, ["dc"]):
        assert appmod._canonical_mac(raw) == "", repr(raw)


def test_customer_accepts_a_hyphenated_mac_and_stores_the_colon_form(app, client):
    hdr = make_tenant(client, "Mac A", "mac_a_admin")
    plan_id = _plan(client, hdr)
    resp = _customer(client, hdr, plan_id, "DC-8E-8D-61-B0-61")
    assert resp.status_code == 201, resp.get_json()
    with app.app_context():
        customer = appmod.Customer.query.filter_by(name="C").first()
        assert customer.onu_mac_address == "dc:8e:8d:61:b0:61"


def test_customer_still_rejects_a_malformed_mac(app, client):
    hdr = make_tenant(client, "Mac B", "mac_b_admin")
    plan_id = _plan(client, hdr)
    resp = _customer(client, hdr, plan_id, "dc-8e-8d-61-b0")
    assert resp.status_code == 400
    assert "not a valid MAC address" in resp.get_json()["error"]


def test_normalize_mac_matches_across_separator_styles():
    assert appmod._normalize_mac("DC-8E-8D-61-B0-61") == appmod._normalize_mac("dc:8e:8d:61:b0:61")
    # A value that is not a MAC at all keeps its old behaviour: stripped and
    # lowercased, never collapsed to '' where it could collide with another
    # malformed value.
    assert appmod._normalize_mac("  Garbage ") == "garbage"
    assert appmod._normalize_mac(12345) == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_mac_address_formats.py -q -p no:warnings`
Expected: FAIL — `AttributeError: module 'app' has no attribute '_canonical_mac'`

- [ ] **Step 3: Add `_canonical_mac` and route both helpers through it**

In `app.py`, replace the `_MAC_ADDRESS_RE` definition and `_validate_mac_address` (currently lines 2814–2841) with:

```python
_MAC_HEX_RE = re.compile(r'^[0-9a-f]{12}$')
_MAC_SEPARATOR_RE = re.compile(r'[\s:.\-]')


def _canonical_mac(raw):
    """The colon-lowercase form of a MAC, or '' if it isn't one.

    Accepts every separator a MAC is realistically pasted with -- colons,
    hyphens (Windows' display format, and what the OLT's own web UI shows in
    places), dots, or none at all. Exactly one form is ever stored, so
    comparison stays a plain string match rather than a fuzzy one.

    A non-string (e.g. a bare int from a malformed request body) returns ''
    rather than reaching .strip() and raising AttributeError.
    """
    if not isinstance(raw, str):
        return ''
    digits = _MAC_SEPARATOR_RE.sub('', raw).lower()
    if not _MAC_HEX_RE.match(digits):
        return ''
    return ':'.join(digits[i:i + 2] for i in range(0, 12, 2))


def _validate_mac_address(raw, allow_empty):
    """Normalize and validate a MAC address string.

    Returns (mac_or_None, error_or_None) -- exactly one is ever set.

    `allow_empty` controls what a blank value means: on the customer
    endpoints an empty/null value clears the link, so allow_empty=True makes
    that normalize to (None, None); /apply requires a non-empty MAC for every
    link, so it passes allow_empty=False and gets an error instead.
    """
    if raw is None:
        raw = ''
    if not isinstance(raw, str):
        return None, f"'{raw}' is not a valid MAC address (expected form aa:bb:cc:dd:ee:ff)."
    if not raw.strip():
        if allow_empty:
            return None, None
        return None, 'Every link needs a mac_address'
    mac = _canonical_mac(raw)
    if not mac:
        return None, f"'{raw}' is not a valid MAC address (expected form aa:bb:cc:dd:ee:ff)."
    return mac, None
```

Then replace `_normalize_mac` (currently line 9198) with:

```python
def _normalize_mac(mac):
    """Normalize a MAC for comparison, tolerant of separator style.

    Canonical values collapse to the colon-lowercase form, so a customer
    linked with hyphens still matches an ONU the OLT reports with colons.
    Anything that isn't a MAC keeps the old behaviour -- stripped and
    lowercased -- rather than collapsing to '', which would make two
    different malformed values compare equal to each other.

    A value that isn't a string -- e.g. an int, from a malformed agent
    payload where a colon-less MAC got parsed as a number -- normalizes to
    '' so it never matches a real MAC and never raises.
    """
    canonical = _canonical_mac(mac)
    if canonical:
        return canonical
    return mac.strip().lower() if isinstance(mac, str) else ''
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_mac_address_formats.py -q -p no:warnings`
Expected: PASS (5 tests)

Run: `python -m pytest -q -p no:warnings`
Expected: PASS, 611 total

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_mac_address_formats.py
git commit -m "fix: accept hyphen and dot separated MAC addresses"
```

---

## Task 2: The tree endpoint returns each device's last known result

**Files:**
- Modify: `app.py` — `_build_device_tree` (currently line 9080), `get_network_tree` (currently line 9789)
- Test: `tests/test_network_tree_endpoint.py` (extend)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: each node in `GET /api/network-tree`'s `tree` gains three keys —
  `last_result` (the enriched result, or `null`), `last_result_at`
  (`'%Y-%m-%d %H:%M:%S'` string, or `null`) and `last_result_operation`
  (`'olt_status' | 'device_health' | null`). Task 3's `buildTopologyTree`
  reads exactly these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_network_tree_endpoint.py`:

```python
# --- Tree v2: the page renders from the last known result, so the endpoint
# carries it. Jobs already store results for 7 days; this is a read, not new
# storage. See docs/superpowers/specs/2026-09-05-network-tree-v2-design.md.

def _tree_by_id(client, hdr):
    def walk(nodes, out):
        for n in nodes:
            out[n["id"]] = n
            walk(n.get("children") or [], out)
        return out
    return walk(client.get("/api/network-tree", headers=hdr).get_json()["tree"], {})


def test_tree_carries_the_newest_completed_olt_result(app, client, monkeypatch):
    hdr = make_tenant(client, "Tree P", "tree_p_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    add_customer(app, "Tree P", "Moussa Ghadir", "b4:64:15:3f:c1:94")
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))
    refresh_and_poll(client, hdr, olt["id"])

    node = _tree_by_id(client, hdr)[olt["id"]]
    assert node["last_result_operation"] == "olt_status"
    assert node["last_result_at"]
    macs = [o["mac_address"] for o in node["last_result"]]
    assert "b4:64:15:3f:c1:94" in macs
    # Enriched at read time, exactly as the job-poll endpoint does it.
    first = node["last_result"][0]
    assert [c["name"] for c in first["customers"]] == ["Moussa Ghadir"]


def test_tree_carries_the_ccr_result_with_interface_labels_applied(app, client, monkeypatch):
    hdr = make_tenant(client, "Tree Q", "tree_q_admin")
    ccr = make_ccr(client, hdr)
    monkeypatch.setattr(appmod.mikrotik, "get_device_health", lambda d: (
        True, {"identity": "CCR", "uptime": "1d",
               "interfaces": [{"name": "ether1", "running": True, "disabled": False}]}))
    client.patch(f"/api/network-devices/{ccr['id']}/interface-labels", headers=hdr,
                 json={"interface_name": "ether1", "label": "MYISP"})
    started = client.post(f"/api/network-devices/{ccr['id']}/check-now", headers=hdr).get_json()
    assert started["ok"] is True

    node = _tree_by_id(client, hdr)[ccr["id"]]
    assert node["last_result_operation"] == "device_health"
    assert node["last_result"]["interfaces"][0]["label"] == "MYISP"


def test_tree_reports_no_result_for_a_device_that_has_never_been_checked(app, client):
    hdr = make_tenant(client, "Tree R", "tree_r_admin")
    ccr = make_ccr(client, hdr)
    node = _tree_by_id(client, hdr)[ccr["id"]]
    assert node["last_result"] is None
    assert node["last_result_at"] is None
    assert node["last_result_operation"] is None


def test_tree_ignores_jobs_that_are_not_done(app, client, monkeypatch):
    """A pending or failed job must not be mistaken for a result."""
    hdr = make_tenant(client, "Tree S", "tree_s_admin")
    ccr = make_ccr(client, hdr)
    olt = make_olt(client, hdr, ccr["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))
    started = client.post(f"/api/network-tree/olt/{olt['id']}/refresh", headers=hdr).get_json()
    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, started["job_id"])
        job.status = "failed"
        appmod.db.session.commit()

    assert _tree_by_id(client, hdr)[olt["id"]]["last_result"] is None


def test_tree_results_stay_tenant_scoped(app, client, monkeypatch):
    hdr_one = make_tenant(client, "Tree T1", "tree_t1_admin")
    ccr_one = make_ccr(client, hdr_one)
    olt_one = make_olt(client, hdr_one, ccr_one["id"])
    monkeypatch.setattr(appmod.vsol_olt, "get_olt_status", lambda d: (True, ONUS))
    refresh_and_poll(client, hdr_one, olt_one["id"])

    hdr_two = make_tenant(client, "Tree T2", "tree_t2_admin")
    ccr_two = make_ccr(client, hdr_two)
    olt_two = make_olt(client, hdr_two, ccr_two["id"])
    assert _tree_by_id(client, hdr_two)[olt_two["id"]]["last_result"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_network_tree_endpoint.py -q -p no:warnings -k "last_result or carries or never_been_checked or not_done or tenant_scoped"`
Expected: FAIL with `KeyError: 'last_result_operation'`

- [ ] **Step 3: Implement**

In `app.py`, immediately **above** `def _build_device_tree(devices):`, add:

```python
def _latest_results_by_device(devices):
    """The newest completed job result for each device, enriched for display.

    The tree page renders this the moment it opens, so it never starts empty.
    Completed jobs already persist their full result and are retained for
    NETWORK_AGENT_JOB_RETENTION_DAYS, so this reads data that already exists
    rather than adding storage.

    Enrichment goes through the same helpers the job-poll endpoint uses --
    _resolve_onu_customers and _with_interface_labels -- so the shapes the
    page receives here and from polling a live job are identical, and both
    inherit those helpers' tolerance of malformed stored results.
    """
    if not devices:
        return {}
    jobs = (tenant_query(NetworkAgentJob)
            .filter(NetworkAgentJob.device_id.in_([d.id for d in devices]),
                    NetworkAgentJob.status == 'done')
            .order_by(NetworkAgentJob.device_id,
                      NetworkAgentJob.created_at.desc(),
                      NetworkAgentJob.id.desc())
            .all())
    newest = {}
    for job in jobs:
        # Ordered newest-first per device, so the first one wins.
        newest.setdefault(job.device_id, job)

    out = {}
    for device_id, job in newest.items():
        result = job.result
        if job.operation == 'olt_status' and result:
            result = _resolve_onu_customers(result)
        elif job.operation == 'device_health':
            result = _with_interface_labels(job, {'result': result}).get('result')
        out[device_id] = {
            'operation': job.operation,
            'result': result,
            'at': job.finished_at.strftime('%Y-%m-%d %H:%M:%S') if job.finished_at else None,
        }
    return out
```

Change `_build_device_tree`'s signature and its `node()` return. The signature becomes:

```python
def _build_device_tree(devices, latest_results=None):
```

and inside, the `node` function's return statement (currently `return {**device.to_dict(), 'children': children}`) becomes:

```python
        latest = (latest_results or {}).get(device.id) or {}
        return {**device.to_dict(),
                'last_result': latest.get('result'),
                'last_result_at': latest.get('at'),
                'last_result_operation': latest.get('operation'),
                'children': children}
```

The default of `None` keeps every existing caller and test working unchanged.

Finally, in `get_network_tree`, replace the body with:

```python
    devices = tenant_query(NetworkDevice).order_by(NetworkDevice.name).all()
    return jsonify({'tree': _build_device_tree(
        devices, _latest_results_by_device(devices))}), 200
```

and update its docstring's first line to:

```python
    """The device skeleton plus each device's last known result -- no device is
    contacted here. Live data is refreshed per-device, on demand, via the
    refresh endpoints; this is what lets the page render before any of that
    completes."""
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_network_tree_endpoint.py -q -p no:warnings`
Expected: PASS

Run: `python -m pytest -q -p no:warnings`
Expected: PASS, 616 total

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_network_tree_endpoint.py
git commit -m "feat: return each device's last known result from the tree endpoint"
```

---

## Task 3: `buildTopologyTree` — the pure shape of the tree

**Files:**
- Create: `frontend/src/components/buildTopologyTree.js`
- Test: `frontend/src/components/buildTopologyTree.test.js`

**Interfaces:**
- Consumes: the API node shape from Task 2 — `{id, name, host, api_port, device_type, last_status, last_checked_at, interface_labels, last_result, last_result_at, last_result_operation, children}`.
- Produces: `buildTopologyTree(devices) -> Node[]` where
  `Node = {key, kind, label, sublabel, meta, status, deviceId, searchText, children}`,
  `kind ∈ 'device'|'ports'|'interface'|'pon'|'onu'|'customer'`, and
  `status ∈ 'up'|'down'|'warn'|'unknown'`. Also exports
  `PON_UNASSIGNED = 'Unassigned'` and `nodeMatches(node, query) -> boolean`.
  Tasks 4, 5 and 7 consume these names exactly.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/buildTopologyTree.test.js`:

```js
import { buildTopologyTree, PON_UNASSIGNED, nodeMatches } from './buildTopologyTree';

const onu = (over = {}) => ({
    pon_port: 'PON1', onu_id: 'EPON0/1:2', status: 'online',
    mac_address: 'b4:64:15:3f:c1:94', description: 'MoussaGhadir',
    distance_m: 531, customers: [], ...over,
});

const olt = (over = {}) => ({
    id: 2, name: 'V-SOL OLT', host: '192.168.8.100', api_port: 161,
    device_type: 'vsol_olt', last_status: 'online', interface_labels: {},
    last_result_operation: 'olt_status', last_result_at: '2026-09-05 12:00:00',
    last_result: [onu()], children: [], ...over,
});

const ccr = (over = {}) => ({
    id: 1, name: 'CCR1009', host: '192.168.100.1', api_port: 8728,
    device_type: 'mikrotik_ccr', last_status: 'online', interface_labels: {},
    last_result_operation: null, last_result_at: null, last_result: null,
    children: [], ...over,
});

const find = (nodes, kind, label) => {
    for (const n of nodes) {
        if (n.kind === kind && (label === undefined || n.label === label)) return n;
        const hit = find(n.children || [], kind, label);
        if (hit) return hit;
    }
    return null;
};

test('an empty device list yields an empty tree', () => {
    expect(buildTopologyTree([])).toEqual([]);
    expect(buildTopologyTree(undefined)).toEqual([]);
});

test('ONUs group into PON nodes under their OLT', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu(), onu({ pon_port: 'PON3', mac_address: 'aa:bb:cc:dd:ee:ff' })],
    })] })]);
    const pons = find(tree, 'device', 'V-SOL OLT').children;
    expect(pons.map((p) => p.label)).toEqual(['PON1', 'PON3']);
    expect(pons[0].children).toHaveLength(1);
    expect(pons[0].children[0].kind).toBe('onu');
});

test('a PON node counts its ONUs and how many are up', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu(), onu({ mac_address: 'aa:bb:cc:dd:ee:01', status: 'offline' })],
    })] })]);
    const pon = find(tree, 'pon', 'PON1');
    expect(pon.meta).toBe('2 ONUs · 1 up');
    expect(pon.status).toBe('up');
});

test('a PON whose every ONU is offline reads as down', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu({ status: 'offline' })],
    })] })]);
    expect(find(tree, 'pon', 'PON1').status).toBe('down');
});

test('an ONU with a missing or malformed pon_port lands under Unassigned, never dropped', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu({ pon_port: undefined }), onu({ pon_port: 42, mac_address: 'aa:bb:cc:dd:ee:02' })],
    })] })]);
    const unassigned = find(tree, 'pon', PON_UNASSIGNED);
    expect(unassigned.children).toHaveLength(2);
});

test('a non-object entry in the result is skipped without throwing', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu(), null, 'nonsense'],
    })] })]);
    expect(find(tree, 'pon', 'PON1').children).toHaveLength(1);
});

test('a result that is not a list yields no PON nodes and does not throw', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({ last_result: { oops: true } })] })]);
    expect(find(tree, 'device', 'V-SOL OLT').children).toEqual([]);
});

test('customers hang off their ONU with the MAC they are linked by', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu({ customers: [
            { id: 7, name: 'Moussa Ghadir', is_subscription_active: true,
              onu_mac_address: 'b4:64:15:3f:c1:94' },
            { id: 8, name: 'Shop', is_subscription_active: false,
              onu_mac_address: 'b4:64:15:3f:c1:94' },
        ] })],
    })] })]);
    const customers = find(tree, 'onu').children;
    expect(customers.map((c) => c.kind)).toEqual(['customer', 'customer']);
    expect(customers[0].sublabel).toBe('b4:64:15:3f:c1:94');
    expect(customers[1].status).toBe('warn');
});

test('a CCR with a health result gains a Ports branch before its child devices', () => {
    const tree = buildTopologyTree([ccr({
        interface_labels: { ether1: 'MYISP' },
        last_result_operation: 'device_health',
        last_result: { interfaces: [
            { name: 'ether1', running: true, disabled: false, label: 'MYISP' },
            { name: 'ether6', running: false, disabled: false, label: null },
        ] },
        children: [olt()],
    })]);
    const kinds = tree[0].children.map((c) => c.kind);
    expect(kinds).toEqual(['ports', 'device']);
    const ports = find(tree, 'ports');
    expect(ports.meta).toBe('1 of 2 up');
    expect(ports.children[0].label).toBe('MYISP');
    expect(ports.children[0].sublabel).toBe('ether1');
    expect(ports.children[1].label).toBe('ether6');
    expect(ports.children[1].status).toBe('down');
});

test('a disabled interface reads as down, not up', () => {
    const tree = buildTopologyTree([ccr({
        last_result_operation: 'device_health',
        last_result: { interfaces: [{ name: 'ether2', running: true, disabled: true }] },
    })]);
    expect(find(tree, 'interface').status).toBe('down');
});

test('a CCR with no health result has no Ports branch', () => {
    const tree = buildTopologyTree([ccr({ children: [olt()] })]);
    expect(tree[0].children.map((c) => c.kind)).toEqual(['device']);
});

test('every node key is unique across the whole tree', () => {
    const tree = buildTopologyTree([ccr({
        last_result_operation: 'device_health',
        last_result: { interfaces: [{ name: 'ether1', running: true, disabled: false }] },
        children: [olt({ last_result: [onu(), onu({ mac_address: 'aa:bb:cc:dd:ee:03' })] })],
    })]);
    const keys = [];
    (function walk(nodes) {
        nodes.forEach((n) => { keys.push(n.key); walk(n.children || []); });
    })(tree);
    expect(new Set(keys).size).toBe(keys.length);
});

test('nodeMatches searches label, sublabel and meta case-insensitively', () => {
    const node = { label: 'MoussaGhadir', sublabel: 'b4:64:15:3f:c1:94', meta: '531 m' };
    expect(nodeMatches(node, 'moussa')).toBe(true);
    expect(nodeMatches(node, 'C1:94')).toBe(true);
    expect(nodeMatches(node, '')).toBe(true);
    expect(nodeMatches(node, 'nothing')).toBe(false);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx react-scripts test --watchAll=false src/components/buildTopologyTree.test.js`
Expected: FAIL — `Cannot find module './buildTopologyTree'`

- [ ] **Step 3: Implement**

Create `frontend/src/components/buildTopologyTree.js`:

```js
/**
 * Turn the /api/network-tree payload into the node array the page renders.
 *
 * Pure on purpose: every rule about what the tree's levels ARE lives here and
 * nowhere else, so it can be unit-tested without rendering anything, and
 * TreeNode stays a presentational component with no knowledge of PON ports or
 * interfaces.
 *
 * Deliberately defensive throughout. `last_result` is whatever JSON was stored
 * when the job completed -- in agent mode, whatever the on-prem agent posted --
 * so a malformed entry must be skipped or bucketed, never dropped silently in
 * a way that makes real hardware vanish from the page, and never thrown on.
 */

export const PON_UNASSIGNED = 'Unassigned';

const asArray = (value) => (Array.isArray(value) ? value : []);

/** ONU/interface status collapses to the four the UI paints. */
const onuStatus = (status) => (status === 'online' ? 'up' : 'down');

const interfaceStatus = (iface) =>
    (iface.running && !iface.disabled ? 'up' : 'down');

const deviceStatus = (lastStatus) => {
    if (lastStatus === 'online') return 'up';
    if (!lastStatus) return 'unknown';
    return 'down';   // 'unreachable', 'auth_failed', anything else
};

const searchTextOf = (...parts) =>
    parts.filter(Boolean).join(' ').toLowerCase();

/** Case-insensitive substring match over a node's visible text. */
export function nodeMatches(node, query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) return true;
    const text = node.searchText
        || searchTextOf(node.label, node.sublabel, node.meta);
    return text.includes(q);
}

function customerNode(customer, ponKey) {
    return {
        key: `${ponKey}/cust-${customer.id}`,
        kind: 'customer',
        label: customer.name || 'Unnamed customer',
        sublabel: customer.onu_mac_address || '',
        meta: '',
        status: customer.is_subscription_active ? 'up' : 'warn',
        searchText: searchTextOf(customer.name, customer.onu_mac_address),
        children: [],
    };
}

function onuNode(onu, ponKey, index) {
    const mac = typeof onu.mac_address === 'string' ? onu.mac_address : '';
    const key = `${ponKey}/onu-${mac || index}`;
    const distance = Number(onu.distance_m) > 0 ? `${onu.distance_m} m` : '';
    return {
        key,
        kind: 'onu',
        label: onu.description || onu.onu_id || mac || 'ONU',
        sublabel: mac,
        meta: distance,
        status: onuStatus(onu.status),
        searchText: searchTextOf(onu.description, onu.onu_id, mac),
        children: asArray(onu.customers)
            .filter((c) => c && typeof c === 'object')
            .map((c) => customerNode(c, key)),
    };
}

/** Group an OLT's ONU list into PON nodes. Order follows first appearance. */
function ponNodes(device) {
    const onus = asArray(device.last_result).filter((o) => o && typeof o === 'object');
    const groups = new Map();
    onus.forEach((onu) => {
        // A pon_port that is missing, blank, or not a string cannot be trusted
        // as a group name -- but the ONU is still real hardware, so it gets a
        // bucket rather than being dropped.
        const port = typeof onu.pon_port === 'string' && onu.pon_port.trim()
            ? onu.pon_port.trim()
            : PON_UNASSIGNED;
        if (!groups.has(port)) groups.set(port, []);
        groups.get(port).push(onu);
    });

    return [...groups.entries()].map(([port, members]) => {
        const key = `dev-${device.id}/pon-${port}`;
        const up = members.filter((o) => o.status === 'online').length;
        return {
            key,
            kind: 'pon',
            label: port,
            sublabel: '',
            meta: `${members.length} ONU${members.length === 1 ? '' : 's'} · ${up} up`,
            status: up > 0 ? 'up' : 'down',
            searchText: searchTextOf(port),
            children: members.map((onu, i) => onuNode(onu, key, i)),
        };
    });
}

/** The synthetic Ports branch: a CCR's own interfaces, as a sibling of its children. */
function portsNode(device) {
    const result = device.last_result;
    if (device.last_result_operation !== 'device_health'
        || !result || typeof result !== 'object') return null;
    const interfaces = asArray(result.interfaces).filter((i) => i && typeof i === 'object');
    if (!interfaces.length) return null;

    const key = `dev-${device.id}/ports`;
    const up = interfaces.filter(interfaceStatus_isUp).length;
    return {
        key,
        kind: 'ports',
        label: 'Ports',
        sublabel: '',
        meta: `${up} of ${interfaces.length} up`,
        status: up > 0 ? 'up' : 'down',
        searchText: 'ports interfaces',
        children: interfaces.map((iface, i) => {
            const name = typeof iface.name === 'string' ? iface.name : '';
            const label = typeof iface.label === 'string' && iface.label ? iface.label : '';
            return {
                key: `${key}/if-${name || i}`,
                kind: 'interface',
                label: label || name || 'interface',
                sublabel: label ? name : '',
                meta: '',
                status: interfaceStatus(iface),
                searchText: searchTextOf(label, name),
                children: [],
            };
        }),
    };
}

function interfaceStatus_isUp(iface) {
    return interfaceStatus(iface) === 'up';
}

function deviceNode(device) {
    const children = [];
    const ports = portsNode(device);
    if (ports) children.push(ports);           // Ports always precedes child devices
    if (device.device_type === 'vsol_olt') children.push(...ponNodes(device));
    asArray(device.children).forEach((child) => children.push(deviceNode(child)));

    return {
        key: `dev-${device.id}`,
        kind: 'device',
        label: device.name || 'Device',
        sublabel: `${device.host || ''}${device.api_port ? `:${device.api_port}` : ''}`,
        meta: '',
        status: deviceStatus(device.last_status),
        deviceId: device.id,
        deviceType: device.device_type,
        lastResultAt: device.last_result_at || null,
        searchText: searchTextOf(device.name, device.host),
        children,
    };
}

export function buildTopologyTree(devices) {
    return asArray(devices).map(deviceNode);
}
```

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx react-scripts test --watchAll=false src/components/buildTopologyTree.test.js`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/buildTopologyTree.js frontend/src/components/buildTopologyTree.test.js
git commit -m "feat: add buildTopologyTree, the pure shape of the network tree"
```

---

## Task 4: `TreeNode` — top-down rendering with connectors and grid wrap

**Files:**
- Create: `frontend/src/components/TreeNode.js`
- Create: `frontend/src/components/networkTree.css`
- Modify: `frontend/src/components/NetworkTreeView.js`

**Interfaces:**
- Consumes: `buildTopologyTree`, `PON_UNASSIGNED` from Task 3.
- Produces: `<TreeNode node expanded onToggle liveLinks />` where `expanded` is a
  `Set` of node keys, `onToggle(key)` flips one, and `liveLinks` is a boolean
  (Task 5 uses it). Task 7 passes a filtered tree and a pre-computed `expanded`
  set through the same props.

- [ ] **Step 1: Write the CSS**

Create `frontend/src/components/networkTree.css`:

```css
/* Network Tree v2 -- top-down layout.
   Colours come from MUI's palette via CSS custom properties set by
   NetworkTreeView, so light and dark themes both work. */

.nt-root { --nt-gap: 28px; overflow-x: auto; padding: 8px 4px 24px; }

.nt-level { display: flex; justify-content: center; gap: 12px; flex-wrap: wrap; }

/* A subtree: the node's own card, its connector, then its children row. */
.nt-sub { display: flex; flex-direction: column; align-items: center; }

.nt-card {
    position: relative; min-width: 132px; max-width: 220px;
    padding: 8px 12px; border-radius: 10px;
    background: var(--nt-surface); border: 1px solid var(--nt-border);
    text-align: left; cursor: default;
    transition: transform 160ms ease-out, border-color 160ms ease-out;
}
.nt-card--interactive { cursor: pointer; }
.nt-card--interactive:hover { transform: translateY(-2px); border-color: var(--nt-border-strong); }
.nt-card--interactive:active { transform: scale(0.98); }
.nt-card:focus-visible { outline: 2px solid var(--nt-accent); outline-offset: 2px; }

.nt-card--up   { border-color: var(--nt-up); }
.nt-card--down { border-color: var(--nt-down); background: var(--nt-down-bg); }
.nt-card--warn { border-color: var(--nt-warn); }

.nt-title { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; }
.nt-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.nt-sub-line, .nt-meta { font-size: 11px; opacity: 0.75; }
.nt-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

/* Connector: a vertical stem under a parent, a horizontal rail over a row of
   children, and a stem down into each child. Drawn with borders, so there is
   no SVG to keep in sync with a wrapping flex layout. */
.nt-stem { width: 0; height: var(--nt-gap); border-left: 1px solid var(--nt-link); }
.nt-children { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center;
               border-top: 1px solid var(--nt-link); padding-top: var(--nt-gap);
               position: relative; }
.nt-children > .nt-sub::before {
    content: ""; position: absolute; top: 0; width: 0; height: var(--nt-gap);
    border-left: 1px solid var(--nt-link);
}
.nt-children > .nt-sub { position: relative; }
.nt-children > .nt-sub::before { left: 50%; }

/* A wide level (many ONUs under one PON) wraps into rows rather than growing
   sideways; below 900px every level becomes one card per row. */
.nt-children--wide { max-width: 940px; }
@media (max-width: 900px) {
    .nt-children, .nt-children--wide { flex-direction: column; align-items: center; }
}

.nt-count { font-size: 11px; opacity: 0.7; margin-left: 4px; }
```

- [ ] **Step 2: Write `TreeNode`**

Create `frontend/src/components/TreeNode.js`:

```js
import React from 'react';
import './networkTree.css';

const DOT = { up: 'var(--nt-up)', down: 'var(--nt-down)', warn: 'var(--nt-warn)', unknown: 'var(--nt-muted)' };

/**
 * One node and, when expanded, its children row.
 *
 * Purely presentational: every decision about what the levels mean lives in
 * buildTopologyTree. This component knows only how to draw a card, a
 * connector, and a row of subtrees.
 */
export default function TreeNode({ node, expanded, onToggle, liveLinks, actions }) {
    const children = node.children || [];
    const canExpand = children.length > 0;
    const isOpen = canExpand && expanded.has(node.key);
    const wide = children.length > 6;

    return (
        <div className="nt-sub">
            <div
                className={`nt-card nt-card--${node.status}${canExpand ? ' nt-card--interactive' : ''}`}
                role={canExpand ? 'button' : undefined}
                tabIndex={canExpand ? 0 : undefined}
                aria-expanded={canExpand ? isOpen : undefined}
                aria-label={`${node.label}${node.meta ? `, ${node.meta}` : ''}, ${node.status}`}
                onClick={canExpand ? () => onToggle(node.key) : undefined}
                onKeyDown={canExpand ? (e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(node.key); }
                } : undefined}
            >
                <div className="nt-title">
                    <span className="nt-dot" style={{ background: DOT[node.status] }} />
                    <span>{node.label}</span>
                    {canExpand && <span className="nt-count">{isOpen ? '▾' : '▸'}</span>}
                </div>
                {node.sublabel && <div className="nt-sub-line nt-mono">{node.sublabel}</div>}
                {node.meta && <div className="nt-meta">{node.meta}</div>}
            </div>

            {isOpen && (
                <>
                    <div className="nt-stem" />
                    <div className={`nt-children${wide ? ' nt-children--wide' : ''}`}>
                        {children.map((child) => (
                            <TreeNode key={child.key} node={child} expanded={expanded}
                                      onToggle={onToggle} liveLinks={liveLinks}
                                      actions={actions} />
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}
```

- [ ] **Step 3: Wire it into `NetworkTreeView`**

In `frontend/src/components/NetworkTreeView.js`:

1. Add the imports:

```js
import TreeNode from './TreeNode';
import { buildTopologyTree } from './buildTopologyTree';
```

2. Replace the `expanded` state's use with a `Set`:

```js
    const [expanded, setExpanded] = useState(() => new Set());
    const toggleNode = useCallback((key) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key); else next.add(key);
            return next;
        });
    }, []);
```

3. Derive the node tree from the API tree plus any live results already merged
   into it:

```js
    const topology = useMemo(() => buildTopologyTree(tree), [tree]);
```

4. Replace the `tree.map((root) => renderDevice(root, 0))` render with:

```js
                <Box className="nt-root" sx={{
                    '--nt-surface': (t) => t.palette.background.paper,
                    '--nt-border': (t) => t.palette.divider,
                    '--nt-border-strong': (t) => t.palette.text.secondary,
                    '--nt-link': (t) => t.palette.divider,
                    '--nt-up': (t) => t.palette.success.main,
                    '--nt-down': (t) => t.palette.error.main,
                    '--nt-down-bg': (t) => t.palette.error.light + '22',
                    '--nt-warn': (t) => t.palette.warning.main,
                    '--nt-muted': (t) => t.palette.text.disabled,
                    '--nt-accent': (t) => t.palette.primary.main,
                }}>
                    <div className="nt-level">
                        {topology.map((root) => (
                            <TreeNode key={root.key} node={root} expanded={expanded}
                                      onToggle={toggleNode} liveLinks />
                        ))}
                    </div>
                </Box>
```

5. Delete the now-unused `renderOnu` and `renderDevice` functions and any imports
   they alone used (`Collapse`, `Divider`, `PersonIcon`, `ChevronRightIcon`,
   `ExpandMoreIcon`, `onuStatusColor`, `STATUS_LABEL`/`STATUS_COLOR` if nothing
   else references them).

   **The per-device buttons must survive this deletion.** `renderDevice` is
   where *Match Labels* and *Load ONUs* live today, so `TreeNode` takes an
   `actions` render prop and calls it for device nodes only:

```js
                {node.kind === 'device' && actions && (
                    <div className="nt-actions">{actions(node)}</div>
                )}
```

   placed inside the card, after the meta line, with `actions` threaded through
   the recursive call alongside `expanded`/`onToggle`/`liveLinks`. Add to
   `networkTree.css`:

```css
.nt-actions { display: flex; gap: 4px; margin-top: 6px; }
/* Buttons live inside a card that is itself a click target, so their clicks
   must not also toggle the node. */
.nt-actions > * { pointer-events: auto; }
```

   and stop the propagation in the handler, since the card is clickable:

```js
                    <div className="nt-actions" onClick={(e) => e.stopPropagation()}>
```

   `NetworkTreeView` supplies the render prop, moving today's JSX across
   unchanged — same `agentOffline` disabling, same tooltips, same
   `canEditLinks` gate on *Match Labels*:

```js
    const deviceActions = useCallback((node) => {
        const device = deviceById.get(node.deviceId);
        if (!device || device.device_type !== 'vsol_olt') return null;
        return (<>{/* Match Labels + Load ONUs, exactly as rendered today */}</>);
    }, [deviceById, agentOffline, agentOfflineReason, refreshingIds, canEditLinks]);
```

   where `deviceById` is a `useMemo` map built by walking the API `tree`, since
   the node array carries `deviceId` but not the raw device row.

- [ ] **Step 4: Verify in the browser**

Run the app against the scratch database (see the session's `run_test_backend.py`
pattern: pin `DATABASE_URL` and `os.chdir` to this worktree, and assert both).
Confirm: the CCR renders at the top, the OLT below it, clicking the OLT reveals
PON nodes, clicking a PON reveals its ONUs, clicking an ONU reveals customers.

Run: `cd frontend && npx react-scripts test --watchAll=false`
Expected: PASS (existing tests plus Task 3's still green)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TreeNode.js frontend/src/components/networkTree.css frontend/src/components/NetworkTreeView.js
git commit -m "feat: render the network tree top-down with connectors"
```

---

## Task 5: Light travels only along links that are up

**Files:**
- Modify: `frontend/src/components/networkTree.css`
- Modify: `frontend/src/components/TreeNode.js`

**Interfaces:**
- Consumes: `TreeNode`'s `liveLinks` prop and `node.status` from Task 4.
- Produces: no new exports. A child's incoming connector carries the class
  `nt-flow` when that child's status is `up` and `liveLinks` is true.

- [ ] **Step 1: Add the motion CSS**

Append to `frontend/src/components/networkTree.css`:

```css
/* A link carries light only when the thing at its far end is actually up, so a
   dark branch reads as dark at a glance -- the motion states the status rather
   than decorating the page. Animating background-position on a gradient stays
   on the compositor: no layout, no paint of surrounding content. */
.nt-children > .nt-sub.nt-flow::before {
    border-left-color: transparent;
    width: 1px;
    background-image: linear-gradient(
        var(--nt-up) 0 18%, transparent 18% 100%);
    background-size: 100% 26px;
    background-repeat: repeat-y;
    animation: nt-travel 2.4s linear infinite;
}

@keyframes nt-travel { from { background-position: 0 -26px; } to { background-position: 0 0; } }

@media (prefers-reduced-motion: reduce) {
    .nt-children > .nt-sub.nt-flow::before { animation: none; }
    .nt-card { transition: none; }
    .nt-card--interactive:hover, .nt-card--interactive:active { transform: none; }
}
```

- [ ] **Step 2: Apply the class**

In `frontend/src/components/TreeNode.js`, change the outer wrapper so a node
declares whether its own incoming link is live:

```js
    const flowing = liveLinks && node.status === 'up';
    return (
        <div className={`nt-sub${flowing ? ' nt-flow' : ''}`}>
```

- [ ] **Step 3: Verify**

In the browser: expand the OLT. PON nodes with at least one ONU up show light
travelling down into them; a PON where everything is offline is a plain static
line. Then set the OS "reduce motion" preference (Windows: Settings → Accessibility
→ Visual effects → Animation effects off) and reload: the tree is identical but
still, and status is still readable from the ring and dot colours.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/networkTree.css frontend/src/components/TreeNode.js
git commit -m "feat: animate light only along links whose far end is up"
```

---

## Task 6: Render from cache, refresh only when stale

**Files:**
- Modify: `frontend/src/components/NetworkTreeView.js`

**Interfaces:**
- Consumes: `last_result` / `last_result_at` from Task 2, `buildTopologyTree` from Task 3.
- Produces: no new exports.

- [ ] **Step 1: Add the staleness constant and the age helper**

Near the top of `frontend/src/components/NetworkTreeView.js`:

```js
// Auto-refresh a device only when its cached result is older than this. Without
// a cap, every visit to the page would fire a fresh 13-second SNMP walk of the
// OLT; with one, opening the page repeatedly is free.
const STALE_AFTER_MS = 5 * 60 * 1000;

/** '2026-09-05 12:00:00' (UTC, as the API emits it) -> "4 min ago". */
export function describeAge(stamp, now = Date.now()) {
    if (!stamp) return 'never checked';
    const then = Date.parse(stamp.replace(' ', 'T') + 'Z');
    if (Number.isNaN(then)) return '';
    const mins = Math.floor((now - then) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} h ago`;
    return `${Math.floor(hours / 24)} d ago`;
}

function isStale(stamp, now = Date.now()) {
    if (!stamp) return true;
    const then = Date.parse(stamp.replace(' ', 'T') + 'Z');
    return Number.isNaN(then) || (now - then) > STALE_AFTER_MS;
}
```

- [ ] **Step 2: Auto-refresh stale devices once, after the tree loads**

Add this effect after the existing agent-fetch effect. It must run only when the
device set or the access mode changes — not on every render — and it must not
fire while the agent is offline:

```js
    // The tree already renders from each device's cached result, so this is a
    // background top-up, not a load. It deliberately runs at most once per
    // device per mount, and never when the agent is offline (in agent mode the
    // job would just be refused).
    const autoRefreshedRef = useRef(new Set());
    useEffect(() => {
        if (accessMode === 'agent' && !agentOnline) return;
        tree.forEach((root) => {
            const walk = (device) => {
                if (!autoRefreshedRef.current.has(device.id)
                        && isStale(device.last_result_at)) {
                    autoRefreshedRef.current.add(device.id);
                    if (device.device_type === 'vsol_olt') refreshOlt(device);
                    else checkDevice(device);
                }
                (device.children || []).forEach(walk);
            };
            walk(root);
        });
        // refreshOlt/checkDevice are stable for a given device set.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tree, accessMode, agentOnline]);
```

**`checkDevice` does not exist yet — add it before this effect.** Model it
directly on the existing `refreshOlt`: same `refreshSeqRef` per-device sequence
guard, same `refreshingIds` in-flight map, same `errorByDevice` handling, same
quiet `loadTree(false)` resync when the job completes. The only differences are
the call — `apiService.checkNetworkDeviceNow(device.id)` instead of
`apiService.refreshOltOnus(device.id)` — and that its result is a health object
rather than an ONU list. Both still poll with `pollNetworkJob(res.data.job_id)`.

Those sequence guards exist because a superseded response overwriting a newer
one was a real bug on this page. Do not simplify them away.

- [ ] **Step 3: Show the age on each device card**

In `buildTopologyTree.js`'s `deviceNode`, the node already carries
`lastResultAt`. In `TreeNode.js`, render it under the sublabel when present:

```js
                {node.ageLabel && <div className="nt-meta">{node.ageLabel}</div>}
```

and in `NetworkTreeView.js`, decorate the built tree with a display label before
passing it down:

```js
    const topology = useMemo(() => {
        const now = Date.now();
        const decorate = (node) => ({
            ...node,
            ageLabel: node.kind === 'device' ? describeAge(node.lastResultAt, now) : undefined,
            children: (node.children || []).map(decorate),
        });
        return buildTopologyTree(tree).map(decorate);
    }, [tree]);
```

- [ ] **Step 4: Verify**

Open the page with a device whose last check is older than five minutes: the
cached tree appears immediately with "12 min ago", then updates to "just now"
once the background refresh lands. Reload within five minutes: the page renders
instantly and issues **no** new job (confirm in the backend log — no
`POST /api/network-devices/.../check-now`).

Run: `cd frontend && npx react-scripts test --watchAll=false`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/NetworkTreeView.js frontend/src/components/TreeNode.js frontend/src/components/buildTopologyTree.js
git commit -m "feat: render the tree from cache and refresh only stale devices"
```

---

## Task 7: Search that expands the path to each hit

**Files:**
- Create: `frontend/src/components/filterTopologyTree.js`
- Test: `frontend/src/components/filterTopologyTree.test.js`
- Modify: `frontend/src/components/NetworkTreeView.js`

**Interfaces:**
- Consumes: `nodeMatches` from Task 3.
- Produces: `filterTopologyTree(nodes, query) -> {nodes, expandedKeys}` — the
  pruned tree plus the set of keys that must be open for every hit to be
  visible. An empty query returns the tree unchanged and an empty set.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/filterTopologyTree.test.js`:

```js
import { filterTopologyTree } from './filterTopologyTree';

const n = (key, label, children = []) => ({
    key, label, sublabel: '', meta: '', kind: 'x', status: 'up', children,
});

const TREE = [n('ccr', 'CCR1009', [
    n('olt', 'V-SOL OLT', [
        n('pon1', 'PON1', [
            n('onu2', 'MoussaGhadir', [n('c7', 'Moussa Ghadir')]),
            n('onu5', 'OstaMarket', []),
        ]),
        n('pon3', 'PON3', [n('onu9', 'AbirKerdy', [])]),
    ]),
])];

test('an empty query returns everything and expands nothing', () => {
    const { nodes, expandedKeys } = filterTopologyTree(TREE, '');
    expect(nodes).toBe(TREE);
    expect(expandedKeys.size).toBe(0);
});

test('a match keeps its ancestors and drops unrelated branches', () => {
    const { nodes } = filterTopologyTree(TREE, 'abirkerdy');
    const olt = nodes[0].children[0];
    expect(olt.children.map((c) => c.key)).toEqual(['pon3']);
    expect(olt.children[0].children[0].label).toBe('AbirKerdy');
});

test('every ancestor of a hit is marked for expansion', () => {
    const { expandedKeys } = filterTopologyTree(TREE, 'moussa ghadir');
    expect([...expandedKeys].sort()).toEqual(['ccr', 'olt', 'onu2', 'pon1']);
});

test('a matching branch keeps its whole subtree', () => {
    const { nodes } = filterTopologyTree(TREE, 'pon1');
    const pon1 = nodes[0].children[0].children[0];
    expect(pon1.children.map((c) => c.key)).toEqual(['onu2', 'onu5']);
});

test('no match yields an empty tree, not a crash', () => {
    const { nodes, expandedKeys } = filterTopologyTree(TREE, 'nothing here');
    expect(nodes).toEqual([]);
    expect(expandedKeys.size).toBe(0);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx react-scripts test --watchAll=false src/components/filterTopologyTree.test.js`
Expected: FAIL — `Cannot find module './filterTopologyTree'`

- [ ] **Step 3: Implement**

Create `frontend/src/components/filterTopologyTree.js`:

```js
import { nodeMatches } from './buildTopologyTree';

/**
 * Prune the tree to branches containing a match, and report which nodes must
 * be expanded for every hit to be on screen.
 *
 * A node is kept when it matches, or when any descendant does. A node that
 * matches keeps its entire subtree, so searching "PON1" shows what is on PON1
 * rather than an empty branch. Returning the expansion set separately keeps
 * the caller's own manual expand/collapse state untouched -- searching does
 * not destroy what the user had open.
 */
export function filterTopologyTree(nodes, query) {
    const q = (query || '').trim();
    if (!q) return { nodes, expandedKeys: new Set() };

    const expandedKeys = new Set();

    const visit = (node) => {
        const selfMatch = nodeMatches(node, q);
        if (selfMatch) {
            // Keep the whole subtree of a matching node, untouched.
            return node;
        }
        const kept = (node.children || []).map(visit).filter(Boolean);
        if (!kept.length) return null;
        expandedKeys.add(node.key);
        return { ...node, children: kept };
    };

    const roots = (nodes || []).map((root) => {
        const kept = visit(root);
        return kept;
    }).filter(Boolean);

    // A matching node's own ancestors were added on the way down; a matching
    // node also needs to be open itself if it has children to reveal.
    const markSelf = (node) => {
        if (nodeMatches(node, q) && (node.children || []).length) expandedKeys.add(node.key);
        (node.children || []).forEach(markSelf);
    };
    roots.forEach(markSelf);

    return { nodes: roots, expandedKeys };
}
```

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx react-scripts test --watchAll=false src/components/filterTopologyTree.test.js`
Expected: PASS (5 tests)

- [ ] **Step 5: Wire the search box in**

In `NetworkTreeView.js`, add the state and input, and combine the two expansion
sources so a search reveals hits without discarding what the user opened:

```js
    const [query, setQuery] = useState('');
    const { nodes: visibleTree, expandedKeys: searchExpanded } =
        useMemo(() => filterTopologyTree(topology, query), [topology, query]);
    const effectiveExpanded = useMemo(
        () => new Set([...expanded, ...searchExpanded]), [expanded, searchExpanded]);
```

Render a `TextField` above the tree (`size="small"`, `placeholder="Search customers, ONUs, MACs…"`),
pass `visibleTree` and `effectiveExpanded` to `TreeNode`, and show
`No matches for "<query>"` when `query` is non-empty and `visibleTree` is empty.

- [ ] **Step 6: Run everything and commit**

Run: `cd frontend && npx react-scripts test --watchAll=false`
Expected: PASS

Run: `python -m pytest -q -p no:warnings`
Expected: PASS, 616 total

```bash
git add frontend/src/components/filterTopologyTree.js frontend/src/components/filterTopologyTree.test.js frontend/src/components/NetworkTreeView.js
git commit -m "feat: search the network tree and auto-expand the path to each hit"
```

---

## Self-review notes

**Spec coverage.** Every section of the spec maps to a task: structure and PON
derivation → Task 3; Ports branch → Task 3; backend `last_result` → Task 2; MAC
separators → Task 1; top-down layout and grid wrap → Task 4; motion and
reduced-motion → Task 5; cache-first rendering and the 5-minute cap → Task 6;
search → Task 7. Failure behaviour is preserved by Tasks 4 and 6 (existing
sequence guards and `errorByDevice` are explicitly kept) and by Task 3's
defensive parsing, which the tests pin.

**Known follow-ups this plan does NOT address** (both already recorded in the
spec's Risks section): the tree payload now carries a full ONU list per OLT
(~20KB), which would want pagination for a tenant with several OLTs; and the
5-minute staleness cap is an untested guess.
