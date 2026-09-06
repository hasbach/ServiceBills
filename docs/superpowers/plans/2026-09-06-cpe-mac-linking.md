# CPE-MAC Linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link a customer to the network by their own router's MAC — which the OLT already learns — so the tree places them automatically, and remember where each one was last seen.

**Architecture:** A new SNMP walk joins the OLT's MAC-learning table to its interface names, yielding `CPE MAC → ONU`. A button creates a job for it; a separate apply step, acting as the logged-in admin, writes each matched customer's remembered ONU. The tree is untouched — it already renders whatever `customers` array an ONU carries.

**Tech Stack:** Flask + SQLAlchemy + Alembic, pysnmp (asyncio API), pytest. React 18 (CRA) + MUI, Jest.

**Spec:** `docs/superpowers/specs/2026-09-06-cpe-mac-linking-design.md`

## Global Constraints

- **Never** use `logger.exception` or `exc_info=True` on any path whose frame locals can hold a device credential. Sentry's `LoggingIntegration` is live and captures frame locals at ERROR. Log `exc.__class__.__name__` and `str(exc)` only.
- **The agent must never write customer records.** It authenticates as a device relay, not a person. It returns the map; the cloud applies it.
- **The uplink exclusion is load-bearing.** 186 of the OLT's 316 learned MACs sit on `GE0/10`. Only ports whose `ifName` matches `EPON<pon>ONU<n>` may be kept.
- **Locate never clears a placement.** A customer whose CPE was not seen keeps their previous ONU and timestamp untouched. That is the memory.
- The Alembic chain cannot replay on SQLite (origin's `bd054e2e7cf9` calls `op.create_unique_constraint` outside batch mode). Tests build schema with `create_all`; a migration test must bootstrap with `create_all` + `stamp` and then run upgrade/downgrade/upgrade — see `tests/test_topology_migration.py`'s `NETWORK_AGENT_REVISION` test.
- Frontend tests **must** use an explicit pattern — the plain CRA command silently reports "No tests found" here because of the dot-prefixed `.worktrees` path segment, which looks identical to passing:
  `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/<file>.test.js"`
- Build check is `cd frontend && npx react-scripts build` **without** `CI=true` (that is what the Dockerfile runs; `CI=true` fails on ~30 pre-existing warnings in unrelated files).
- Do not use `git stash` — the stash stack is shared across worktrees. Use a WIP commit.
- Never commit anything under `frontend/build/` or `build/` — tracked artifacts a CI bot regenerates.
- Baseline on this branch: **618 backend tests, 46 frontend.**

---

## File Structure

| File | Responsibility |
|---|---|
| `migrations/versions/c3a71d5e04b8_add_cpe_mac_linking.py` | **new** — two customer columns, index, unique constraint |
| `app.py` | `Customer` columns + `__table_args__`; `cpe_mac_address` on customer create/update; `AGENT_OPERATIONS`; `_validate_agent_result`; `_locate_customers_from_result`; the two Locate endpoints |
| `vsol_olt.py` | `get_cpe_locations(server)` and its helpers |
| `agent/servicebills_agent.py` | `cpe_locations` in `ALLOWED_OPERATIONS` and in `execute_job`'s dispatch |
| `tests/test_cpe_locations_connector.py` | **new** — the connector against recorded varbinds |
| `tests/test_cpe_linking_api.py` | **new** — field validation, uniqueness, apply semantics, authorization |
| `tests/test_topology_migration.py` | extended — the new migration up/down/up |
| `frontend/src/components/formatStamp.js` | **new** — the shared UTC→local helper |
| `frontend/src/components/formatStamp.test.js` | **new** |
| `frontend/src/components/SubscriptionsView.js` | CPE MAC field on the create and edit dialogs |
| `frontend/src/components/NetworkTreeView.js` | *Locate Customers* button; local-time stamps |
| `frontend/src/components/NetworkDeviceManagementView.js` | local-time stamps |
| `frontend/src/components/buildTopologyTree.js` | the remembered-placement marker |

---

## Task 1: Schema — the CPE MAC and the last-seen timestamp

**Files:**
- Create: `migrations/versions/c3a71d5e04b8_add_cpe_mac_linking.py`
- Modify: `app.py` — `class Customer` (starts line 493)
- Test: `tests/test_topology_migration.py` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces: `Customer.cpe_mac_address` (`String(20)`, nullable, indexed) and `Customer.onu_last_seen_at` (`DateTime`, nullable), plus the constraint `uq_customer_tenant_cpe_mac` on `(tenant_id, cpe_mac_address)`. Tasks 2 and 5 read and write both.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topology_migration.py`:

```python
CPE_LINKING_REVISION = "c3a71d5e04b8"


def test_cpe_linking_migration_upgrade_downgrade_upgrade():
    """Same bootstrap reasoning as the network-agent migration test above: the
    real chain cannot be walked on SQLite, so build the pre-migration schema
    from the current models (minus what this migration adds), stamp at its
    parent, and drive the real upgrade()/downgrade() from there."""
    tmpdir = tempfile.mkdtemp(prefix="cpe_linking_migration_test_")
    db_path = os.path.join(tmpdir, "cpe_linking_migration.db")
    mig_app = Flask("test_cpe_linking_migration")
    mig_app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + db_path.replace("\\", "/"))
    mig_db = SQLAlchemy(mig_app)
    Migrate(mig_app, mig_db, directory=MIGRATIONS_DIR, render_as_batch=True)

    try:
        with mig_app.app_context():
            engine = mig_db.engine
            # Start from the post-migration schema and go DOWN first, rather
            # than hand-dropping the columns to fake the pre-migration state.
            # `ALTER TABLE customer DROP COLUMN cpe_mac_address` cannot work
            # here: the column participates in uq_customer_tenant_cpe_mac, and
            # SQLite refuses to drop a column an index or constraint depends
            # on. Downgrading first exercises the real downgrade() and leaves
            # exactly the pre-migration shape for the upgrade to act on.
            appmod.db.metadata.create_all(bind=engine)
            stamp(directory=MIGRATIONS_DIR, revision=CPE_LINKING_REVISION)

            downgrade(directory=MIGRATIONS_DIR, revision="-1")
            cols = _table_columns(engine, "customer")
            assert "cpe_mac_address" not in cols
            assert "onu_last_seen_at" not in cols
            assert ("tenant_id", "cpe_mac_address") not in _unique_constraint_columns(
                engine, "customer")

            upgrade(directory=MIGRATIONS_DIR, revision=CPE_LINKING_REVISION)
            cols = _table_columns(engine, "customer")
            assert "cpe_mac_address" in cols
            assert "onu_last_seen_at" in cols
            assert ("tenant_id", "cpe_mac_address") in _unique_constraint_columns(
                engine, "customer")

            downgrade(directory=MIGRATIONS_DIR, revision="-1")
            assert "cpe_mac_address" not in _table_columns(engine, "customer")

            engine.dispose()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
