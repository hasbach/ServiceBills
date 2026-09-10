# Network Geo Fibre Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a geographic map where staff trace DeltaNet's fibre run node-by-node on satellite imagery, and every span colours green or red from live ONU status, localising a fault to one span on the ground.

**Architecture:** One new self-referential table (`NetworkNode`) where each node's link to its parent *is* a span. A pure Python function turns (nodes, ONU status) into per-span colours and a single "fault boundary" — the red span whose parent is still alive, which is where the technician goes. The map page is Leaflet over keyless tile sources, reading a cache-first API that never contacts a device.

**Tech Stack:** Flask + SQLAlchemy + Alembic (models and routes in the `app.py` monolith), React 18 + MUI 5, Leaflet 1.9 (new dependency), pytest, Jest via react-scripts.

## Global Constraints

- **Branch from `origin/main`, NOT local `main`.** Local `main` is `c78dba5`, 0 ahead / 108 behind. It has no commits of its own, so `git fetch origin && git merge --ff-only origin/main` is safe. The Layer 2 agent, Network Tree v2 and CPE-MAC linking exist only on `origin/main`.
- **Spec:** `docs/superpowers/specs/2026-09-10-network-geo-fiber-map-design.md`. Read it before Task 1.
- **Scope:** this plan covers the map only. Optical logging and the deregister-reason scrape are specified in the same spec but get their own plan — see "Deliberately not in this plan".
- **Read endpoints use `network_view_required()`** (admin, finance, employee, collector). **Every write uses `admin_or_finance_required()`.** Tree v2 shipped a defect where a page granted to employee/collector called an admin-only endpoint on mount and showed those roles a permanent red error. The map page must render without calling any admin-only endpoint.
- **Models live in `app.py`.** Do not create a models package. Connectors live at repo root beside `mikrotik.py` / `vsol_olt.py`.
- **`apiService` is a DIRECT named export of `AppContext.js`**, not part of `useAppContext()`'s value. Only `setSnackbar` comes from the hook.
- **Tests build schema with `db.create_all()`, never migrations.** SQLite FK enforcement is OFF in tests (no pragma listener), so a foreign-key mistake will not surface there — Postgres is where it bites.
- **MAC handling:** store with `_canonical_mac(raw)` (app.py:3066, colon form, rejects anything not 12 hex digits); compare with `_normalize_mac(mac)` (app.py:9635). Never compare raw strings — the validator accepts `:`, `-` and `.` separators.
- **Jest in a worktree:** a dot-prefixed path segment corrupts CRA's default `testMatch` and `react-scripts test` reports "No tests found" for every file, which looks identical to passing. Always run `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/<file>.test.js"`.
- **Builds:** `CI=true npx react-scripts build` fails on ~30 pre-existing warnings in unrelated files. The Dockerfile runs it without `CI`. Use the bare `npx react-scripts build`.

## File Structure

| File | Responsibility |
|---|---|
| `app.py` (modify) | `NetworkNode` model; `_compute_map_status`; `_map_distance_warnings`; five `/api/network-map*` routes; registration in `TENANT_OWNED_MODELS` and `_TENANT_DELETE_ORDER` |
| `migrations/versions/<rev>_add_network_node.py` (create) | The one migration for this plan |
| `tests/test_network_map_status.py` (create) | The status algorithm — pure, no HTTP, no device |
| `tests/test_network_map_api.py` (create) | Route behaviour, invariants, authz |
| `tests/test_network_map_distance.py` (create) | Gross-placement sanity check |
| `frontend/src/components/NetworkMapView.js` (create) | The Leaflet page: layers, markers, polylines, placement UX |
| `frontend/src/components/networkMap.css` (create) | Fault-boundary animation, marker and control styling |
| `frontend/src/components/NetworkMapView.test.js` (create) | Component tests (needs `@testing-library/react`, installed in Task 8) |

**Why the algorithm is Python, not JavaScript.** The existing network pages assemble on the frontend (`buildTopologyTree.js`, `mergeNetworkStatus.js`). This plan deliberately diverges: the declared next slice is alerting, which must decide server-side which spans are red in order to send a push notification. Putting the algorithm in Python makes it reusable by that job instead of duplicating it in two languages, where the copies would drift. The frontend receives per-span status and only renders.

---

### Task 1: `NetworkNode` model and migration

**Files:**
- Modify: `app.py` — add model after `NetworkDevice` (near line 549 on origin/main); register in `TENANT_OWNED_MODELS` (line 1564) and `_TENANT_DELETE_ORDER` (line 2382)
- Create: `migrations/versions/<rev>_add_network_node.py`
- Test: `tests/test_network_map_api.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `NetworkNode` with fields `id, tenant_id, olt_device_id, kind, label, latitude, longitude, parent_node_id, onu_mac, last_dereg_reason, last_dereg_reason_at, created_at, updated_at`; `NetworkNode.to_dict()` returning `latitude`/`longitude` as Python floats; module constant `NODE_KINDS = ('root', 'junction', 'onu')`.

- [ ] **Step 1: Write the failing test**

In `tests/test_network_map_api.py`:

```python
import app as appmod
from tests.conftest import make_tenant, auth_headers


def test_network_node_is_tenant_owned_and_in_delete_order():
    assert appmod.NetworkNode in appmod.TENANT_OWNED_MODELS
    assert appmod.NetworkNode in appmod._TENANT_DELETE_ORDER


def test_node_kinds_are_exactly_the_three_documented_values():
    assert appmod.NODE_KINDS == ('root', 'junction', 'onu')


def test_to_dict_returns_coordinates_as_floats(app):
    with app.app_context():
        node = appmod.NetworkNode(
            tenant_id=1, olt_device_id=1, kind='root', label='Control Room',
            latitude=34.436700, longitude=35.849700)
        data = node.to_dict()
        assert isinstance(data['latitude'], float)
        assert isinstance(data['longitude'], float)
        assert data['latitude'] == 34.4367
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_network_map_api.py -v`
Expected: FAIL with `AttributeError: module 'app' has no attribute 'NetworkNode'`

- [ ] **Step 3: Write minimal implementation**

In `app.py`, immediately after the `NetworkDevice` class:

```python
NODE_KINDS = ('root', 'junction', 'onu')