```

If `downgrade`-first turns out not to work either, say so in your report rather
than weakening the assertions — a migration test that does not execute the real
`upgrade()` and `downgrade()` is worth nothing, and this migration's downgrade
drops a unique constraint through batch mode, which is exactly the operation
most likely to be wrong.

Also add to `tests/test_cpe_linking_api.py` (create it):

```python
"""The CPE MAC is the customer's own router, as the OLT learns it -- unique
per customer, unlike the shared ONU MAC. See
docs/superpowers/specs/2026-09-06-cpe-mac-linking-design.md."""
import app as appmod
from tests.conftest import make_tenant


def _plan(client, hdr):
    return client.post("/api/subscription_plans", headers=hdr,
                       json={"name": "P", "price": 10,
                             "billing_cycle": "monthly"}).get_json()["plan"]["id"]


def _customer(client, hdr, plan_id, name, **extra):
    body = {"name": name, "phone": "1", "address": "a",
            "subscription_plan_id": plan_id,
            "subscription_start_date": "2026-01-01"}
    body.update(extra)
    return client.post("/api/customers", headers=hdr, json=body)


def test_customer_model_has_the_cpe_columns(app, client):
    hdr = make_tenant(client, "Cpe A", "cpe_a_admin")
    plan_id = _plan(client, hdr)
    _customer(client, hdr, plan_id, "C")
    with app.app_context():
        customer = appmod.Customer.query.filter_by(name="C").first()
        assert customer.cpe_mac_address is None
        assert customer.onu_last_seen_at is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_cpe_linking_api.py -q -p no:warnings`
Expected: FAIL — `AttributeError: 'Customer' object has no attribute 'cpe_mac_address'`

- [ ] **Step 3: Add the columns**

In `app.py`, inside `class Customer`, immediately after the existing
`onu_mac_address` column (currently line 532):

```python
    # The customer's OWN router, as the OLT's MAC-learning table sees it.
    # Unlike onu_mac_address -- which is the ONU's address and therefore
    # SHARED by everyone behind that transparent bridge -- this identifies
    # exactly one customer, which is what lets the OLT place them without
    # anyone having to know which ONU they are on.
    cpe_mac_address = db.Column(db.String(20), nullable=True, index=True)
    # When that CPE was last located. Null means never. Compared against the
    # newest completed cpe_locations job to tell a confirmed placement from a
    # remembered one -- see the tree's "last seen" marker.
    onu_last_seen_at = db.Column(db.DateTime, nullable=True)
```

`Customer` has no `__table_args__` today, so add one at the end of the class
body, after the last column and before the first `@property`:

```python
    # A MAC identifies one physical device, so two customers cannot own the
    # same one. The create/update endpoints check first for a friendly error
    # naming the holder; this is the backstop that survives a race between
    # two concurrent writes. NULLs do not collide in either Postgres or
    # SQLite, so the many customers with no CPE recorded are unaffected.
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'cpe_mac_address',
                            name='uq_customer_tenant_cpe_mac'),
    )