class NetworkNode(db.Model):
    """One point on the geographic fibre map. Each node's link to its parent
    IS a span -- fibre is a tree, so a node has exactly one parent and no
    separate span table is needed. Mirrors NetworkDevice.parent_device_id.

    See docs/superpowers/specs/2026-09-10-network-geo-fiber-map-design.md."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    olt_device_id = db.Column(db.Integer, db.ForeignKey('network_device.id'), nullable=False)
    kind = db.Column(db.String(10), nullable=False)          # one of NODE_KINDS
    label = db.Column(db.String(100), nullable=False)
    # Numeric, not Float: ~11cm of precision with no binary-float drift across
    # the JSON round-trip a drag-to-reposition performs on every save.
    latitude = db.Column(db.Numeric(9, 6), nullable=False)
    longitude = db.Column(db.Numeric(9, 6), nullable=False)
    parent_node_id = db.Column(db.Integer, db.ForeignKey('network_node.id'), nullable=True)
    # Colon-form MAC (see _canonical_mac). Set only when kind == 'onu'; this is
    # what binds a placed point to a live ONU in the OLT's walk. Bound by MAC
    # rather than (pon, onu_id) because a MAC survives the ONU being moved
    # between PON ports, and it is the key Customer.onu_mac_address uses.
    onu_mac = db.Column(db.String(20), nullable=True, index=True)
    # Volatile scraped state, overwritten each fetch, never appended to.
    # DISPLAY ONLY -- _compute_map_status must not read these. Populated by a
    # later plan; the columns exist now so the map's one migration is the only
    # one this feature needs.
    last_dereg_reason = db.Column(db.String(20), nullable=True)
    # When WE read it, deliberately not the OLT's own timestamp: the OLT has no
    # real clock and reports uptime-relative times like 1970/01/30 17:52:34.
    last_dereg_reason_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'olt_device_id': self.olt_device_id,
            'kind': self.kind,
            'label': self.label,
            # float(), not str(): Numeric round-trips as Decimal, which is not
            # JSON-serialisable and would 500 the whole map endpoint.
            'latitude': float(self.latitude),
            'longitude': float(self.longitude),
            'parent_node_id': self.parent_node_id,
            'onu_mac': self.onu_mac,
            'last_dereg_reason': self.last_dereg_reason,
        }
```

Add `NetworkNode` to `TENANT_OWNED_MODELS`. In `_TENANT_DELETE_ORDER`, place `NetworkNode` **before** `NetworkDevice` — nodes hold an FK to `network_device.id`, so deleting devices first raises `ForeignKeyViolation` on Postgres and passes silently on SQLite.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_network_map_api.py -v`
Expected: 3 passed

- [ ] **Step 5: Generate and inspect the migration**

Run: `python -m flask db migrate -m "add network_node"`

Open the generated file. Assert by reading: it creates `network_node` with a self-referential FK, and it contains **no** unrelated `ADD`/`DROP` on existing tables. Production's real schema disagrees with migration history in both directions, so an autogenerated migration routinely contains drift that must be deleted by hand. Delete anything not about `network_node`.

Confirm there is exactly one Alembic head — `flask db upgrade` runs on container start via the Dockerfile CMD, so a split head fails the **deploy**, not just a command:

Run: `python -m flask db heads`
Expected: exactly one revision printed.

- [ ] **Step 6: Verify the migration applies and reverses**

Run: `python -m flask db upgrade && python -m flask db downgrade && python -m flask db upgrade`
Expected: no error. (SQLite note: if `downgrade` fails on a constraint, wrap the offending operation in `op.batch_alter_table` — migration `bd054e2e7cf9` on `origin/main` has this exact pre-existing bug with `create_unique_constraint` outside batch mode.)

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q`
Expected: all pass, including `tests/test_lifecycle.py::test_tenant_owned_models_all_in_delete_order` — the test that caught `NetworkDevice` silently falling out of the delete order during Tree v2's merge.

- [ ] **Step 8: Commit**

```bash
git add app.py migrations/versions tests/test_network_map_api.py
git commit -m "feat: add NetworkNode model for the geographic fibre map"
```

---

### Task 2: The status algorithm

The heart of the feature. Pure function, no HTTP, no device, no database.

**Files:**
- Modify: `app.py` — add `_compute_map_status` near `_build_device_tree` (line 9505 on origin/main)
- Test: `tests/test_network_map_status.py`

**Interfaces:**
- Consumes: `NetworkNode` from Task 1; `_normalize_mac` (app.py:9635).
- Produces: `_compute_map_status(nodes, onu_status) -> dict` with exactly three keys:
  - `spans`: list of `{'parent_node_id': int, 'child_node_id': int, 'status': 'green'|'red'|'grey', 'is_fault_boundary': bool}`, ordered by `child_node_id` ascending
  - `node_status`: `{node_id: 'online'|'offline'|'unknown'}`
  - `orphans`: sorted list of node ids unreachable from any root

  `onu_status` is `{normalised_mac: 'online'|'offline'}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_network_map_status.py`:

```python
"""The status algorithm, tested as the pure function it is. No app context, no
database, no device -- these use a tiny stand-in with the three attributes the
algorithm actually reads, so a schema change cannot silently break these tests
into vacuous passes."""
import app as appmod


class FakeNode:
    def __init__(self, id, kind, parent_node_id=None, onu_mac=None):
        self.id = id
        self.kind = kind
        self.parent_node_id = parent_node_id
        self.onu_mac = onu_mac


def chain(*macs):
    """root -> onu(mac[0]) -> onu(mac[1]) -> ... as a single line."""
    nodes = [FakeNode(1, 'root')]
    for i, mac in enumerate(macs, start=2):
        nodes.append(FakeNode(i, 'onu', parent_node_id=i - 1, onu_mac=mac))
    return nodes


def span_for(result, child_id):
    return next(s for s in result['spans'] if s['child_node_id'] == child_id)


A, B, C = 'aa:aa:aa:aa:aa:aa', 'bb:bb:bb:bb:bb:bb', 'cc:cc:cc:cc:cc:cc'


def test_all_online_is_all_green():
    nodes = chain(A, B, C)
    r = appmod._compute_map_status(
        nodes, {A: 'online', B: 'online', C: 'online'})
    assert [s['status'] for s in r['spans']] == ['green', 'green', 'green']
    assert not any(s['is_fault_boundary'] for s in r['spans'])


def test_offline_node_with_live_downstream_keeps_the_trunk_green():
    """The owner's case 1: C being up proves the fibre through B is intact, so
    the fault is LOCAL to B -- its own drop or its power -- not a cut span."""
    nodes = chain(A, B, C)
    r = appmod._compute_map_status(
        nodes, {A: 'online', B: 'offline', C: 'online'})
    assert span_for(r, 3)['status'] == 'green'      # root -> B stays green
    assert r['node_status'][3] == 'offline'         # B's own dot is red
    assert not any(s['is_fault_boundary'] for s in r['spans'])


def test_dead_downstream_marks_the_cut_span_as_the_fault_boundary():
    """The owner's case 2: nothing downstream survives, so the span into B is
    cut. B->C is also red but is consequence, not cause."""
    nodes = chain(A, B, C)
    r = appmod._compute_map_status(
        nodes, {A: 'online', B: 'offline', C: 'offline'})
    assert span_for(r, 3)['status'] == 'red'
    assert span_for(r, 4)['status'] == 'red'
    assert span_for(r, 3)['is_fault_boundary'] is True
    assert span_for(r, 4)['is_fault_boundary'] is False


def test_branch_point_with_one_live_and_one_dead_child():
    """A junction is alive if ANY child subtree is alive, so the span to the
    dead child becomes the boundary with no special-casing."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'junction', parent_node_id=1),
             FakeNode(3, 'onu', parent_node_id=2, onu_mac=A),
             FakeNode(4, 'onu', parent_node_id=2, onu_mac=B)]
    r = appmod._compute_map_status(nodes, {A: 'online', B: 'offline'})
    assert span_for(r, 2)['status'] == 'green'
    assert span_for(r, 3)['status'] == 'green'
    assert span_for(r, 4)['status'] == 'red'
    assert span_for(r, 4)['is_fault_boundary'] is True
    assert r['node_status'][2] == 'online'


def test_entirely_dead_tree_yields_no_boundary():
    """Nothing is alive, so no red span has a live parent. The fault is the OLT
    or upstream of it, which the caller handles as its own case -- the absence
    of a boundary must not be mistaken for the absence of a fault."""
    nodes = chain(A, B)
    r = appmod._compute_map_status(nodes, {A: 'offline', B: 'offline'})
    assert all(s['status'] == 'red' for s in r['spans'])
    assert not any(s['is_fault_boundary'] for s in r['spans'])


def test_unknown_mac_is_grey_not_red():
    """A placed ONU the OLT has never reported is unknown, not down. Colouring
    it red would invent an outage."""
    nodes = chain(A)
    r = appmod._compute_map_status(nodes, {})
    assert span_for(r, 2)['status'] == 'grey'
    assert r['node_status'][2] == 'unknown'


def test_a_junction_with_no_known_onu_below_it_is_grey():
    nodes = [FakeNode(1, 'root'), FakeNode(2, 'junction', parent_node_id=1)]
    r = appmod._compute_map_status(nodes, {})
    assert span_for(r, 2)['status'] == 'grey'


def test_mac_join_is_separator_insensitive():
    """The OLT emits colons; a staff-entered MAC may use hyphens or dots."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac='AA-AA-AA-AA-AA-AA')]
    r = appmod._compute_map_status(
        nodes, {appmod._normalize_mac(A): 'online'})
    assert r['node_status'][2] == 'online'


def test_a_cycle_is_reported_as_orphans_not_silently_dropped():
    """Tree v2 shipped a builder that silently dropped whole cyclic
    components. A cycle here must be surfaced, and must not crash."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=3, onu_mac=A),
             FakeNode(3, 'onu', parent_node_id=2, onu_mac=B)]
    r = appmod._compute_map_status(nodes, {A: 'online', B: 'online'})
    assert r['orphans'] == [2, 3]
    assert all(s['child_node_id'] == 1 or s['child_node_id'] not in (2, 3)
               for s in r['spans'])


def test_a_node_parented_to_itself_is_treated_as_a_root():
    nodes = [FakeNode(1, 'root'), FakeNode(2, 'junction', parent_node_id=2)]
    r = appmod._compute_map_status(nodes, {})
    assert r['orphans'] == []


def test_empty_input_is_not_an_error():
    r = appmod._compute_map_status([], {})
    assert r == {'spans': [], 'node_status': {}, 'orphans': []}


def test_output_is_deterministic_across_input_ordering():
    """Dict and set iteration order must not leak into the response."""
    nodes = chain(A, B, C)
    status = {A: 'online', B: 'offline', C: 'offline'}
    first = appmod._compute_map_status(nodes, status)
    second = appmod._compute_map_status(list(reversed(nodes)), status)
    assert first == second
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_network_map_status.py -v`
Expected: every test FAILS with `AttributeError: module 'app' has no attribute '_compute_map_status'`

- [ ] **Step 3: Write the implementation**

In `app.py`, near `_build_device_tree`:

```python
def _compute_map_status(nodes, onu_status):
    """Colour every span on the geographic map from live ONU status.

    One rule, evaluated bottom-up:

        a node's subtree is ALIVE if it contains at least one online ONU,
        itself included;
        a node's subtree is KNOWN if it contains at least one ONU whose MAC
        appears in the OLT's data at all, online or offline.

    From which: a span is green if its child's subtree is alive, red if the
    subtree is known and not alive, and grey if nothing below it is known.

    The FAULT BOUNDARY is a red span whose parent node is still alive. That is
    the whole point of the feature: everything red below the boundary is
    consequence, not cause, so the boundary is the one span worth driving to.

    Deliberately does NOT read last_dereg_reason. Colour must depend only on
    status, because the scraped reason describes the *last* disconnection
    rather than the current one, is 'N/A' on at least some offline ONUs, and
    comes from a scrape that can fail -- any of which would turn a correct map
    into a wrong one if it were allowed to decide colour.

    Cycles are surfaced as `orphans`, never dropped: Tree v2 shipped a builder
    that silently discarded whole cyclic components, so a mis-parented run
    vanished from the page with no indication anything was missing.
    """
    by_id = {node.id: node for node in nodes}
    children_of = {}
    roots = []
    # Ascending id throughout, so set/dict iteration order cannot leak into
    # the response and make the payload differ between two identical calls.
    ordered = sorted(nodes, key=lambda n: n.id)
    for node in ordered:
        parent_id = node.parent_node_id
        if parent_id is None or parent_id == node.id or parent_id not in by_id:
            roots.append(node)
        else:
            children_of.setdefault(parent_id, []).append(node)

    visited, alive, known = set(), {}, {}

    def walk(node):
        visited.add(node.id)
        own = None
        if node.kind == 'onu' and node.onu_mac:
            own = onu_status.get(_normalize_mac(node.onu_mac))
        node_alive = own == 'online'
        node_known = own is not None
        for child in sorted(children_of.get(node.id, []), key=lambda n: n.id):
            if child.id in visited:
                continue
            walk(child)
            node_alive = node_alive or alive[child.id]
            node_known = node_known or known[child.id]
        alive[node.id] = node_alive
        known[node.id] = node_known

    for root in roots:
        if root.id not in visited:
            walk(root)

    spans = []
    for node in ordered:
        parent_id = node.parent_node_id
        if (parent_id is None or parent_id == node.id
                or parent_id not in by_id or node.id not in alive):
            continue
        if alive[node.id]:
            status = 'green'
        elif known[node.id]:
            status = 'red'
        else:
            status = 'grey'
        spans.append({
            'parent_node_id': parent_id,
            'child_node_id': node.id,
            'status': status,
            'is_fault_boundary': status == 'red' and alive.get(parent_id, False),
        })

    node_status = {}
    for node in ordered:
        if node.id not in alive:
            node_status[node.id] = 'unknown'      # unreachable: a cycle member
        elif node.kind == 'onu':
            own = onu_status.get(_normalize_mac(node.onu_mac)) if node.onu_mac else None
            node_status[node.id] = own or 'unknown'
        elif alive[node.id]:
            node_status[node.id] = 'online'
        elif known[node.id]:
            node_status[node.id] = 'offline'
        else:
            node_status[node.id] = 'unknown'

    return {
        'spans': spans,
        'node_status': node_status,
        'orphans': sorted(n.id for n in nodes if n.id not in visited),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_network_map_status.py -v`
Expected: 12 passed

- [ ] **Step 5: Prove the tests have teeth**

Mutate the implementation one change at a time and confirm a test fails each time. A test suite that survives these is not testing the rule. During the Tree v2 build a review found a four-term tie-break where three terms could each be deleted with the suite still green — this step exists to catch that class of hole before a reviewer does.

1. Change `status == 'red' and alive.get(parent_id, False)` to `status == 'red'` → `test_dead_downstream_marks_the_cut_span_as_the_fault_boundary` must fail on the `4` assertion.
2. Change `elif known[node.id]: status = 'red'` to `else: status = 'red'` → `test_unknown_mac_is_grey_not_red` must fail.
3. Change `node_alive or alive[child.id]` to just `node_alive` → `test_offline_node_with_live_downstream_keeps_the_trunk_green` must fail.
4. Remove `_normalize_mac` from the lookup → `test_mac_join_is_separator_insensitive` must fail.

Revert every mutation afterwards. Run `git diff` and confirm it is empty before committing.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_network_map_status.py
git commit -m "feat: add span-colouring algorithm with fault-boundary detection"
```

---

### Task 3: Read endpoints

**Files:**
- Modify: `app.py` — add routes after the existing `/api/network-tree` block (line 10469 on origin/main)
- Test: `tests/test_network_map_api.py`

**Interfaces:**
- Consumes: `NetworkNode` (Task 1); `_compute_map_status` (Task 2); `_latest_results_by_device(devices)` (app.py:9400); `_resolve_onu_customers(onus)` (app.py:9654); `vsol_olt._dedupe_by_mac`; `network_view_required()` (app.py:1731).
- Produces: `GET /api/network-map?olt_device_id=<id>` → `{nodes, spans, node_status, orphans, onu_status, last_result_at, distance_warnings}`; `GET /api/network-map/unplaced-onus?olt_device_id=<id>` → `{onus: [...]}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_network_map_api.py`:

```python
def _olt(client, headers):
    """Create a vsol_olt device and return its id."""
    r = client.post('/api/network-devices', headers=headers, json={
        'name': 'OLT', 'host': '192.168.8.100', 'api_port': 161,
        'username': '', 'password': 'public', 'device_type': 'vsol_olt'})
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()['id']


def test_map_read_is_allowed_for_employee(app, client):
    """The map must render for the field-facing roles, and must NOT call any
    admin-only endpoint to do it -- Tree v2 shipped exactly that defect."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    emp = auth_headers(client, 'emp', 'pw', role='employee')
    r = client.get(f'/api/network-map?olt_device_id={olt}', headers=emp)
    assert r.status_code == 200
    body = r.get_json()
    assert body['nodes'] == []
    assert body['spans'] == []
    assert body['orphans'] == []


def test_map_read_rejects_a_role_outside_network_view(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    other = auth_headers(client, 'nobody', 'pw', role='customer')
    r = client.get(f'/api/network-map?olt_device_id={olt}', headers=other)
    assert r.status_code == 403


def test_map_read_404s_for_an_unknown_device(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    assert client.get('/api/network-map?olt_device_id=99999',
                      headers=admin).status_code == 404


def test_map_read_rejects_a_device_that_is_not_an_olt(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    r = client.post('/api/network-devices', headers=admin, json={
        'name': 'CCR', 'host': '192.168.100.1', 'api_port': 8728,
        'username': 'admin', 'password': 'x', 'device_type': 'mikrotik_ccr'})
    ccr = r.get_json()['id']
    assert client.get(f'/api/network-map?olt_device_id={ccr}',
                      headers=admin).status_code == 400


def test_a_failed_walk_must_not_blank_the_map(app, client):
    """A failed connector run is stored as status='done', result=None, error
    set -- in BOTH direct and agent mode. Tree v2 shipped a defect where that
    row became 'the cached result', blanking the tree AND advancing its
    freshness stamp so it never retried. Symptom on this page would be worse:
    a map claiming the whole town is offline. The failed job must be ignored
    and the last good result kept.

    Note this fabricates job rows the way the connector really writes them --
    status='done', NOT status='failed'. An existing Tree v2 test hand-set
    'failed', a state the connector path never produces, and therefore proved
    nothing."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    good = [{'mac_address': 'aa:aa:aa:aa:aa:aa', 'status': 'online',
             'pon_port': 1, 'onu_id': 1, 'description': 'villaEid',
             'distance': 856}]
    with app.app_context():
        appmod.db.session.add(appmod.NetworkAgentJob(
            tenant_id=1, device_id=olt, operation='olt_status',
            status='done', result=good, error=None))
        appmod.db.session.commit()
        appmod.db.session.add(appmod.NetworkAgentJob(
            tenant_id=1, device_id=olt, operation='olt_status',
            status='done', result=None, error='SNMP timeout'))
        appmod.db.session.commit()
    body = client.get(f'/api/network-map?olt_device_id={olt}',
                      headers=admin).get_json()
    assert body['onu_status'] == {'aaaaaaaaaaaa': 'online'}
    assert body['last_result_at'] is not None


def test_unplaced_onus_excludes_already_placed_ones(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    onus = [{'mac_address': 'aa:aa:aa:aa:aa:aa', 'status': 'online',
             'pon_port': 1, 'onu_id': 1, 'description': 'villaEid', 'distance': 856},
            {'mac_address': 'bb:bb:bb:bb:bb:bb', 'status': 'offline',
             'pon_port': 1, 'onu_id': 2, 'description': 'Lions', 'distance': 0}]
    with app.app_context():
        appmod.db.session.add(appmod.NetworkAgentJob(
            tenant_id=1, device_id=olt, operation='olt_status',
            status='done', result=onus, error=None))
        appmod.db.session.add(appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt, kind='root', label='CR',
            latitude=34.4367, longitude=35.8497))
        appmod.db.session.commit()
        appmod.db.session.add(appmod.NetworkNode(
            tenant_id=1, olt_device_id=olt, kind='onu', label='Villa Eid',
            latitude=34.4368, longitude=35.8498, parent_node_id=1,
            onu_mac='aa:aa:aa:aa:aa:aa'))
        appmod.db.session.commit()
    body = client.get(f'/api/network-map/unplaced-onus?olt_device_id={olt}',
                      headers=admin).get_json()
    assert [o['mac_address'] for o in body['onus']] == ['bb:bb:bb:bb:bb:bb']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_network_map_api.py -v`
Expected: the six new tests FAIL with 404 (route not registered)

- [ ] **Step 3: Write the implementation**

In `app.py`:

```python
def _map_onu_status(device):
    """{normalised_mac: 'online'|'offline'} from the OLT's newest genuinely
    successful walk, plus when that walk happened.

    Reuses _latest_results_by_device rather than re-querying, because that
    helper already encodes the filter this page depends on: status == 'done'
    AND error IS NULL AND result IS NOT NULL AND operation == 'olt_status'.
    A failed run is also stored as 'done', so without that filter the newest
    job after an outage would have result=None and this map would report every
    ONU as unknown while advancing its own freshness stamp.

    Contacts nothing. The map is cache-first by design: in direct mode a walk
    blocks the single sync gunicorn worker ~13s, and this page is granted to
    field roles who may open it repeatedly.
    """
    latest = _latest_results_by_device([device]).get(device.id) or {}
    rows = latest.get('result') or []
    status = {}
    for row in rows:
        mac = row.get('mac_address')
        if not mac:
            continue
        status[_normalize_mac(mac)] = (
            'online' if row.get('status') == 'online' else 'offline')
    return status, latest.get('at'), rows


def _require_olt(device_id):
    """(device, error_response). Mirrors the existing refresh endpoint's
    checks so the map cannot be pointed at a CCR."""
    device = tenant_query(NetworkDevice).filter_by(id=device_id).first()
    if not device:
        return None, (jsonify({'message': 'Network device not found!'}), 404)
    if device.device_type != 'vsol_olt':
        return None, (jsonify({'error': 'That device is not an OLT'}), 400)
    return device, None


@app.route('/api/network-map', methods=['GET'])
@jwt_required()
@network_view_required()
def get_network_map():
    device_id = request.args.get('olt_device_id', type=int)
    device, err = _require_olt(device_id)
    if err:
        return err
    nodes = (tenant_query(NetworkNode)
             .filter_by(olt_device_id=device.id)
             .order_by(NetworkNode.id).all())
    onu_status, last_result_at, rows = _map_onu_status(device)
    computed = _compute_map_status(nodes, onu_status)
    return jsonify({
        'nodes': [n.to_dict() for n in nodes],
        'onu_status': onu_status,
        'last_result_at': last_result_at,
        'distance_warnings': _map_distance_warnings(nodes, rows),
        **computed,
    }), 200


@app.route('/api/network-map/unplaced-onus', methods=['GET'])
@jwt_required()
@network_view_required()
def get_unplaced_onus():
    device_id = request.args.get('olt_device_id', type=int)
    device, err = _require_olt(device_id)
    if err:
        return err
    _, _, rows = _map_onu_status(device)
    placed = {
        _normalize_mac(mac) for (mac,) in
        db.session.query(NetworkNode.onu_mac)
        .filter(NetworkNode.tenant_id == current_tenant_id(),
                NetworkNode.olt_device_id == device.id,
                NetworkNode.onu_mac.isnot(None)).all()
    }
    unplaced = [row for row in _resolve_onu_customers(rows)
                if _normalize_mac(row.get('mac_address') or '') not in placed]
    return jsonify({'onus': unplaced}), 200
```

`current_tenant_id()`, `tenant_query()` and `new_for_tenant(model, **kwargs)` all come from `tenancy.py` and are already imported in `app.py`. There is no underscore-prefixed variant of any of them — do not invent one.

`_map_distance_warnings` is written in Task 5. Until then, stub it as `return []` in **this** step so the endpoint imports, and Task 5 replaces the stub with the real implementation and its tests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_network_map_api.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_network_map_api.py
git commit -m "feat: add cache-first network map read endpoints"
```

---

### Task 4: Node write endpoints and invariants

**Files:**
- Modify: `app.py`
- Test: `tests/test_network_map_api.py`

**Interfaces:**
- Consumes: Task 1 and Task 3.
- Produces: `POST /api/network-map/nodes`, `PUT /api/network-map/nodes/<id>`, `DELETE /api/network-map/nodes/<id>`, all `admin_or_finance_required()`; helper `_validate_node_payload(payload, device_id, node_id=None) -> (cleaned_dict, error_response_or_None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_network_map_api.py`:

```python
def _node(client, headers, olt, **kw):
    payload = {'olt_device_id': olt, 'kind': 'junction', 'label': 'P',
               'latitude': 34.4367, 'longitude': 35.8497}
    payload.update(kw)
    return client.post('/api/network-map/nodes', headers=headers, json=payload)


def test_writes_are_refused_to_employee(app, client):
    """Read is widened to field roles; placing nodes is not. Widening the page
    must not quietly widen who can rewrite the customer-to-ONU mapping."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    emp = auth_headers(client, 'emp', 'pw', role='employee')
    assert _node(client, emp, olt, kind='root', label='CR').status_code == 403


def test_root_must_have_no_parent_and_be_unique_per_olt(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    first = _node(client, admin, olt, kind='root', label='CR')
    assert first.status_code == 201
    assert _node(client, admin, olt, kind='root', label='CR2').status_code == 400
    root_id = first.get_json()['id']
    bad = _node(client, admin, olt, kind='root', label='CR3',
                parent_node_id=root_id)
    assert bad.status_code == 400


def test_a_non_root_requires_a_parent(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    assert _node(client, admin, olt, kind='junction').status_code == 400


def test_mac_is_required_on_onu_and_forbidden_elsewhere(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    assert _node(client, admin, olt, kind='onu', parent_node_id=root
                 ).status_code == 400
    assert _node(client, admin, olt, kind='junction', parent_node_id=root,
                 onu_mac='aa:aa:aa:aa:aa:aa').status_code == 400
    ok = _node(client, admin, olt, kind='onu', parent_node_id=root,
               onu_mac='AA-AA-AA-AA-AA-AA')
    assert ok.status_code == 201
    assert ok.get_json()['onu_mac'] == 'aa:aa:aa:aa:aa:aa'   # canonicalised


def test_one_onu_cannot_be_placed_twice(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    _node(client, admin, olt, kind='onu', parent_node_id=root,
          onu_mac='aa:aa:aa:aa:aa:aa')
    dup = _node(client, admin, olt, kind='onu', parent_node_id=root,
                onu_mac='aa:aa:aa:aa:aa:aa')
    assert dup.status_code == 400


def test_a_malformed_mac_is_rejected(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    assert _node(client, admin, olt, kind='onu', parent_node_id=root,
                 onu_mac='not-a-mac').status_code == 400


def test_out_of_range_coordinates_are_rejected(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    assert _node(client, admin, olt, kind='root', label='CR',
                 latitude=91.0).status_code == 400
    assert _node(client, admin, olt, kind='root', label='CR',
                 longitude=-181.0).status_code == 400


def test_a_node_cannot_be_reparented_under_its_own_descendant(app, client):
    """The cycle guard. _compute_map_status defends on read, but a cycle must
    not be creatable in the first place."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    mid = _node(client, admin, olt, kind='junction', label='M',
                parent_node_id=root).get_json()['id']
    leaf = _node(client, admin, olt, kind='junction', label='L',
                 parent_node_id=mid).get_json()['id']
    r = client.put(f'/api/network-map/nodes/{mid}', headers=admin,
                   json={'parent_node_id': leaf})
    assert r.status_code == 400
    self_parent = client.put(f'/api/network-map/nodes/{mid}', headers=admin,
                             json={'parent_node_id': mid})
    assert self_parent.status_code == 400


def test_deleting_a_node_with_children_is_refused_with_409(app, client):
    """Naming the children matters: re-tracing a run by hand is expensive, so
    an accidental delete must be both blocked and explained."""
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    root = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    child = _node(client, admin, olt, kind='junction', label='Pole 4',
                  parent_node_id=root).get_json()['id']
    r = client.delete(f'/api/network-map/nodes/{root}', headers=admin)
    assert r.status_code == 409
    assert 'Pole 4' in r.get_json()['message']
    assert client.delete(f'/api/network-map/nodes/{child}',
                         headers=admin).status_code == 200


def test_a_node_from_another_tenant_is_invisible(app, client):
    make_tenant(client, 'DeltaNet', 'admin')
    admin = auth_headers(client, 'admin', 'pw', role='admin')
    olt = _olt(client, admin)
    node = _node(client, admin, olt, kind='root', label='CR').get_json()['id']
    make_tenant(client, 'Other ISP', 'admin2')
    other = auth_headers(client, 'admin2', 'pw', role='admin')
    assert client.put(f'/api/network-map/nodes/{node}', headers=other,
                      json={'label': 'hijacked'}).status_code == 404
    assert client.delete(f'/api/network-map/nodes/{node}',
                         headers=other).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_network_map_api.py -v`
Expected: the eleven new tests FAIL with 404/405

- [ ] **Step 3: Write the implementation**

In `app.py`:

```python
def _node_descendant_ids(node_id, device_id):
    """Every id at or below node_id, so a reparent cannot build a cycle.
    Iterative with a seen-set: a cycle already in the data would make the
    recursive form loop forever, and this helper must be usable to repair one."""
    seen, frontier = {node_id}, [node_id]
    while frontier:
        rows = (db.session.query(NetworkNode.id)
                .filter(NetworkNode.tenant_id == current_tenant_id(),
                        NetworkNode.olt_device_id == device_id,
                        NetworkNode.parent_node_id.in_(frontier)).all())
        frontier = [r.id for r in rows if r.id not in seen]
        seen.update(frontier)
    return seen


def _validate_node_payload(payload, device_id, node=None):
    """(cleaned, error). `node` is the row being updated, or None on create."""
    kind = payload.get('kind', node.kind if node else None)
    if kind not in NODE_KINDS:
        return None, (jsonify({'message': f'kind must be one of {NODE_KINDS}'}), 400)

    cleaned = {'kind': kind}

    label = payload.get('label', node.label if node else None)
    if not (label or '').strip():
        return None, (jsonify({'message': 'label is required'}), 400)
    cleaned['label'] = label.strip()[:100]

    for field, limit in (('latitude', 90.0), ('longitude', 180.0)):
        if field in payload or node is None:
            try:
                value = float(payload[field])
            except (KeyError, TypeError, ValueError):
                return None, (jsonify({'message': f'{field} must be a number'}), 400)
            if not -limit <= value <= limit:
                return None, (jsonify(
                    {'message': f'{field} must be between -{limit} and {limit}'}), 400)
            cleaned[field] = value

    # Parent: root has none, everything else must have one.
    parent_id = payload.get('parent_node_id',
                            node.parent_node_id if node else None)
    if kind == 'root':
        if parent_id is not None:
            return None, (jsonify({'message': 'a root node cannot have a parent'}), 400)
        existing_root = (tenant_query(NetworkNode)
                         .filter_by(olt_device_id=device_id, kind='root')
                         .filter(NetworkNode.id != (node.id if node else -1))
                         .first())
        if existing_root:
            return None, (jsonify(
                {'message': 'this OLT already has a root node'}), 400)
    else:
        if parent_id is None:
            return None, (jsonify(
                {'message': 'a non-root node requires a parent'}), 400)
        parent = (tenant_query(NetworkNode)
                  .filter_by(id=parent_id, olt_device_id=device_id).first())
        if not parent:
            return None, (jsonify({'message': 'parent node not found'}), 400)
        if node is not None and parent_id in _node_descendant_ids(node.id, device_id):
            return None, (jsonify(
                {'message': 'cannot reparent a node under itself or its own '
                            'descendant -- that would create a cycle'}), 400)
    cleaned['parent_node_id'] = parent_id

    # MAC: required on an ONU, forbidden otherwise, unique per OLT.
    raw_mac = payload.get('onu_mac', node.onu_mac if node else None)
    if kind == 'onu':
        if not raw_mac:
            return None, (jsonify(
                {'message': 'an ONU node requires onu_mac'}), 400)
        mac = _canonical_mac(raw_mac)
        if not mac:
            return None, (jsonify({'message': 'onu_mac is not a valid MAC'}), 400)
        clash = (tenant_query(NetworkNode)
                 .filter_by(olt_device_id=device_id, onu_mac=mac)
                 .filter(NetworkNode.id != (node.id if node else -1)).first())
        if clash:
            return None, (jsonify(
                {'message': f'that ONU is already placed as "{clash.label}"'}), 400)
        cleaned['onu_mac'] = mac
    else:
        if raw_mac:
            return None, (jsonify(
                {'message': f'a {kind} node cannot carry onu_mac'}), 400)
        cleaned['onu_mac'] = None

    return cleaned, None


@app.route('/api/network-map/nodes', methods=['POST'])
@jwt_required()
@admin_or_finance_required()
def create_network_node():
    payload = request.json or {}
    device, err = _require_olt(payload.get('olt_device_id'))
    if err:
        return err
    cleaned, err = _validate_node_payload(payload, device.id, node=None)
    if err:
        return err
    node = new_for_tenant(NetworkNode, olt_device_id=device.id, **cleaned)
    db.session.add(node)
    db.session.commit()
    return jsonify(node.to_dict()), 201


@app.route('/api/network-map/nodes/<int:node_id>', methods=['PUT'])
@jwt_required()
@admin_or_finance_required()
def update_network_node(node_id):
    node = tenant_query(NetworkNode).filter_by(id=node_id).first()
    if not node:
        return jsonify({'message': 'Node not found!'}), 404
    cleaned, err = _validate_node_payload(request.json or {},
                                          node.olt_device_id, node=node)
    if err:
        return err
    for field, value in cleaned.items():
        setattr(node, field, value)
    db.session.commit()
    return jsonify(node.to_dict()), 200


@app.route('/api/network-map/nodes/<int:node_id>', methods=['DELETE'])
@jwt_required()
@admin_or_finance_required()
def delete_network_node(node_id):
    node = tenant_query(NetworkNode).filter_by(id=node_id).first()
    if not node:
        return jsonify({'message': 'Node not found!'}), 404
    children = tenant_query(NetworkNode).filter_by(parent_node_id=node.id).all()
    if children:
        names = ', '.join(sorted(c.label for c in children))
        return jsonify({'message':
                        f'Cannot delete "{node.label}" -- it still carries: {names}. '
                        f'Delete or reparent those first.'}), 409
    db.session.delete(node)
    db.session.commit()
    return jsonify({'message': 'Node deleted'}), 200
```

`new_for_tenant` is the codebase's tenant-stamping constructor — confirm its exact signature with `grep -n "def new_for_tenant" app.py` and match how neighbouring routes call it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_network_map_api.py -v`
Expected: all pass

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_network_map_api.py
git commit -m "feat: add network map node CRUD with cycle and uniqueness guards"
```

---

### Task 5: Gross-placement sanity check

Replaces the Task 3 stub.

**Files:**
- Modify: `app.py` — replace the `_map_distance_warnings` stub
- Test: `tests/test_network_map_distance.py`

**Interfaces:**
- Consumes: `NetworkNode` (Task 1).
- Produces: `_map_distance_warnings(nodes, onu_rows) -> [{'node_id': int, 'chain_metres': int, 'reported_metres': int}]`; constants `DISTANCE_CHECK_MIN_METRES = 100`, `DISTANCE_CHECK_FACTOR = 2.0`, `DISTANCE_CHECK_MIN_GAP_METRES = 100`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_network_map_distance.py`:

```python
"""The check is a backstop against a data-entry blunder, NOT a measure of
accuracy. An earlier spec draft proposed a 15% tolerance; the owner reports an
ONU physically 1m from the OLT reading as 6m, so the error is a fixed offset of
several metres, not proportional -- 500% at 1m, 0.3% at 1500m. Any single
percentage is therefore wrong at one end or the other, and the check requires
BOTH a proportional and an absolute overshoot before it says anything."""
import app as appmod


class FakeNode:
    def __init__(self, id, kind, parent_node_id=None, onu_mac=None,
                 latitude=34.4367, longitude=35.8497):
        self.id = id
        self.kind = kind
        self.parent_node_id = parent_node_id
        self.onu_mac = onu_mac
        self.latitude = latitude
        self.longitude = longitude


def onu_row(mac, distance):
    return {'mac_address': mac, 'status': 'online', 'distance': distance}


MAC = 'aa:aa:aa:aa:aa:aa'


def test_the_owners_one_metre_reads_as_six_metres_case_does_not_flag():
    """The case that killed the percentage rule. Must stay silent."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.43671, longitude=35.84970)]
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 6)]) == []


def test_short_reported_distances_never_flag_whatever_the_chain_says():
    """Below the floor, ranging error swamps the signal entirely."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4467, longitude=35.8497)]   # ~1.1km away
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 99)]) == []


def test_a_pin_in_the_wrong_village_flags():
    """~5.5km of chain against a reported 400m: both conditions met."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4867, longitude=35.8497)]
    warnings = appmod._map_distance_warnings(nodes, [onu_row(MAC, 400)])
    assert len(warnings) == 1
    assert warnings[0]['node_id'] == 2
    assert warnings[0]['reported_metres'] == 400
    assert warnings[0]['chain_metres'] > 5000


def test_proportional_overshoot_alone_does_not_flag():
    """3x over, but only a 300m absolute gap -- under the min-gap floor, so
    silent. This is what keeps short runs quiet."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.44025, longitude=35.8497)]   # ~395m
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 150)]) == []


def test_absolute_overshoot_alone_does_not_flag():
    """A 900m gap on a 4km reported run is well under 2x -- legitimate slack
    on a long route, so silent."""
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4808, longitude=35.8497)]   # ~4.9km
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 4000)]) == []


def test_a_chain_shorter_than_reported_never_flags():
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4394, longitude=35.8497)]   # ~300m
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 1200)]) == []


def test_chain_length_accumulates_through_intermediate_hops():
    """The chain is the sum of its spans, not the straight line from root."""
    nodes = [FakeNode(1, 'root', latitude=34.4367, longitude=35.8497),
             FakeNode(2, 'junction', parent_node_id=1,
                      latitude=34.4667, longitude=35.8497),
             FakeNode(3, 'onu', parent_node_id=2, onu_mac=MAC,
                      latitude=34.4367, longitude=35.8497)]
    # Doubles back, so the chain is ~6.6km while the endpoint sits on the root.
    warnings = appmod._map_distance_warnings(nodes, [onu_row(MAC, 500)])
    assert len(warnings) == 1
    assert warnings[0]['chain_metres'] > 6000


def test_an_onu_with_no_reported_distance_is_skipped():
    nodes = [FakeNode(1, 'root'),
             FakeNode(2, 'onu', parent_node_id=1, onu_mac=MAC,
                      latitude=34.4867, longitude=35.8497)]
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 0)]) == []
    assert appmod._map_distance_warnings(nodes, []) == []


def test_a_cycle_does_not_hang_the_check():
    nodes = [FakeNode(2, 'onu', parent_node_id=3, onu_mac=MAC),
             FakeNode(3, 'junction', parent_node_id=2)]
    assert appmod._map_distance_warnings(nodes, [onu_row(MAC, 400)]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_network_map_distance.py -v`
Expected: FAIL — the Task 3 stub returns `[]`, so the two flagging tests fail while the silent ones pass vacuously.

- [ ] **Step 3: Write the implementation**

Replace the stub in `app.py`:

```python
# All three are named constants, not literals: the real values can only be
# tuned once genuine placements exist, and the spec records why a single
# percentage cannot work at both ends of the range.
DISTANCE_CHECK_MIN_METRES = 100       # below this, ranging error dominates
DISTANCE_CHECK_FACTOR = 2.0           # chain must exceed reported by this much
DISTANCE_CHECK_MIN_GAP_METRES = 100   # ...AND by this many absolute metres


def _great_circle_metres(lat1, lon1, lat2, lon2):
    """Haversine. Plain stdlib -- no geo dependency for one formula."""
    radius = 6371000.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lon2) - float(lon1))
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _map_distance_warnings(nodes, onu_rows):
    """Flag ONUs whose traced chain is grossly longer than the OLT's reported
    distance -- a pin in the wrong village, not a pin across the street.

    Requires BOTH a proportional and an absolute overshoot. The factor stops
    it firing on long runs with legitimate slack; the absolute gap stops it
    firing on short runs where the OLT's fixed offset dominates (the owner
    reports 1m reading as 6m). Advisory only: never blocks a save.
    """
    by_id = {n.id: n for n in nodes}
    reported = {}
    for row in onu_rows or []:
        mac = row.get('mac_address')
        distance = _to_int_or_none(row.get('distance'))
        if mac and distance:      # 0 means "offline", not "at the OLT"
            reported[_normalize_mac(mac)] = distance

    warnings = []
    for node in sorted(nodes, key=lambda n: n.id):
        if node.kind != 'onu' or not node.onu_mac:
            continue
        distance = reported.get(_normalize_mac(node.onu_mac))
        if not distance or distance < DISTANCE_CHECK_MIN_METRES:
            continue

        # Walk to the root, summing spans. seen-set, not recursion: a cycle
        # would otherwise loop forever, and this must survive bad data.
        chain, current, seen = 0.0, node, {node.id}
        while current.parent_node_id and current.parent_node_id in by_id:
            parent = by_id[current.parent_node_id]
            if parent.id in seen:
                chain = None      # cyclic: no meaningful chain length
                break
            chain += _great_circle_metres(current.latitude, current.longitude,
                                          parent.latitude, parent.longitude)
            seen.add(parent.id)
            current = parent
        if chain is None:
            continue

        if (chain > distance * DISTANCE_CHECK_FACTOR
                and chain - distance > DISTANCE_CHECK_MIN_GAP_METRES):
            warnings.append({'node_id': node.id,
                             'chain_metres': int(round(chain)),
                             'reported_metres': distance})
    return warnings
```

Add a module-level `_to_int_or_none(value)` returning `None` for anything non-numeric. Do NOT reuse `vsol_olt._to_int(text, default=0)`: it lives in the connector, not `app.py`, and its zero default is indistinguishable from the OLT reporting distance 0 for an offline ONU. `import math` is already present at `app.py:6` — do not add it again.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_network_map_distance.py -v`
Expected: 9 passed

- [ ] **Step 5: Confirm the endpoint now returns warnings**

Run: `python -m pytest tests/test_network_map_api.py -v`
Expected: all still pass (`distance_warnings` is now real rather than `[]`).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_network_map_distance.py
git commit -m "feat: flag grossly mis-placed ONU pins against OLT distance"
```

---

## Deliberately not in this plan

Both are specified in `2026-09-10-network-geo-fiber-map-design.md` and get their own plan, written next. They are separated because each is a distinct subsystem that this plan's deliverable does not depend on — the map is complete and shippable without either.

**Optical logging** — `vsol_olt.get_olt_optical()` walking table `1.3.6.1.4.1.37950.1.1.5.12.2.1.13.1`, a new `olt_optical` agent operation added to **both** `AGENT_OPERATIONS` and `DEVICE_TYPE_OPERATIONS['vsol_olt']`, the `OnuOpticalReading` and `OnuOpticalHourly` tables, and a 15-minute APScheduler job that **must omit** `next_run_time` (every existing job passes `datetime.now()`, firing during `flask db upgrade` on deploy — `NetworkAgentJob`'s docstring records that this is exactly why a scheduled pruner was avoided).

**The deregister-reason scrape** — gated behind verifying whether the V1600D permits more than one concurrent admin session. If it does not, the scraper will evict staff from their own OLT web UI, and the component should be reconsidered rather than shipped. The `last_dereg_reason` and `last_dereg_reason_at` columns are created in Task 1 so that plan needs no second migration on `network_node`.

---

### Task 6: Map dependencies, pure style helpers, and read-only rendering

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/components/fiberMapStyles.js`
- Create: `frontend/src/components/fiberMapStyles.test.js`
- Create: `frontend/src/components/NetworkMapView.js`
- Create: `frontend/src/components/networkMap.css`

**Interfaces:**
- Consumes: `GET /api/network-map` (Task 3).
- Produces: `spanStyle(span) -> {color, weight, dashArray, className}`; `nodeMarkerStyle(kind, status) -> {color, radius, fillOpacity}`; `TILE_LAYERS` (array of `{key, name, url, attribution, maxZoom}`); `NetworkMapView` default export.

Pure helpers go in their own module with their own test, matching the established pattern of `buildTopologyTree.js` / `filterTopologyTree.js` / `mergeNetworkStatus.js` — each a pure function beside a `.test.js`. This keeps the colour rules testable without mounting Leaflet in jsdom, which is where component tests of map libraries usually go to die.

- [ ] **Step 1: Install dependencies**

```bash
cd frontend && npm install --save leaflet@1.9.4 react-leaflet@4.2.1 && npm install --save-dev @testing-library/react@14.2.1 @testing-library/jest-dom@6.4.2
```

`@testing-library/react` is genuinely missing today, which is why every refresh-lifecycle guarantee in Tree v2 is verified by reading only — the top follow-up from that build. Installing it here is deliberate: span colour is the whole feature and must not ship with the same blind spot.

Verify: `node -e "console.log(require('./frontend/package.json').dependencies['react-leaflet'])"` prints `^4.2.1`.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/fiberMapStyles.test.js`:

```javascript
import { spanStyle, nodeMarkerStyle, TILE_LAYERS } from './fiberMapStyles';

describe('spanStyle', () => {
  test('a green span is thin and solid', () => {
    const s = spanStyle({ status: 'green', is_fault_boundary: false });
    expect(s.color).toBe('#2e7d32');
    expect(s.dashArray).toBeNull();
    expect(s.className).toBe('');
  });

  test('an ordinary red span is red but not animated', () => {
    const s = spanStyle({ status: 'red', is_fault_boundary: false });
    expect(s.color).toBe('#c62828');
    expect(s.className).toBe('');
  });

  test('the fault boundary is thicker and animated', () => {
    const boundary = spanStyle({ status: 'red', is_fault_boundary: true });
    const ordinary = spanStyle({ status: 'red', is_fault_boundary: false });
    expect(boundary.weight).toBeGreaterThan(ordinary.weight);
    expect(boundary.className).toBe('fiber-span-fault-boundary');
  });

  test('a grey span is dashed, so unknown never reads as an outage', () => {
    const s = spanStyle({ status: 'grey', is_fault_boundary: false });
    expect(s.color).toBe('#9e9e9e');
    expect(s.dashArray).not.toBeNull();
  });

  test('an unrecognised status falls back to grey rather than throwing', () => {
    expect(spanStyle({ status: 'banana' }).color).toBe('#9e9e9e');
    expect(spanStyle({}).color).toBe('#9e9e9e');
  });
});

describe('nodeMarkerStyle', () => {
  test('the root is drawn larger than an ONU', () => {
    expect(nodeMarkerStyle('root', 'online').radius)
      .toBeGreaterThan(nodeMarkerStyle('onu', 'online').radius);
  });

  test('offline is red and online is green at every kind', () => {
    expect(nodeMarkerStyle('onu', 'offline').color).toBe('#c62828');
    expect(nodeMarkerStyle('junction', 'online').color).toBe('#2e7d32');
  });

  test('unknown is grey, never red', () => {
    expect(nodeMarkerStyle('onu', 'unknown').color).toBe('#9e9e9e');
  });
});

describe('TILE_LAYERS', () => {
  test('offers a keyless satellite layer first and a street layer', () => {
    expect(TILE_LAYERS).toHaveLength(2);
    expect(TILE_LAYERS[0].key).toBe('satellite');
    expect(TILE_LAYERS[1].key).toBe('street');
  });

  test('no layer URL carries an API key or token placeholder', () => {
    TILE_LAYERS.forEach((layer) => {
      expect(layer.url).not.toMatch(/key=|token=|access_token|apikey/i);
    });
  });

  test('every layer carries attribution, which both sources require', () => {
    TILE_LAYERS.forEach((layer) => {
      expect(layer.attribution.length).toBeGreaterThan(10);
    });
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/fiberMapStyles.test.js"`
Expected: FAIL — `Cannot find module './fiberMapStyles'`

**If it instead reports "No tests found", you are hitting the worktree `testMatch` bug — that is not a pass.** Re-read the Global Constraints entry on Jest.

- [ ] **Step 4: Write the implementation**

Create `frontend/src/components/fiberMapStyles.js`:

```javascript
// Pure style rules for the geographic fibre map, kept out of the component so
// they can be tested without mounting Leaflet in jsdom. Mirrors the existing
// buildTopologyTree.js / mergeNetworkStatus.js pattern.

const GREEN = '#2e7d32';
const RED = '#c62828';
const GREY = '#9e9e9e';

// Both sources are keyless and were verified serving real imagery over
// Tripoli. Esri World Imagery is usable to z=19 (~0.3 m/px, enough to pin a
// rooftop); OSM's own maximum is 19 and it 400s above that.
export const TILE_LAYERS = [
  {
    key: 'satellite',
    name: 'Satellite',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community',
    maxZoom: 19,
  },
  {
    key: 'street',
    name: 'Street',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
  },
];

export function spanStyle(span) {
  const status = (span && span.status) || 'grey';
  if (status === 'green') {
    return { color: GREEN, weight: 3, dashArray: null, className: '' };
  }
  if (status === 'red') {
    // The fault boundary is the one span worth driving to, so it is the only
    // thing on the map that moves. Everything red below it is consequence.
    return span.is_fault_boundary
      ? { color: RED, weight: 7, dashArray: null,
          className: 'fiber-span-fault-boundary' }
      : { color: RED, weight: 3, dashArray: null, className: '' };
  }
  // Dashed, so "we have no data" is visually distinct from "it is up" at a
  // glance and can never be misread as an outage.
  return { color: GREY, weight: 2, dashArray: '6 6', className: '' };
}

export function nodeMarkerStyle(kind, status) {
  const color = status === 'online' ? GREEN : status === 'offline' ? RED : GREY;
  const radius = kind === 'root' ? 10 : kind === 'junction' ? 6 : 7;
  return { color, radius, fillOpacity: 0.9 };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/fiberMapStyles.test.js"`
Expected: 11 passed

- [ ] **Step 6: Build the read-only page**

Create `frontend/src/components/networkMap.css`:

```css
/* Only the fault boundary animates -- the span a technician should drive to. */
@keyframes fiber-fault-pulse {
  0%   { stroke-opacity: 1; }
  50%  { stroke-opacity: 0.35; }
  100% { stroke-opacity: 1; }
}
.fiber-span-fault-boundary {
  animation: fiber-fault-pulse 1.4s ease-in-out infinite;
}
.fiber-map-container {
  height: 70vh;
  width: 100%;
  border-radius: 8px;
}
/* Leaflet computes size from the container, so a zero-height parent renders a
   blank map with no error. Guard against a flex parent collapsing it. */
.fiber-map-container .leaflet-container {
  height: 100%;
  width: 100%;
  background: #e0e0e0;
}
```

Create `frontend/src/components/NetworkMapView.js` — a read-only page for now. Placement arrives in Task 7.

```javascript
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Typography, Alert, CircularProgress, ToggleButton,
         ToggleButtonGroup, Paper } from '@mui/material';
import { MapContainer, TileLayer, CircleMarker, Polyline, Tooltip } from 'react-leaflet';
import { apiService, useAppContext } from '../AppContext';
import { spanStyle, nodeMarkerStyle, TILE_LAYERS } from './fiberMapStyles';
import 'leaflet/dist/leaflet.css';
import './networkMap.css';

// Tripoli / Koura -- where DeltaNet's plant is. Only used when a tenant has no
// nodes placed yet; once a root exists the map centres on it.
const DEFAULT_CENTER = [34.4367, 35.8497];

export default function NetworkMapView({ oltDeviceId }) {
  const { setSnackbar } = useAppContext();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [layerKey, setLayerKey] = useState('satellite');

  const load = useCallback(async () => {
    try {
      const res = await apiService.get(
        `/api/network-map?olt_device_id=${oltDeviceId}`);
      setData(res.data);
    } catch (err) {
      setSnackbar({ open: true, severity: 'error',
                    message: 'Could not load the network map.' });
    } finally {
      setLoading(false);
    }
  }, [oltDeviceId, setSnackbar]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <CircularProgress />;
  if (!data) return <Alert severity="error">The network map is unavailable.</Alert>;

  const byId = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
  const root = data.nodes.find((n) => n.kind === 'root');
  const center = root ? [root.latitude, root.longitude] : DEFAULT_CENTER;
  const layer = TILE_LAYERS.find((l) => l.key === layerKey) || TILE_LAYERS[0];
  const boundary = data.spans.filter((s) => s.is_fault_boundary);

  return (
    <Box>
      <Typography variant="h5" gutterBottom>Network Map</Typography>

      {boundary.length > 0 && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {boundary.length === 1
            ? `Fault localised to the span into "${byId[boundary[0].child_node_id]?.label}".`
            : `${boundary.length} separate faults localised.`}
        </Alert>
      )}

      {data.orphans.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {data.orphans.length} node(s) are not connected to the control room
          and are not drawn. Their parent links form a loop and need fixing.
        </Alert>
      )}

      {data.distance_warnings.length > 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {data.distance_warnings.length} pin(s) sit much further from the
          control room than the OLT reports. Likely placed in the wrong spot.
        </Alert>
      )}

      <ToggleButtonGroup size="small" exclusive value={layerKey} sx={{ mb: 1 }}
        onChange={(e, v) => v && setLayerKey(v)}>
        {TILE_LAYERS.map((l) => (
          <ToggleButton key={l.key} value={l.key}>{l.name}</ToggleButton>
        ))}
      </ToggleButtonGroup>

      <Paper className="fiber-map-container">
        <MapContainer center={center} zoom={16} scrollWheelZoom
                      className="leaflet-container">
          <TileLayer key={layer.key} url={layer.url}
                     attribution={layer.attribution} maxZoom={layer.maxZoom} />

          {data.spans.map((span) => {
            const a = byId[span.parent_node_id];
            const b = byId[span.child_node_id];
            if (!a || !b) return null;
            const style = spanStyle(span);
            return (
              <Polyline key={`${span.parent_node_id}-${span.child_node_id}`}
                positions={[[a.latitude, a.longitude], [b.latitude, b.longitude]]}
                pathOptions={style}>
                <Tooltip sticky>
                  {a.label} &rarr; {b.label}
                  {span.is_fault_boundary ? ' — FAULT HERE' : ''}
                </Tooltip>
              </Polyline>
            );
          })}

          {data.nodes.map((node) => {
            const status = data.node_status[node.id] || 'unknown';
            const style = nodeMarkerStyle(node.kind, status);
            return (
              <CircleMarker key={node.id}
                center={[node.latitude, node.longitude]}
                radius={style.radius}
                pathOptions={{ color: style.color, fillColor: style.color,
                               fillOpacity: style.fillOpacity }}>
                <Tooltip>
                  <strong>{node.label}</strong><br />
                  {node.kind} — {status}
                  {node.onu_mac ? <><br />{node.onu_mac}</> : null}
                </Tooltip>
              </CircleMarker>
            );
          })}
        </MapContainer>
      </Paper>

      <Typography variant="caption" sx={{ mt: 1, display: 'block' }}>
        {data.last_result_at
          ? `ONU status from the OLT check at ${data.last_result_at}.`
          : 'No successful OLT check yet — every span is shown as unknown.'}
      </Typography>
    </Box>
  );
}
```

Confirm the `apiService` import form against a neighbouring component — it is a **direct named export** of `AppContext.js`, and only `setSnackbar` comes from `useAppContext()`. Check `NetworkTreeView.js` and copy its exact import line rather than trusting the sketch above.

- [ ] **Step 7: Verify the build compiles**

Run: `cd frontend && npx react-scripts build`
Expected: "Compiled with warnings" (the ~30 pre-existing ones) and a written bundle. **Not** `CI=true` — that turns those warnings into a failure.

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/fiberMapStyles.js frontend/src/components/fiberMapStyles.test.js frontend/src/components/NetworkMapView.js frontend/src/components/networkMap.css
git commit -m "feat: add read-only geographic fibre map page"
```

---

### Task 7: Placement UX

**Files:**
- Modify: `frontend/src/components/NetworkMapView.js`
- Create: `frontend/src/components/NetworkMapView.test.js`

**Interfaces:**
- Consumes: Task 6; `GET /api/network-map/unplaced-onus` (Task 3); node CRUD (Task 4).
- Produces: no new exports — behaviour only.

Behaviour to build:

1. **Add mode.** A toggle. While on, clicking the map opens a dialog: kind (junction or ONU), label, and — for an ONU — a searchable select of unplaced ONUs showing MAC, the OLT's own description and any linked customer name.
2. **First node.** With no nodes placed, the only offered kind is `root`, labelled "Control room". The API enforces one root per OLT; the UI should not offer a second.
3. **Draw from here.** Selecting a placed node and clicking "Draw from here" arms the next map click to create a child of that node. This is the core interaction — it is how a run is traced.
4. **Drag to reposition.** Dragging a marker issues `PUT` with new coordinates.
5. **Unplaced panel.** A count and list of ONUs not yet placed, so staff can see what remains. 77 unique MACs exist today, 66 online.
6. **Delete.** Refused with 409 when the node still carries children; surface the server's message verbatim, since it names them.

Every write is admin/finance. The page must **hide** these controls for employee and collector rather than showing controls that 403 — and must still render the map for them.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/NetworkMapView.test.js`:

```javascript
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

// react-leaflet renders a real Leaflet map, which jsdom cannot size. Stub it to
// plain divs: this test is about which CONTROLS appear for which role and what
// the page does with the payload -- not about Leaflet's own rendering.
jest.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="map">{children}</div>,
  TileLayer: ({ url }) => <div data-testid="tile" data-url={url} />,
  CircleMarker: ({ children }) => <div data-testid="marker">{children}</div>,
  Polyline: ({ children }) => <div data-testid="span">{children}</div>,
  Tooltip: ({ children }) => <div>{children}</div>,
}));

const mockGet = jest.fn();
jest.mock('../AppContext', () => ({
  apiService: { get: (...a) => mockGet(...a), post: jest.fn(), put: jest.fn(),
                delete: jest.fn() },
  useAppContext: () => ({ setSnackbar: jest.fn() }),
}));

import NetworkMapView from './NetworkMapView';

const PAYLOAD = {
  nodes: [
    { id: 1, kind: 'root', label: 'Control Room', latitude: 34.4367,
      longitude: 35.8497, parent_node_id: null, onu_mac: null },
    { id: 2, kind: 'onu', label: 'Villa Eid', latitude: 34.4368,
      longitude: 35.8498, parent_node_id: 1, onu_mac: 'aa:aa:aa:aa:aa:aa' },
  ],
  spans: [{ parent_node_id: 1, child_node_id: 2, status: 'red',
            is_fault_boundary: true }],
  node_status: { 1: 'online', 2: 'offline' },
  orphans: [],
  onu_status: { aaaaaaaaaaaa: 'offline' },
  last_result_at: '2026-09-10 19:00:00',
  distance_warnings: [],
};

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockResolvedValue({ data: PAYLOAD });
});

test('names the span the fault was localised to', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.getByText(/Villa Eid/)).toBeInTheDocument();
  expect(screen.getByText(/Fault localised/)).toBeInTheDocument();
});

test('renders the map for an employee but offers no editing controls', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="employee" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.queryByRole('button', { name: /add node/i })).toBeNull();
  expect(screen.queryByRole('button', { name: /draw from here/i })).toBeNull();
});

test('offers editing controls to an admin', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.getByRole('button', { name: /add node/i })).toBeInTheDocument();
});

test('a failed load shows an error and never an empty map', async () => {
  mockGet.mockRejectedValue(new Error('boom'));
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() =>
    expect(screen.getByText(/network map is unavailable/i)).toBeInTheDocument());
  expect(screen.queryByTestId('map')).toBeNull();
});

test('says so plainly when no OLT check has ever succeeded', async () => {
  mockGet.mockResolvedValue({
    data: { ...PAYLOAD, last_result_at: null, spans: [], node_status: {} } });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() =>
    expect(screen.getByText(/No successful OLT check yet/i)).toBeInTheDocument());
});

test('warns about orphaned nodes rather than hiding them silently', async () => {
  mockGet.mockResolvedValue({ data: { ...PAYLOAD, orphans: [7, 8] } });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() =>
    expect(screen.getByText(/2 node\(s\) are not connected/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/NetworkMapView.test.js"`
Expected: the two role tests FAIL (no `userRole` prop handling, no Add node button); the rest may already pass from Task 6.

- [ ] **Step 3: Implement the placement UI**

Extend `NetworkMapView.js`: accept a `userRole` prop, derive `const canEdit = ['admin', 'finance'].includes(userRole)`, and gate every control and every map-click handler on it. Add the add-node dialog, the draw-from-here arming, marker `draggable={canEdit}` with a `dragend` handler issuing `PUT`, the unplaced-ONU side panel fed by `GET /api/network-map/unplaced-onus`, and a delete action that renders the server's 409 message verbatim.

Reload the map payload after every successful write. Do **not** clear `data` to `null` before refetching — Tree v2 shipped a page that blanked on every refresh, and on this page that would flash the whole map away each time a pin is dropped.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/NetworkMapView.test.js"`
Expected: 6 passed

- [ ] **Step 5: Verify the build compiles**

Run: `cd frontend && npx react-scripts build`
Expected: compiled with the pre-existing warnings only.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/NetworkMapView.js frontend/src/components/NetworkMapView.test.js
git commit -m "feat: add node placement and run tracing to the fibre map"
```

---

### Task 8: Route the page and verify against the real OLT

**Files:**
- Modify: `frontend/src/App.js` (or wherever routes are declared — confirm with `grep -rn "NetworkTreeView" frontend/src --include=*.js`)
- Modify: the navigation component that lists the Network Tree link

**Interfaces:**
- Consumes: everything above.
- Produces: a reachable `/network-map` route.

- [ ] **Step 1: Wire the route and nav entry**

Copy exactly how `NetworkTreeView` is routed and how its nav entry is role-gated. Grant the same roles — the map's read path uses `network_view_required()`, matching the tree.

- [ ] **Step 2: Run the full backend suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 3: Run the full frontend suite**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/*.test.js"`
Expected: all pass, and a **non-zero test count**. A count of zero is the worktree `testMatch` bug, not success.

- [ ] **Step 4: Verify against the real OLT — from an isolated database**

**Read the local-test-server warning before starting a server.** `preview_start` reads the *main* checkout's `launch.json`, not the active worktree, so it will run against the real `instance/database.db`. During the topology-tree build this created a stray tenant in the owner's real database. Start the server yourself with an explicit `cwd` and an explicit scratch `DATABASE_URL`, and assert the resolved URI before serving.

Then confirm by hand: place a root, trace three nodes including one ONU, verify spans render, drag a pin and confirm it persists across a reload, and attempt to delete the root to see the 409 naming its children.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: route the network map page"
```

## Self-review notes

Checked against the spec: `NetworkNode` (Task 1), status algorithm including all three owner cases (Task 2), both read endpoints (Task 3), all three write endpoints and every stated invariant (Task 4), the gross-placement check with the owner's 1 m/6 m case as an explicit test (Task 5), Leaflet with both keyless layers and attribution (Task 6), the full placement flow and role gating (Task 7), routing and live verification (Task 8).

Spec requirements deliberately deferred to the follow-on plan, with the columns they need already created in Task 1: optical logging, and the deregister-reason scrape.

One spec line is **not** covered by any task and is intentionally dropped: the spec's suggestion that the map "degrade to a blank canvas with a visible notice" if a tile source disappears. Leaflet already renders an empty grey canvas with markers and spans intact when tiles fail, which is the required behaviour; detecting *why* tiles are missing would mean intercepting tile errors for a case with no evidence of occurring. The `background: #e0e0e0` in `networkMap.css` is what makes the degraded state legible.