```

- [ ] **Step 4: Write the migration**

Create `migrations/versions/c3a71d5e04b8_add_cpe_mac_linking.py`:

```python
"""add customer cpe_mac_address and onu_last_seen_at

Revision ID: c3a71d5e04b8
Revises: b71fe5010a39
Create Date: 2026-09-06 12:00:00.000000

Hand-written rather than autogenerated, for the reason 5f65a6fd6e8d already
records: this repo's local SQLite database cannot reach the real head at all
(origin's bd054e2e7cf9 calls op.create_unique_constraint outside batch mode,
which SQLite rejects), so autogenerate has nothing valid to diff against.
The operations below are exactly what it would emit from Customer's two new
columns and its new UniqueConstraint.

Additive only: two nullable columns on an existing table. No data is read,
written or moved, so this is safe to run against production's populated
customer table.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3a71d5e04b8'
down_revision = 'b71fe5010a39'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cpe_mac_address', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('onu_last_seen_at', sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f('ix_customer_cpe_mac_address'),
                              ['cpe_mac_address'], unique=False)
        batch_op.create_unique_constraint('uq_customer_tenant_cpe_mac',
                                          ['tenant_id', 'cpe_mac_address'])


def downgrade():
    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.drop_constraint('uq_customer_tenant_cpe_mac', type_='unique')
        batch_op.drop_index(batch_op.f('ix_customer_cpe_mac_address'))
        batch_op.drop_column('onu_last_seen_at')
        batch_op.drop_column('cpe_mac_address')
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_cpe_linking_api.py tests/test_topology_migration.py -q -p no:warnings`
Expected: PASS

Run: `python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; c=Config('migrations/alembic.ini'); c.set_main_option('script_location','migrations'); print(ScriptDirectory.from_config(c).get_heads())"`
Expected: exactly one head, `('c3a71d5e04b8',)`. **A second head fails the production deploy**, because the container runs `flask db upgrade` on start.

Run: `python -m pytest -q -p no:warnings`
Expected: PASS, 620 total

- [ ] **Step 6: Commit**

```bash
git add app.py migrations/versions/c3a71d5e04b8_add_cpe_mac_linking.py tests/test_cpe_linking_api.py tests/test_topology_migration.py
git commit -m "feat: add Customer.cpe_mac_address and onu_last_seen_at"
```

---

## Task 2: The CPE MAC on the customer endpoints

**Files:**
- Modify: `app.py` — `create_customer` (~line 3024), `update_customer` (~line 3301), and both `to_dict`-style payloads (~lines 2960, 3377)
- Test: `tests/test_cpe_linking_api.py`

**Interfaces:**
- Consumes: Task 1's columns.
- Produces: `POST`/`PUT /api/customers` accept and return `cpe_mac_address`; a duplicate is rejected with HTTP 400 and a message naming the holder. Task 5 matches on the stored value.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cpe_linking_api.py`:

```python
def test_cpe_mac_is_stored_canonicalised_and_returned(app, client):
    hdr = make_tenant(client, "Cpe B", "cpe_b_admin")
    plan_id = _plan(client, hdr)
    resp = _customer(client, hdr, plan_id, "C", cpe_mac_address="DC-8E-8D-61-B0-61")
    assert resp.status_code == 201, resp.get_json()
    with app.app_context():
        customer = appmod.Customer.query.filter_by(name="C").first()
        assert customer.cpe_mac_address == "dc:8e:8d:61:b0:61"
    listed = client.get("/api/customers", headers=hdr).get_json()
    rows = listed["customers"] if isinstance(listed, dict) else listed
    assert any(c.get("cpe_mac_address") == "dc:8e:8d:61:b0:61" for c in rows)


def test_a_second_customer_cannot_claim_the_same_cpe(app, client):
    hdr = make_tenant(client, "Cpe C", "cpe_c_admin")
    plan_id = _plan(client, hdr)
    _customer(client, hdr, plan_id, "First", cpe_mac_address="dc:8e:8d:61:b0:61")
    resp = _customer(client, hdr, plan_id, "Second", cpe_mac_address="DC:8E:8D:61:B0:61")
    assert resp.status_code == 400
    # The message must name the holder -- "duplicate" alone leaves the
    # operator hunting through 300 customers for the clash.
    assert "First" in resp.get_json()["error"]


def test_the_same_cpe_may_be_used_by_another_tenant(app, client):
    hdr_one = make_tenant(client, "Cpe D1", "cpe_d1_admin")
    _customer(client, hdr_one, _plan(client, hdr_one), "Theirs",
              cpe_mac_address="dc:8e:8d:61:b0:61")
    hdr_two = make_tenant(client, "Cpe D2", "cpe_d2_admin")
    resp = _customer(client, hdr_two, _plan(client, hdr_two), "Ours",
                     cpe_mac_address="dc:8e:8d:61:b0:61")
    assert resp.status_code == 201, resp.get_json()


def test_updating_a_customer_to_its_own_cpe_is_not_a_duplicate(app, client):
    hdr = make_tenant(client, "Cpe E", "cpe_e_admin")
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "C",
                    cpe_mac_address="dc:8e:8d:61:b0:61").get_json()["customer_id"]
    resp = client.put(f"/api/customers/{cid}", headers=hdr,
                      json={"cpe_mac_address": "dc:8e:8d:61:b0:61"})
    assert resp.status_code == 200, resp.get_json()


def test_clearing_the_cpe_is_allowed(app, client):
    hdr = make_tenant(client, "Cpe F", "cpe_f_admin")
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "C",
                    cpe_mac_address="dc:8e:8d:61:b0:61").get_json()["customer_id"]
    assert client.put(f"/api/customers/{cid}", headers=hdr,
                      json={"cpe_mac_address": ""}).status_code == 200
    with app.app_context():
        assert appmod.Customer.query.get(cid).cpe_mac_address is None


def test_a_malformed_cpe_is_rejected(app, client):
    hdr = make_tenant(client, "Cpe G", "cpe_g_admin")
    resp = _customer(client, hdr, _plan(client, hdr), "C", cpe_mac_address="nope")
    assert resp.status_code == 400
    assert "not a valid MAC address" in resp.get_json()["error"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cpe_linking_api.py -q -p no:warnings`
Expected: FAIL — the value is not stored, so the canonicalisation and duplicate assertions fail.

- [ ] **Step 3: Add a shared duplicate check**

In `app.py`, immediately above `create_customer`, add:

```python
def _check_cpe_mac_available(mac, customer_id=None):
    """Return an error message if another customer in this tenant already
    holds this CPE MAC, else None.

    The DB constraint uq_customer_tenant_cpe_mac is the real guarantee; this
    exists to name the other customer, because "duplicate MAC" alone leaves
    the operator hunting through 300 records for the clash.
    """
    if not mac:
        return None
    query = tenant_query(Customer).filter(Customer.cpe_mac_address == mac)
    if customer_id is not None:
        query = query.filter(Customer.id != customer_id)
    holder = query.first()
    if holder:
        return (f"CPE MAC {mac} is already recorded for "
                f"{holder.name}. A router belongs to one customer.")
    return None
```

- [ ] **Step 4: Wire it into create and update**

In `create_customer`, alongside the existing `onu_mac_address` validation:

```python
        cpe_mac_address, cpe_error = _validate_mac_address(
            data.get('cpe_mac_address'), allow_empty=True)
        if cpe_error:
            return jsonify({'error': cpe_error}), 400
        duplicate = _check_cpe_mac_available(cpe_mac_address)
        if duplicate:
            return jsonify({'error': duplicate}), 400
```

and add `cpe_mac_address=cpe_mac_address` to the `Customer(...)` constructor
call alongside `onu_mac_address=onu_mac_address`.

In `update_customer`, alongside the existing `onu_mac_address` block:

```python
        if 'cpe_mac_address' in data:
            cpe_mac_address, cpe_error = _validate_mac_address(
                data['cpe_mac_address'], allow_empty=True)
            if cpe_error:
                return jsonify({'error': cpe_error}), 400
            duplicate = _check_cpe_mac_available(cpe_mac_address, customer.id)
            if duplicate:
                return jsonify({'error': duplicate}), 400
            customer.cpe_mac_address = cpe_mac_address
```

Add `'cpe_mac_address': c.cpe_mac_address,` to the customer list payload
(beside the existing `'onu_mac_address'` at ~line 2960) and
`'cpe_mac_address': customer.cpe_mac_address,` to the single-customer payload
(~line 3377).

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_cpe_linking_api.py -q -p no:warnings`
Expected: PASS

Run: `python -m pytest -q -p no:warnings`
Expected: PASS, 626 total

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_cpe_linking_api.py
git commit -m "feat: accept a CPE MAC on the customer endpoints"
```

---

## Task 3: `get_cpe_locations` — the connector

**Files:**
- Modify: `vsol_olt.py`
- Test: `tests/test_cpe_locations_connector.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `vsol_olt.get_cpe_locations(server) -> (ok, value)`. On success `value` is `{cpe_mac: {"pon_port": str, "onu_id": str, "onu_mac": str}}`, all MACs lowercase colon-form. On failure `value` is a human-readable string. Never raises. Tasks 4 and 5 consume this shape.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cpe_locations_connector.py`:

```python
"""get_cpe_locations joins the OLT's MAC-learning table to its interface
names, so a customer's own router identifies which ONU they sit behind.

The varbind shapes here were recorded from DeltaNet's real V-SOL V1600D on
2026-09-05: bridge port 10 is GE0/10, the uplink, carrying 186 of the 316
learned MACs and attributable to nobody. See
docs/superpowers/specs/2026-09-06-cpe-mac-linking-design.md.
"""
import types

import vsol_olt


ONUS = [
    {"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
     "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
     "model": "V2801D", "distance_m": 531},
    {"pon_port": "PON3", "onu_id": "EPON0/3:1", "status": "online",
     "mac_address": "4c:d7:c8:cc:4a:5c", "description": "AbirKerdy",
     "model": "V2801S", "distance_m": 619},
]

# {bridge port -> ifName}, exactly the shape ifName returns.
IFNAMES = {
    10: "GE0/10",          # the uplink
    19: "EPON0/3",         # a PON interface, not an ONU
    26: "EPON01ONU2 MoussaGhadir",
    41: "EPON03ONU1 AbirKerdy",
    99: "EPON07ONU4 Nobody",   # a real ONU port with no matching ONU row
}

FDB = {
    "00:0c:42:db:51:be": 10,   # uplink -- must be dropped
    "00:14:78:54:c5:4f": 10,   # uplink -- must be dropped
    "aa:bb:cc:00:00:01": 26,   # behind MoussaGhadir's ONU
    "aa:bb:cc:00:00:02": 26,   # a second device behind the same ONU
    "aa:bb:cc:00:00:03": 41,   # behind AbirKerdy's ONU
    "aa:bb:cc:00:00:04": 19,   # PON-level -- identifies a tree, not a leaf
    "aa:bb:cc:00:00:05": 99,   # ONU-shaped but no ONU row to join to
    "aa:bb:cc:00:00:06": 77,   # a port ifName never reported
}


def _patch(monkeypatch, fdb=None, ifnames=None, onus=None):
    monkeypatch.setattr(vsol_olt, "_walk_fdb_ports",
                        lambda *a, **k: FDB if fdb is None else fdb)
    monkeypatch.setattr(vsol_olt, "_walk_if_names",
                        lambda *a, **k: IFNAMES if ifnames is None else ifnames)
    monkeypatch.setattr(vsol_olt, "get_olt_status",
                        lambda s: (True, ONUS if onus is None else onus))


def _server():
    return types.SimpleNamespace(host="192.168.8.100", api_port=161,
                                 password="public", last_status=None)


def test_uplink_macs_are_excluded(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert "00:0c:42:db:51:be" not in value
    assert "00:14:78:54:c5:4f" not in value


def test_a_pon_level_port_is_excluded(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert "aa:bb:cc:00:00:04" not in value


def test_several_cpes_behind_one_onu_all_resolve(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert value["aa:bb:cc:00:00:01"] == {
        "pon_port": "PON1", "onu_id": "EPON0/1:2",
        "onu_mac": "b4:64:15:3f:c1:94"}
    assert value["aa:bb:cc:00:00:02"]["onu_mac"] == "b4:64:15:3f:c1:94"
    assert value["aa:bb:cc:00:00:03"]["onu_mac"] == "4c:d7:c8:cc:4a:5c"


def test_an_onu_port_with_no_matching_onu_row_is_skipped(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert "aa:bb:cc:00:00:05" not in value


def test_a_port_with_no_ifname_is_skipped(monkeypatch):
    _patch(monkeypatch)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok
    assert "aa:bb:cc:00:00:06" not in value


def test_an_onu_walk_failure_is_reported_not_raised(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(vsol_olt, "get_olt_status", lambda s: (False, "timeout"))
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok is False
    assert "timeout" in value


def test_an_snmp_failure_is_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise OSError("No SNMP response received before timeout")
    _patch(monkeypatch)
    monkeypatch.setattr(vsol_olt, "_walk_fdb_ports", boom)
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok is False
    assert "timeout" in value.lower()


def test_no_learned_macs_is_success_with_an_empty_map(monkeypatch):
    _patch(monkeypatch, fdb={})
    ok, value = vsol_olt.get_cpe_locations(_server())
    assert ok is True
    assert value == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_cpe_locations_connector.py -q -p no:warnings`
Expected: FAIL — `AttributeError: module 'vsol_olt' has no attribute 'get_cpe_locations'`

- [ ] **Step 3: Implement**

In `vsol_olt.py`, add near the existing OID constants:

```python
# dot1dTpFdbPort: {MAC -> bridge port}. The OLT's MAC-learning table -- every
# address it has seen behind each ONU, i.e. the customers' own routers.
FDB_PORT_OID = "1.3.6.1.2.1.17.4.3.1.2"
# ifName: {ifIndex -> name}. On this device the bridge port number IS the
# ifIndex, and the name reads "EPON01ONU2 MoussaGhadir" -- PON, ONU and the
# OLT's own label in one string. Verified against the real device: 54 of 54
# non-uplink FDB ports resolved this way.
IF_NAME_OID = "1.3.6.1.2.1.31.1.1.1.1"

# Only an interface of this exact shape identifies a single ONU. Anything
# else -- GE0/10 (the uplink, carrying 186 of 316 learned MACs), or a
# PON-level EPON0/3 -- names a trunk rather than a customer's ONU, and a MAC
# behind it cannot be attributed to anyone.
_ONU_IFNAME_RE = re.compile(r'^EPON(\d+)ONU(\d+)\b')
# The ONU inventory reports its own id as "EPON0/1:2" -- PON 1, ONU 2.
_ONU_ID_RE = re.compile(r'^EPON\d+/(\d+):(\d+)$')
```

Add `import re` to the imports if it is not already there.

Add one shared walk helper plus the two callers. `_walk_onu_table` (the
existing function, around line 86) is the model for the engine setup and the
error contract — read it and keep every detail the same:

```python
async def _walk_oid(host, port, community, oid):
    """Walk one OID subtree and return {index_suffix: value}, where the key is
    the OID text after `oid.`.

    Same engine setup, error contract and ceiling as _walk_onu_table: raises
    OltRejected on an SNMP errorStatus, OSError (via pysnmp's errorIndication)
    when the device never answers, and stops at _MAX_CELLS so a device that
    streams forever cannot hang the caller.
    """
    engine = SnmpEngine()
    try:
        target = await UdpTransportTarget.create(
            (host, port), timeout=_TIMEOUT_SECONDS, retries=_RETRIES,
        )
        prefix = oid + "."
        cells = {}
        async for err_indication, err_status, _err_index, var_binds in bulk_walk_cmd(
            engine,
            CommunityData(community, mpModel=1),  # SNMPv2c
            target,
            ContextData(),
            0, 25,
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
        ):
            if err_indication:
                raise OSError(str(err_indication))
            if err_status:
                raise OltRejected(err_status.prettyPrint())
            for name, value in var_binds:
                text = str(name.get_oid())
                if not text.startswith(prefix):
                    continue
                cells[text[len(prefix):]] = value.prettyPrint()
            if len(cells) >= _MAX_CELLS:
                logger.warning("VSOL OLT walk of %s hit the %s-cell ceiling at %s",
                               oid, _MAX_CELLS, host)
                break
        return cells
    finally:
        _safe_close(engine)


async def _walk_fdb_ports(host, port, community):
    """dot1dTpFdbPort -> {mac: bridge_port}.

    The OID index is the MAC as six decimal octets -- ...1.2.0.12.66.219.81.190
    means 00:0c:42:db:51:be. An index that is not six octets, or a value that
    is not an integer, is skipped rather than raising: this table is read from
    a device, not from something this code controls.
    """
    out = {}
    for index, value in (await _walk_oid(host, port, community, FDB_PORT_OID)).items():
        parts = index.split(".")
        if len(parts) != 6:
            continue
        try:
            mac = ":".join("%02x" % int(part) for part in parts)
            out[mac] = int(value)
        except (TypeError, ValueError):
            continue
    return out


async def _walk_if_names(host, port, community):
    """ifName -> {ifIndex: name}. A non-integer index is skipped."""
    out = {}
    for index, value in (await _walk_oid(host, port, community, IF_NAME_OID)).items():
        try:
            out[int(index)] = value
        except (TypeError, ValueError):
            continue
    return out
```

Then the public function. **Note two things the existing module does that a
first draft usually gets wrong:** it passes `server.password` straight through
(the `EncryptedString` column type has already decrypted it — there is no
`decrypt()` call anywhere in this file), and it resolves the port as
`server.api_port or DEFAULT_SNMP_PORT`.

```python
def get_cpe_locations(server):
    """Where the OLT last saw each customer device.

    Returns (True, {cpe_mac: {"pon_port", "onu_id", "onu_mac"}}) or
    (False, message). Never raises -- same contract as get_olt_status.

    Only ports whose ifName names a single ONU are kept. That exclusion is
    not a tidiness measure: on the real device 186 of 316 learned MACs sit on
    GE0/10, the uplink, and attributing those to customers would place a
    majority of them on hardware they are not behind.
    """
    ok, onus = get_olt_status(server)
    if not ok:
        return False, onus

    # (pon, onu) -> the ONU's own MAC and reported id
    by_position = {}
    for onu in onus:
        match = _ONU_ID_RE.match(onu.get("onu_id") or "")
        if match:
            by_position[(int(match.group(1)), int(match.group(2)))] = onu

    port = server.api_port or DEFAULT_SNMP_PORT
    try:
        fdb = asyncio.run(_walk_fdb_ports(server.host, port, server.password))
        names = asyncio.run(_walk_if_names(server.host, port, server.password))
    except OltRejected as exc:
        _mark_checked(server, "auth_failed")
        logger.warning("VSOL OLT rejected the FDB walk for device %s: %s",
                       server.id, exc)
        return False, "OLT rejected the SNMP request: {}".format(exc)
    except Exception as exc:  # noqa: BLE001 -- this module never raises out
        _mark_checked(server, "unreachable")
        # WARNING with exc_info, never exception/error: Sentry's
        # LoggingIntegration captures ERROR records WITH frame locals, and
        # these frames' locals include the SNMP community string. At WARNING
        # it becomes a breadcrumb, which carries none. Same reasoning, and
        # the same shape, as get_olt_status's own handler.
        logger.warning("VSOL OLT FDB walk failed for device %s: %s",
                       server.id, exc, exc_info=True)
        return False, str(exc) or exc.__class__.__name__

    located = {}
    for mac, bridge_port in fdb.items():
        name = names.get(bridge_port)
        if not name:
            continue
        match = _ONU_IFNAME_RE.match(name)
        if not match:
            continue          # the uplink, or a PON-level interface
        onu = by_position.get((int(match.group(1)), int(match.group(2))))
        if not onu:
            continue          # an ONU port with no row in the inventory
        located[mac] = {"pon_port": onu.get("pon_port"),
                        "onu_id": onu.get("onu_id"),
                        "onu_mac": onu.get("mac_address")}
    return True, located
```

Add `import re` to `vsol_olt.py`'s imports — it is not there today.

One consequence to be aware of, not a bug: `get_olt_status` returns
`(False, "OLT responded but reported no ONUs ...")` rather than an empty list
when the walk comes back empty, so `get_cpe_locations` inherits that failure
for an OLT with no ONUs at all. That is correct — with no ONU inventory there
is nothing to join CPEs to.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_cpe_locations_connector.py -q -p no:warnings`
Expected: PASS (8 tests)

Run: `python -m pytest -q -p no:warnings`
Expected: PASS, 634 total

- [ ] **Step 5: Commit**

```bash
git add vsol_olt.py tests/test_cpe_locations_connector.py
git commit -m "feat: read CPE locations from the OLT's MAC-learning table"
```

---

## Task 4: Relay the new operation through the agent

**Files:**
- Modify: `app.py` — `AGENT_OPERATIONS` (line 414), `_validate_agent_result` (line 9509)
- Modify: `agent/servicebills_agent.py` — `ALLOWED_OPERATIONS`, `execute_job`
- Test: `tests/test_network_agent_program.py`, `tests/test_network_agent_jobs.py`

**Interfaces:**
- Consumes: Task 3's `vsol_olt.get_cpe_locations`.
- Produces: `'cpe_locations'` is a valid agent operation end to end. Task 5 creates jobs with it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_network_agent_program.py`:

```python
def test_cpe_locations_dispatches_to_vsol(monkeypatch):
    seen = {}

    def fake(server):
        seen["host"] = server.host
        return True, {"aa:bb:cc:00:00:01": {"pon_port": "PON1",
                                            "onu_id": "EPON0/1:2",
                                            "onu_mac": "b4:64:15:3f:c1:94"}}

    monkeypatch.setattr(agent.vsol_olt, "get_cpe_locations", fake)
    ok, result, error, status = agent.execute_job(
        job(operation="cpe_locations"), CONFIG)
    assert ok is True and error is None
    assert seen["host"] == "192.168.8.100"
    assert result["aa:bb:cc:00:00:01"]["onu_mac"] == "b4:64:15:3f:c1:94"


def test_cpe_locations_is_in_the_agent_allowlist():
    assert "cpe_locations" in agent.ALLOWED_OPERATIONS
```

Check `job(...)` in that file accepts an `operation` keyword; if it does not,
follow whatever shape its existing callers use to set the operation.

Append to `tests/test_network_agent_jobs.py`:

```python
def test_cpe_locations_result_must_be_an_object(app, client):
    """The agent is outside the trust boundary; a malformed result must be
    refused before it is stored, not discovered by a consumer later."""
    assert appmod._validate_agent_result('cpe_locations', []) is not None
    assert appmod._validate_agent_result('cpe_locations', 'nope') is not None
    assert appmod._validate_agent_result(
        'cpe_locations', {'aa:bb:cc:00:00:01': {'onu_mac': 'b4:64:15:3f:c1:94'}}) is None
    assert appmod._validate_agent_result(
        'cpe_locations', {'aa:bb:cc:00:00:01': 'not-an-object'}) is not None
    assert appmod._validate_agent_result(
        'cpe_locations', {'aa:bb:cc:00:00:01': {'onu_mac': 42}}) is not None
    assert appmod._validate_agent_result('cpe_locations', {}) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_network_agent_program.py tests/test_network_agent_jobs.py -q -p no:warnings -k cpe`
Expected: FAIL — the operation is refused by `validate_job`, and the validator returns `None` for an unknown operation.

- [ ] **Step 3: Implement**

In `app.py`, extend `AGENT_OPERATIONS`:

```python
AGENT_OPERATIONS = (
    'test_connection', 'device_health', 'secret_status',
    'active_session', 'olt_status', 'cpe_locations',
)
```

In `_validate_agent_result`, add before the final return:

```python
    if operation == 'cpe_locations':
        if not isinstance(result, dict):
            return 'result must be an object mapping CPE MAC to its ONU'
        for mac, entry in result.items():
            if not isinstance(entry, dict):
                return 'every CPE entry must be an object'
            onu_mac = entry.get('onu_mac')
            if not isinstance(onu_mac, str) or not onu_mac:
                return 'every CPE entry must have a non-empty string onu_mac'
```

Update that function's docstring: it currently says only two operations have a
contract worth enforcing. Three do now.

In `agent/servicebills_agent.py`, add `"cpe_locations"` to `ALLOWED_OPERATIONS`
— keeping the comment above it accurate, since it explains that the tuple is
read-only by design — and add the dispatch branch in `execute_job`:

```python
        elif operation == "cpe_locations":
            ok, value = vsol_olt.get_cpe_locations(server)
```

Place it immediately after the `olt_status` branch, so the two SNMP operations
sit together. The final `else` comment currently reads "active_session -- the
only remaining allowed operation"; it still is, so leave it.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_network_agent_program.py tests/test_network_agent_jobs.py -q -p no:warnings`
Expected: PASS

Run: `python -m pytest -q -p no:warnings`
Expected: PASS, 641 total

- [ ] **Step 5: Commit**

```bash
git add app.py agent/servicebills_agent.py tests/test_network_agent_program.py tests/test_network_agent_jobs.py
git commit -m "feat: relay cpe_locations through the on-prem agent"
```

---

## Task 5: The Locate endpoints

**Files:**
- Modify: `app.py` — near the label-matcher endpoints (~line 10048)
- Test: `tests/test_cpe_linking_api.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  `POST /api/network-tree/olt/<id>/locate-customers` → `{ok, job_id, message}`, same shape as the OLT refresh.
  `POST /api/network-tree/olt/<id>/locate-customers/apply` with `{"job_id": N}` → `{located, moved, unmatched}`.
  Both `admin_or_finance_required`. Task 6 calls them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cpe_linking_api.py`:

```python
import types

ONUS = [
    {"pon_port": "PON1", "onu_id": "EPON0/1:2", "status": "online",
     "mac_address": "b4:64:15:3f:c1:94", "description": "MoussaGhadir",
     "model": "V2801D", "distance_m": 531},
]
LOCATIONS = {"aa:bb:cc:00:00:01": {"pon_port": "PON1", "onu_id": "EPON0/1:2",
                                   "onu_mac": "b4:64:15:3f:c1:94"}}


def _olt(client, hdr):
    ccr = client.post("/api/network-devices", headers=hdr, json={
        "name": "Core CCR", "host": "10.0.0.1", "username": "admin",
        "password": "secret", "device_type": "mikrotik_ccr"}).get_json()["device"]
    return client.post("/api/network-devices", headers=hdr, json={
        "name": "EPON OLT", "host": "192.168.8.100", "password": "public",
        "device_type": "vsol_olt", "parent_device_id": ccr["id"],
    }).get_json()["device"]


def _locate_and_apply(client, hdr, olt_id):
    started = client.post(f"/api/network-tree/olt/{olt_id}/locate-customers",
                          headers=hdr).get_json()
    assert started["ok"] is True, started
    return client.post(f"/api/network-tree/olt/{olt_id}/locate-customers/apply",
                       headers=hdr, json={"job_id": started["job_id"]})


def test_locate_places_a_customer_behind_the_onu_holding_their_cpe(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc A", "loc_a_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "Moussa",
                    cpe_mac_address="aa:bb:cc:00:00:01").get_json()["customer_id"]
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))

    body = _locate_and_apply(client, hdr, olt["id"]).get_json()
    assert body["located"] == 1
    with app.app_context():
        customer = appmod.Customer.query.get(cid)
        assert customer.onu_mac_address == "b4:64:15:3f:c1:94"
        assert customer.onu_last_seen_at is not None


def test_a_cpe_matching_no_customer_is_ignored(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc B", "loc_b_admin")
    olt = _olt(client, hdr)
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))
    body = _locate_and_apply(client, hdr, olt["id"]).get_json()
    assert body["located"] == 0
    assert body["unmatched"] == 1


def test_a_customer_whose_cpe_was_not_seen_is_left_completely_alone(app, client, monkeypatch):
    """This is the memory. Their previous placement AND its timestamp stand."""
    hdr = make_tenant(client, "Loc C", "loc_c_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "Absent",
                    cpe_mac_address="aa:bb:cc:99:99:99",
                    onu_mac_address="f4:c4:d6:4d:80:e1").get_json()["customer_id"]
    with app.app_context():
        appmod.Customer.query.get(cid).onu_last_seen_at = appmod.datetime(2026, 1, 1)
        appmod.db.session.commit()
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))

    _locate_and_apply(client, hdr, olt["id"])
    with app.app_context():
        customer = appmod.Customer.query.get(cid)
        assert customer.onu_mac_address == "f4:c4:d6:4d:80:e1"
        assert customer.onu_last_seen_at == appmod.datetime(2026, 1, 1)


def test_a_moved_cpe_updates_the_placement(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc D", "loc_d_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "Mover",
                    cpe_mac_address="aa:bb:cc:00:00:01",
                    onu_mac_address="f4:c4:d6:4d:80:e1").get_json()["customer_id"]
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))

    body = _locate_and_apply(client, hdr, olt["id"]).get_json()
    assert body["moved"] == 1
    with app.app_context():
        assert appmod.Customer.query.get(cid).onu_mac_address == "b4:64:15:3f:c1:94"


def test_a_cpe_recorded_with_hyphens_still_matches(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc E", "loc_e_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    cid = _customer(client, hdr, plan_id, "Hyphen",
                    cpe_mac_address="AA-BB-CC-00-00-01").get_json()["customer_id"]
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))
    _locate_and_apply(client, hdr, olt["id"])
    with app.app_context():
        assert appmod.Customer.query.get(cid).onu_mac_address == "b4:64:15:3f:c1:94"


def test_applying_the_same_result_twice_is_harmless(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc F", "loc_f_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    _customer(client, hdr, plan_id, "Twice", cpe_mac_address="aa:bb:cc:00:00:01")
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))
    _locate_and_apply(client, hdr, olt["id"])
    body = _locate_and_apply(client, hdr, olt["id"]).get_json()
    assert body["located"] == 1
    assert body["moved"] == 0


def test_a_malformed_entry_is_skipped_not_raised(app, client, monkeypatch):
    hdr = make_tenant(client, "Loc G", "loc_g_admin")
    olt = _olt(client, hdr)
    plan_id = _plan(client, hdr)
    _customer(client, hdr, plan_id, "Ok", cpe_mac_address="aa:bb:cc:00:00:01")
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, dict(LOCATIONS)))
    started = client.post(f"/api/network-tree/olt/{olt['id']}/locate-customers",
                          headers=hdr).get_json()
    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, started["job_id"])
        job.result = {"aa:bb:cc:00:00:01": {"onu_mac": "b4:64:15:3f:c1:94"},
                      "bad": "not-an-object", "worse": {"onu_mac": None}}
        appmod.db.session.commit()
    resp = client.post(f"/api/network-tree/olt/{olt['id']}/locate-customers/apply",
                       headers=hdr, json={"job_id": started["job_id"]})
    assert resp.status_code == 200
    assert resp.get_json()["located"] == 1


def test_locate_on_a_non_olt_is_rejected(app, client):
    hdr = make_tenant(client, "Loc H", "loc_h_admin")
    ccr = client.post("/api/network-devices", headers=hdr, json={
        "name": "Core CCR", "host": "10.0.0.1", "username": "admin",
        "password": "secret", "device_type": "mikrotik_ccr"}).get_json()["device"]
    assert client.post(f"/api/network-tree/olt/{ccr['id']}/locate-customers",
                       headers=hdr).status_code == 400


def test_employee_and_collector_cannot_locate_or_apply(app, client):
    """Reading the tree is theirs; rewriting who lives where is not."""
    admin_hdr = make_tenant(client, "Loc I", "loc_i_admin")
    olt = _olt(client, admin_hdr)
    for username, role in (("loc_i_emp", "employee"), ("loc_i_col", "collector")):
        client.post("/api/users", headers=admin_hdr,
                    json={"username": username, "password": "pw", "role": role})
        token = client.post("/api/login", json={"username": username,
                                                "password": "pw"}).get_json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}
        assert client.post(f"/api/network-tree/olt/{olt['id']}/locate-customers",
                           headers=hdr).status_code == 403, role
        assert client.post(f"/api/network-tree/olt/{olt['id']}/locate-customers/apply",
                           headers=hdr, json={"job_id": 1}).status_code == 403, role


def test_apply_is_tenant_scoped(app, client, monkeypatch):
    hdr_one = make_tenant(client, "Loc J1", "loc_j1_admin")
    olt_one = _olt(client, hdr_one)
    monkeypatch.setattr(appmod.vsol_olt, "get_cpe_locations", lambda s: (True, LOCATIONS))
    started = client.post(f"/api/network-tree/olt/{olt_one['id']}/locate-customers",
                          headers=hdr_one).get_json()

    hdr_two = make_tenant(client, "Loc J2", "loc_j2_admin")
    olt_two = _olt(client, hdr_two)
    resp = client.post(f"/api/network-tree/olt/{olt_two['id']}/locate-customers/apply",
                       headers=hdr_two, json={"job_id": started["job_id"]})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cpe_linking_api.py -q -p no:warnings -k "locate or cpe_matching or moved or twice or malformed or employee or scoped"`
Expected: FAIL with 404 — the routes do not exist.

- [ ] **Step 3: Implement the apply helper**

In `app.py`, above the endpoints:

```python
def _apply_cpe_locations(result):
    """Write each located customer's remembered ONU. Returns counts.

    Deliberately never clears: a customer whose CPE was not in this result is
    left completely alone, placement and timestamp both. That is what makes
    this a memory rather than a live view -- a router switched off must not
    erase where we know its owner lives.

    Defensive about `result`'s shape for the same reason
    _resolve_onu_customers is: in agent mode this is whatever JSON the on-prem
    agent posted. _validate_agent_result rejects the worst shapes at the
    boundary, but rows stored before that check existed can still be odd, and
    a malformed entry must be skipped rather than abort the whole apply.
    """
    if not isinstance(result, dict):
        return {'located': 0, 'moved': 0, 'unmatched': 0}

    by_cpe = {}
    for customer in tenant_query(Customer).filter(
            Customer.cpe_mac_address.isnot(None)).all():
        by_cpe[_normalize_mac(customer.cpe_mac_address)] = customer

    located = moved = unmatched = 0
    now = datetime.utcnow()
    for raw_mac, entry in result.items():
        if not isinstance(entry, dict):
            continue
        onu_mac = entry.get('onu_mac')
        if not isinstance(onu_mac, str) or not onu_mac:
            continue
        customer = by_cpe.get(_normalize_mac(raw_mac))
        if customer is None:
            unmatched += 1
            continue
        if _normalize_mac(customer.onu_mac_address) != _normalize_mac(onu_mac):
            moved += 1
        customer.onu_mac_address = onu_mac
        customer.onu_last_seen_at = now
        located += 1
    db.session.commit()
    return {'located': located, 'moved': moved, 'unmatched': unmatched}
```

- [ ] **Step 4: Implement the endpoints**

Model both on the existing OLT refresh and label-matcher routes — read
`refresh_olt_onus` and `apply_onu_label_matches` and follow their structure for
the device lookup, the `device_type != 'vsol_olt'` rejection, and
`_create_device_job`'s `(job, error)` return:

```python
@app.route('/api/network-tree/olt/<int:device_id>/locate-customers', methods=['POST'])
@jwt_required()
@admin_or_finance_required()
def locate_customers(device_id):
    """Start a CPE-location walk. Writes nothing -- the apply step does that,
    so the agent (which may run the walk) never touches customer records."""
    # device lookup + vsol_olt guard, exactly as refresh_olt_onus does
    job, error = _create_device_job(device, 'cpe_locations')
    if error:
        return jsonify({'ok': False, 'message': error, 'job_id': None}), 200
    return jsonify({'ok': True, 'message': None, 'job_id': job.id}), 200


@app.route('/api/network-tree/olt/<int:device_id>/locate-customers/apply',
           methods=['POST'])
@jwt_required()
@admin_or_finance_required()
def apply_customer_locations(device_id):
    # device lookup + vsol_olt guard, as above
    job_id = (request.json or {}).get('job_id')
    job = tenant_query(NetworkAgentJob).filter_by(
        id=job_id, device_id=device.id, operation='cpe_locations').first()
    if not job:
        return jsonify({'message': 'Job not found'}), 404
    if job.status != 'done' or job.error:
        return jsonify({'error': job.error or 'The locate is still running.'}), 400
    return jsonify(_apply_cpe_locations(job.result)), 200
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_cpe_linking_api.py -q -p no:warnings`
Expected: PASS

Run: `python -m pytest -q -p no:warnings`
Expected: PASS, 651 total

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_cpe_linking_api.py
git commit -m "feat: locate customers by their CPE MAC and remember where"
```

---

## Task 6: Timestamps in the viewer's own timezone

**Files:**
- Create: `frontend/src/components/formatStamp.js`, `frontend/src/components/formatStamp.test.js`
- Modify: `frontend/src/components/NetworkTreeView.js`, `frontend/src/components/NetworkDeviceManagementView.js`

**Interfaces:**
- Consumes: nothing.
- Produces: `formatStamp(stamp) -> string` and `parseUtc(stamp) -> number|NaN`, both exported. Task 7 uses `formatStamp` for the "last seen" marker.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/formatStamp.test.js`:

```js
import { formatStamp, parseUtc } from './formatStamp';

test('parses the API stamp as UTC, not as local time', () => {
    // The backend emits datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S').
    expect(parseUtc('2026-09-06 11:31:30')).toBe(Date.UTC(2026, 8, 6, 11, 31, 30));
});

test('renders a UTC stamp in the viewer local zone', () => {
    // Whatever zone the test runs in, the rendered value must be the local
    // rendering of that UTC instant -- this is the bug being fixed: the UI
    // used to print the UTC string verbatim, reading hours behind.
    const expected = new Date(Date.UTC(2026, 8, 6, 11, 31, 30)).toLocaleString();
    expect(formatStamp('2026-09-06 11:31:30')).toBe(expected);
});

test('an empty or missing stamp renders as an em dash, never "Invalid Date"', () => {
    expect(formatStamp(null)).toBe('—');
    expect(formatStamp('')).toBe('—');
    expect(formatStamp(undefined)).toBe('—');
});

test('an unparseable stamp renders as an em dash', () => {
    expect(formatStamp('not a date')).toBe('—');
    expect(Number.isNaN(parseUtc('not a date'))).toBe(true);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/formatStamp.test.js"`
Expected: FAIL — `Cannot find module './formatStamp'`

- [ ] **Step 3: Implement**

Create `frontend/src/components/formatStamp.js`:

```js
/**
 * The API emits every timestamp as UTC, formatted
 * `datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')` — no zone marker at all.
 * Printed verbatim, that reads hours behind for anyone not on UTC: measured
 * 2026-09-06, the agent's "last seen" showed 11:31 to a user whose clock said
 * 14:31. Appending 'Z' before parsing is the one missing step, and it is
 * exactly what describeAge already does for relative labels.
 */
export function parseUtc(stamp) {
    if (!stamp) return NaN;
    return Date.parse(String(stamp).replace(' ', 'T') + 'Z');
}

/** A UTC API stamp rendered in the viewer's own timezone. */
export function formatStamp(stamp) {
    const ms = parseUtc(stamp);
    if (Number.isNaN(ms)) return '—';
    return new Date(ms).toLocaleString();
}
```

- [ ] **Step 4: Apply it to every absolute stamp on the network pages**

In `NetworkTreeView.js`: import `formatStamp`, and replace the raw
`agent.last_seen_at` interpolations at the agent chip (~line 580) and in
`agentOfflineReason` (~line 245) with `formatStamp(agent.last_seen_at)`.
Have `describeAge`/`isStale` import `parseUtc` from the new module and delete
their local copy, so there is one parser.

In `NetworkDeviceManagementView.js`: import `formatStamp` and do the same at
its agent chip (~line 243) and `agentOfflineReason` (~line 89). Also check that
file for any `last_checked_at` rendered as an absolute date and convert it.

- [ ] **Step 5: Run the tests and build**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/formatStamp.test.js"`
Expected: PASS (4 tests)

Run each of the four existing frontend suites with its own `--testMatch`:
`buildTopologyTree.test.js` (20), `NetworkTreeView.toggleExpansion.test.js` (11),
`NetworkTreeView.describeAge.test.js` (10), `filterTopologyTree.test.js` (5).
Expected: all pass.

Run: `cd frontend && npx react-scripts build`
Expected: succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/formatStamp.js frontend/src/components/formatStamp.test.js frontend/src/components/NetworkTreeView.js frontend/src/components/NetworkDeviceManagementView.js
git commit -m "fix: show network timestamps in the viewer's timezone, not UTC"
```

---

## Task 7: The CPE field, the Locate button, and the remembered marker

**Files:**
- Modify: `frontend/src/components/SubscriptionsView.js`, `frontend/src/components/NetworkTreeView.js`, `frontend/src/components/buildTopologyTree.js`, `frontend/src/context/AppContext.js`
- Test: `frontend/src/components/buildTopologyTree.test.js`

**Interfaces:**
- Consumes: Task 5's endpoints, Task 6's `formatStamp`.
- Produces: no new exports.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/buildTopologyTree.test.js`:

```js
test('a customer not seen in the last locate is marked as remembered', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        lastLocateAt: '2026-09-06 12:00:00',
        last_result: [onu({ customers: [
            { id: 1, name: 'Seen', is_subscription_active: true,
              onu_mac_address: 'b4:64:15:3f:c1:94',
              onu_last_seen_at: '2026-09-06 12:00:00' },
            { id: 2, name: 'Remembered', is_subscription_active: true,
              onu_mac_address: 'b4:64:15:3f:c1:94',
              onu_last_seen_at: '2026-09-04 09:00:00' },
            { id: 3, name: 'Never', is_subscription_active: true,
              onu_mac_address: 'b4:64:15:3f:c1:94', onu_last_seen_at: null },
        ] })],
    })] })]);
    const customers = find(tree, 'onu').children;
    expect(customers[0].meta).toBe('');
    expect(customers[1].meta).toMatch(/^last seen /);
    expect(customers[2].meta).toBe('');
});
```

`ccr`/`olt`/`onu`/`find` are the existing helpers at the top of that file; add
`lastLocateAt` to the `olt` helper's defaults as `null`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/buildTopologyTree.test.js"`
Expected: FAIL — `customers[1].meta` is `''`.

- [ ] **Step 3: Mark remembered placements**

In `buildTopologyTree.js`, thread the OLT's last-locate stamp down to
`customerNode` and set `meta` when the customer's own stamp is older:

```js
function customerNode(customer, ponKey, index, seenIds, lastLocateAt) {
    // A customer located in the most recent run renders plainly. One whose
    // CPE was not seen still shows under their last known ONU -- that is the
    // memory -- but says so, because during an outage "here" and "here as of
    // Thursday" are very different facts.
    const seen = parseUtc(customer.onu_last_seen_at);
    const run = parseUtc(lastLocateAt);
    const remembered = !Number.isNaN(seen) && !Number.isNaN(run) && seen < run;
    // ... meta: remembered ? `last seen ${formatStamp(customer.onu_last_seen_at)}` : ''
}
```

Import `parseUtc` and `formatStamp` from `./formatStamp`. A customer with no
stamp at all shows nothing — they have never been located, which is different
from having been located and since gone quiet.

The device node needs `lastLocateAt` from the API. Add it to the tree
endpoint's device payload in `app.py`, alongside `last_result_at`: the
`finished_at` of the newest completed `cpe_locations` job for that device.
`_latest_results_by_device` already finds a newest job per device — add a
second, similarly-shaped lookup restricted to `operation == 'cpe_locations'`
rather than widening the existing one, so the cached-result filter keeps its
current meaning.

- [ ] **Step 4: Add the CPE field and the Locate button**

In `SubscriptionsView.js`, beside each of the two existing
`ONU MAC Address (Optional)` fields (create dialog ~line 960, edit dialog
~line 1334), add:

```jsx
                            <TextField fullWidth label="CPE MAC Address (router, optional)"
                                value={newCustomer.cpe_mac_address || ''}
                                onChange={(e) => setNewCustomer({ ...newCustomer, cpe_mac_address: e.target.value })}
                                helperText="The customer's own router, as the OLT sees it. Used to place them on the network map." />
```

and the `editingCustomer` equivalent. Add `cpe_mac_address: ''` to the
`setNewCustomer` reset object (~line 759) and to the initial state (~line 225).
**Follow the existing `onu_mac_address` snapshot pattern in the edit dialog**
(~lines 241, 688, 713–721): the payload includes the field only when the user
actually changed it, which is what stops a stale form value clobbering a
concurrent edit.

In `AppContext.js`, beside the other network calls:

```js
    locateCustomers: (id) => api.post(`/network-tree/olt/${id}/locate-customers`),
    applyCustomerLocations: (id, jobId) =>
        api.post(`/network-tree/olt/${id}/locate-customers/apply`, { job_id: jobId }),
```

In `NetworkTreeView.js`, add *Locate Customers* to `deviceActions` beside
*Match Labels* — same `isOlt && canEditLinks` gate, same `agentOffline`
disabling and tooltip. On click: call `locateCustomers`, poll with
`pollNetworkJob`, then call `applyCustomerLocations`, then `loadTree(false)` to
pull the new placements. Report the result through the existing snackbar:
`Located 42 customers (3 moved). 12 devices matched no customer.`

Wrap it in the same per-device sequence guard and `refreshingIds` handling
`refreshOlt` uses — read that function and follow it exactly.

- [ ] **Step 5: Run everything**

Run all five frontend suites with their own `--testMatch`: `formatStamp` (4),
`buildTopologyTree` (21), `NetworkTreeView.toggleExpansion` (11),
`NetworkTreeView.describeAge` (10), `filterTopologyTree` (5).
Expected: all pass.

Run: `cd frontend && npx react-scripts build`
Expected: succeeds.

Run: `python -m pytest -q -p no:warnings`
Expected: PASS, 651 total.

- [ ] **Step 6: Commit**

```bash
git add frontend/src app.py
git commit -m "feat: record a CPE MAC, locate customers, mark remembered placements"
```

---

## Self-review notes

**Spec coverage.** Data model → Task 1. Field and uniqueness → Task 2. Connector
and the uplink exclusion → Task 3. Relay and `_validate_agent_result` → Task 4.
The two endpoints, apply semantics and authorization → Task 5. The timestamp
fix → Task 6. The field, the button and the remembered marker → Task 7.

**Deliberately not built** (spec decision 3): any bulk assignment UI. Staff fill
the field in as they go.

**Carried into the plan from the spec's Risks:** a customer whose router has
been off since before the first locate shows as unlinked rather than
remembered, and nothing here can infer otherwise; and Locate being
admin/finance only may exclude the field staff most likely to know a router's
MAC — widening it is a one-line change but deserves its own decision.
