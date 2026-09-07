# Mikrotik Device Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a router one database row instead of two, and wire up the three agent operations that have never been reachable from the cloud.

**Architecture:** `MikrotikServer` is deleted and its one unique column (`service_name`) moves onto `NetworkDevice`. `Customer.mikrotik_server_id` is renamed to `network_device_id`. The three dead read operations (`test_connection`, `secret_status`, `active_session`) become reachable through the existing `_create_device_job` factory, so they work in direct mode and through the on-prem agent identically. Writes (`set_secret_enabled`) stay inline and refuse cleanly in agent mode.

**Tech Stack:** Flask, SQLAlchemy, hand-written Alembic, React 18 + MUI (CRA), pytest, Jest.

## Global Constraints

- **CLOUD-ONLY. `agent/servicebills_agent.py`, `mikrotik.py` and `vsol_olt.py` must not change.** Every operation needed already exists in the shipped agent 1.2.0 — `ALLOWED_OPERATIONS` lists all six and `execute_job` dispatches all six. Changing the agent would force the owner to hand-copy three files onto the on-prem box and restart it, and the connector-fingerprint check would report their agent stale until they did. If a task seems to need an agent change, the task is wrong.
- Baseline to beat: **684 backend tests, 61 frontend tests.** Both suites must be green at every commit.
- Backend tests: `python -m pytest -q` from the worktree root.
- Frontend tests MUST use an explicit pattern: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/<file>.test.js"`. The plain command reports "No tests found" because of the dot-prefixed `.worktrees` path segment, which is indistinguishable from passing.
- Frontend build check: `cd frontend && npx react-scripts build` **without** `CI=true` (that is what the Dockerfile runs; `CI=true` fails on ~30 pre-existing warnings in unrelated files).
- **Never** `logger.exception` or `exc_info=True` on any path whose frame locals can hold a device credential — Sentry's `LoggingIntegration` captures frame locals at ERROR. This explicitly includes the migration, which copies encrypted device passwords.
- Migration tests bootstrap with `create_all` + `stamp` and then drive `upgrade`/`downgrade`/`upgrade` directly. Never walk the chain from base — origin's `bd054e2e7cf9` calls `op.create_unique_constraint` outside batch mode, which SQLite rejects. Copy the shape from the four existing tests in `tests/test_topology_migration.py`.
- Current Alembic head is `d9e2b7c4a815`. The new revision must be the only new head.
- Do **not** use `git stash` — the stash stack is shared across worktrees. Use a WIP commit if you must set work aside.
- Nothing under `frontend/build/` or `build/` may be committed. If a build was run, revert those paths before committing: `git checkout -- frontend/build build && git clean -fdq frontend/build build`.
- `NETWORK_DEVICE_TYPES` stays exactly `('mikrotik_ccr', 'vsol_olt')`. Do not add a device type.
- Known pre-existing test-isolation bug: running `tests/test_topology_migration.py` **before** `tests/test_network_agent_program.py` breaks 5 tests in the latter (Alembic's `fileConfig(disable_existing_loggers=True)` disables the already-imported `servicebills_agent` logger). The full suite's alphabetical order is safe. Do not fix it in this cycle; do not be alarmed by it when running a subset.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `migrations/versions/f2b6c9d4e703_consolidate_mikrotik_server_into_network_device.py` | **Create.** Schema move, defensive copy-forward. | 1 |
| `app.py` | Model, endpoints, job factory. Large existing file; follow its patterns, do not restructure. | 1–4 |
| `tests/test_topology_migration.py` | **Append** one migration test covering both branches. | 1 |
| `tests/test_mikrotik_consolidation.py` | **Create.** Model, endpoint, guard and relay tests for this cycle. | 1–4 |
| `tests/test_lifecycle.py` | **Modify.** Remove `MikrotikServer` from the known-gaps set. | 1 |
| `tests/test_phase1_hotfixes.py` | **Modify.** Repoint a 403 test off a deleted endpoint. | 1 |
| `tests/test_network_device_model.py` | **Modify.** Docstring only. | 1 |
| `frontend/src/context/AppContext.js` | API method definitions. | 5, 6 |
| `frontend/src/App.js` | Nav entries. | 5 |
| `frontend/src/components/MikrotikServerManagementView.js` | **Delete.** | 5 |
| `frontend/src/components/NetworkDeviceManagementView.js` | Service name field, Test connection action. | 5 |
| `frontend/src/components/SubscriptionsView.js` | Customer device dropdown, status panel. | 6 |

---

### Task 1: One router model

Delete `MikrotikServer`, move `service_name` onto `NetworkDevice`, rename the customer FK, and migrate. Behaviour is otherwise unchanged — every call site keeps doing exactly what it did, just against `NetworkDevice`. This task is deliberately atomic: a model cannot be half-deleted.

**Files:**
- Create: `migrations/versions/f2b6c9d4e703_consolidate_mikrotik_server_into_network_device.py`
- Create: `tests/test_mikrotik_consolidation.py`
- Modify: `app.py` (model ~310-352, ~384-410, 646, 1486, 3016-3040, 3113, 3227, 3273, 3508-3529, 3623, 4343-4370, 9047-9145, 9193-9296, 9297-9315, 10595-10650)
- Modify: `tests/test_topology_migration.py` (append)
- Modify: `tests/test_lifecycle.py:115`, `tests/test_phase1_hotfixes.py:200-204`, `tests/test_network_device_model.py:2`

**Interfaces:**
- Produces: `NetworkDevice.service_name` (`str | None`); `NetworkDevice.customers` (backref `network_device`); `Customer.network_device_id` (`int | None`); `_customer_network_context(customer_id) -> (customer, device, err)` where `err` is `None` or `(payload_dict, status_code)`; Alembic revision `f2b6c9d4e703`.
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing model test**

Create `tests/test_mikrotik_consolidation.py`:

```python
"""One router model: MikrotikServer is gone and NetworkDevice carries its job.

See docs/superpowers/specs/2026-09-07-mikrotik-device-consolidation-design.md.
"""
import app as appmod
from tests.conftest import make_tenant


def _tenant(name):
    return appmod.Tenant.query.filter_by(name=name).first()


def make_device(app, tenant_name, **over):
    """A mikrotik_ccr device in the named tenant. Returns its id."""
    with app.app_context():
        tenant = _tenant(tenant_name)
        fields = dict(name="CCR", host="192.168.100.1", api_port=8728,
                      username="admin", password="pw", device_type="mikrotik_ccr")
        fields.update(over)
        device = appmod.NetworkDevice(tenant_id=tenant.id, **fields)
        appmod.db.session.add(device)
        appmod.db.session.commit()
        return device.id


def test_the_mikrotik_server_model_is_gone():
    """Its whole point was being a second, near-identical router table."""
    assert not hasattr(appmod, "MikrotikServer")


def test_network_device_carries_service_name(app, client):
    make_tenant(client, "Cons A", "cons_a_admin")
    device_id = make_device(app, "Cons A", service_name="BCH")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert device.service_name == "BCH"
        assert device.to_dict()["service_name"] == "BCH"


def test_service_name_is_optional(app, client):
    """Only a router running more than one PPPoE server instance needs it."""
    make_tenant(client, "Cons B", "cons_b_admin")
    device_id = make_device(app, "Cons B")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert device.service_name is None
        assert device.to_dict()["service_name"] is None


def test_customers_link_to_a_network_device(app, client):
    make_tenant(client, "Cons C", "cons_c_admin")
    device_id = make_device(app, "Cons C")
    with app.app_context():
        tenant = _tenant("Cons C")
        customer = appmod.Customer(
            tenant_id=tenant.id, name="Bach", network_device_id=device_id,
            pppoe_username="bach1")
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        assert [c.id for c in device.customers] == [customer.id]
        assert customer.network_device.id == device_id
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_mikrotik_consolidation.py -q`
Expected: FAIL — `test_the_mikrotik_server_model_is_gone` asserts False, and the others raise `TypeError: 'network_device_id' is an invalid keyword argument for Customer`.

- [ ] **Step 3: Move `service_name` onto `NetworkDevice`**

In `app.py`, inside `class NetworkDevice`, immediately after the `use_tls` column:

```python
    # Only needed when this router runs more than one PPPoE server instance
    # (DeltaNet's CCR runs four: BCH enabled, BCHVPN/MYISP/SMN-W disabled), or
    # when its network is shared with another ISP. Blank means "match by
    # /ppp/secret name only", which is correct for a single-instance router --
    # see mikrotik._secret_where().
    service_name = db.Column(db.String(100), nullable=True)
```

Add the backref that used to live on `MikrotikServer`, next to the existing `children` relationship:

```python
    customers = db.relationship('Customer', backref='network_device', lazy=True)
```

Add to `NetworkDevice.to_dict()`, after `'use_tls'`:

```python
            'service_name': self.service_name,
```

- [ ] **Step 4: Delete the `MikrotikServer` model**

Delete the whole `class MikrotikServer(db.Model):` block from `app.py` (~line 310 through its `to_dict`, ~line 352), including its `customers = db.relationship(...)` backref at line 338.

Remove `MikrotikServer` from the import list at `app.py:1486`:

```python
    UpstreamProvider, UpstreamProviderPayment,
```

- [ ] **Step 5: Rename the customer FK**

`app.py:646`:

```python
    network_device_id = db.Column(db.Integer, db.ForeignKey('network_device.id'),
                                  nullable=True, index=True)
```

- [ ] **Step 6: Run the model test**

Run: `python -m pytest tests/test_mikrotik_consolidation.py -q`
Expected: 4 passed. (`app.py` still references `MikrotikServer` in endpoints — those are `NameError`s at request time, not import time, so this passes. Steps 7-11 fix them.)

- [ ] **Step 7: Delete the five MikrotikServer endpoints**

Delete these whole functions from `app.py` (~9047-9145), including their decorators:

- `get_mikrotik_servers`
- `create_mikrotik_server`
- `update_mikrotik_server`
- `delete_mikrotik_server`
- `test_mikrotik_connection`

Do **not** delete `NETWORK_DEVICE_TYPES` or `_default_device_api_port`, which follow immediately after.

- [ ] **Step 8: Carry the linked-customer delete guard onto devices**

`delete_mikrotik_server` refused to delete a server with customers linked; `delete_network_device` has no such guard. Now that customers point at devices, deleting one would raise a Postgres `ForeignKeyViolation` that surfaces as a 500. In `delete_network_device`, immediately after the `if not device:` check:

```python
        # Carried over from the deleted delete_mikrotik_server: customers now
        # link to devices, and Postgres would reject the DELETE with a
        # ForeignKeyViolation that the bare `except` below turns into an
        # opaque 500. A named 400 tells the user what to actually do.
        linked = tenant_query(Customer).filter_by(network_device_id=device.id).first()
        if linked:
            return jsonify({'error': 'Cannot delete a device with customers linked '
                                     'to it. Unlink them first.'}), 400
```

- [ ] **Step 9: Accept `service_name` on device create and update**

In `create_network_device`, add to the `NetworkDevice(...)` constructor call:

```python
            service_name=data.get('service_name') or None,
```

In `update_network_device`, next to the `status` handling:

```python
        if 'service_name' in data:
            device.service_name = data['service_name'] or None
```

- [ ] **Step 10: Repoint the customer link plumbing**

`_check_network_link_conflict` (`app.py:3016`) — rename the parameter and the filter. The message stays customer-facing and accurate:

```python
def _check_network_link_conflict(exclude_customer_id, network_device_id, pppoe_username,
                                  upstream_provider_id, upstream_username):
```

```python
    if network_device_id and pppoe_username:
        q = tenant_query(Customer).filter_by(
            network_device_id=network_device_id, pppoe_username=pppoe_username)
```

Update both call sites (`app.py:3227` and `app.py:3508-3518`) and both customer payloads (`app.py:3113` and `app.py:3623`) to read `network_device_id` instead of `mikrotik_server_id`, and the create at `app.py:3273`. In `update_customer`, the local variable becomes `effective_network_device_id`:

```python
        effective_network_device_id = (data['network_device_id'] if 'network_device_id' in data
                                        else customer.network_device_id) or None
```

```python
        if 'network_device_id' in data:
            customer.network_device_id = effective_network_device_id
```

- [ ] **Step 11: Repoint the two read consumers**

Rename `_customer_mikrotik_context` to `_customer_network_context` and resolve a device (`app.py:10595`):

```python
def _customer_network_context(customer_id):
    customer = tenant_query(Customer).filter_by(id=customer_id).first()
    if not customer:
        return None, None, ({'message': 'Customer not found!'}, 404)
    if not customer.network_device_id or not customer.pppoe_username:
        return None, None, ({'error': 'Customer is not linked to a network device.'}, 400)
    device = tenant_query(NetworkDevice).filter_by(id=customer.network_device_id).first()
    if not device:
        return None, None, ({'error': 'Linked network device not found.'}, 404)
    return customer, device, None
```

Update its three callers (`get_customer_mikrotik_status`, `suspend_customer_mikrotik`, `unsuspend_customer_mikrotik`) to call `_customer_network_context` and name the second value `device`. Their routes and behaviour are unchanged in this task — Tasks 3 and 4 change those.

In `_maybe_restore_mikrotik_access` (`app.py:4343`), swap the lookup:

```python
    if not customer.network_device_id:
        return None
    try:
        device = tenant_query(NetworkDevice).filter_by(id=customer.network_device_id).first()
        if not device or not customer.pppoe_username:
            return None
        ok, status = mikrotik.get_secret_status(device, customer.pppoe_username)
        if not ok:
            return {'attempted': True, 'ok': False, 'message': status}
        if status != 'disabled':
            return None  # already enabled (or not_found) -- nothing to restore
        ok, message = mikrotik.set_secret_enabled(device, customer.pppoe_username, True)
        return {'attempted': True, 'ok': ok, 'message': message}
```

Leave its existing `except Exception` / `logging.error(f"...")` exactly as it is. It logs the message only, never a traceback, which is what keeps the device credential out of Sentry.

- [ ] **Step 12: Fix the three affected existing tests**

`tests/test_lifecycle.py:115` — drop the now-nonexistent model from the known-gaps set:

```python
        appmod.UpstreamProvider, appmod.UpstreamProviderPayment,
```

`tests/test_phase1_hotfixes.py:200-204` — the endpoint is gone; the same authorization rule lives on network devices:

```python
def test_network_devices_get_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_authz4")
    collector_hdr = _add_collector(client, a, "collector_authz4")
    r = client.get("/api/network-devices", headers=collector_hdr)
    assert r.status_code == 403
```

`tests/test_network_device_model.py:2` — docstring only:

```python
"""Model-level tests for NetworkDevice -- tenant-scoped device-health
```

- [ ] **Step 13: Assert the deleted endpoints are actually gone**

A deleted route is easy to half-delete — leaving the function but removing the
decorator, or vice versa. Append to `tests/test_mikrotik_consolidation.py`:

```python
def test_the_mikrotik_server_endpoints_are_gone(app, client):
    """One tenant, one loop -- not parametrized. Parametrizing would build a
    tenant per case, and the case names contain slashes, which would end up in
    the generated tenant slug."""
    hdr = make_tenant(client, "Gone Co", "gone_admin")
    routes = [
        ("get", "/api/mikrotik-servers"),
        ("post", "/api/mikrotik-servers"),
        ("put", "/api/mikrotik-servers/1"),
        ("delete", "/api/mikrotik-servers/1"),
        ("post", "/api/mikrotik-servers/1/test-connection"),
    ]
    for method, path in routes:
        response = getattr(client, method)(path, headers=hdr, json={})
        assert response.status_code == 404, "{} {} still routes".format(
            method.upper(), path)
```

- [ ] **Step 14: Run the whole backend suite**

Run: `python -m pytest -q`
Expected: all pass, count ≥ 684 + 9. Any failure naming `MikrotikServer` or
`mikrotik_server_id` is a missed reference — `grep -rn "MikrotikServer\|mikrotik_server" app.py tests/`
and fix every hit.

- [ ] **Step 15: Write the migration**

Create `migrations/versions/f2b6c9d4e703_consolidate_mikrotik_server_into_network_device.py`:

```python
"""consolidate mikrotik_server into network_device

Revision ID: f2b6c9d4e703
Revises: d9e2b7c4a815
Create Date: 2026-09-07 12:30:00.000000

Hand-written rather than autogenerated, for the reason 5f65a6fd6e8d already
records: this repo's local SQLite database cannot reach the real head at all
(origin's bd054e2e7cf9 calls op.create_unique_constraint outside batch mode,
which SQLite rejects), so autogenerate has nothing valid to diff against.

Every step inspects before acting. Production's real schema disagrees with
migration history in both directions, so "the column must already be there" is
not a safe assumption in either direction.

The mikrotik_server table is EMPTY in production -- the owner confirmed via
super-admin that no tenant has configured one and no customer is linked. The
copy-forward branch below exists anyway because the container runs
`flask db upgrade && exec gunicorn`: a migration that raises does not misbehave,
it takes the whole application down. Copying rows forward and keeping the
original table is the only outcome that neither fails the deploy nor destroys
data.

downgrade() recreates the table and the old column but does NOT restore rows
into mikrotik_server. That is lossless for the zero-row case, which is the case
that exists.

No logger.exception or exc_info=True anywhere here: the copied rows carry
encrypted device passwords, and Sentry's LoggingIntegration captures frame
locals at ERROR.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2b6c9d4e703'
down_revision = 'd9e2b7c4a815'
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table, column):
    if not _has_table(table):
        return False
    return column in {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    bind = op.get_bind()

    if not _has_column('network_device', 'service_name'):
        with op.batch_alter_table('network_device', schema=None) as batch_op:
            batch_op.add_column(sa.Column('service_name', sa.String(length=100),
                                          nullable=True))

    if not _has_column('customer', 'network_device_id'):
        with op.batch_alter_table('customer', schema=None) as batch_op:
            batch_op.add_column(sa.Column('network_device_id', sa.Integer(),
                                          nullable=True))
            batch_op.create_index(batch_op.f('ix_customer_network_device_id'),
                                  ['network_device_id'], unique=False)
            batch_op.create_foreign_key('fk_customer_network_device_id',
                                        'network_device', ['network_device_id'], ['id'])

    kept_old_table = False
    if _has_table('mikrotik_server'):
        rows = bind.execute(sa.text('SELECT COUNT(*) FROM mikrotik_server')).scalar() or 0
        if rows:
            # INSERT ... RETURNING cannot report the SOURCE row's id, so a
            # temporary column carries the correlation from old row to new.
            with op.batch_alter_table('network_device', schema=None) as batch_op:
                batch_op.add_column(sa.Column('migrated_from_mikrotik_server_id',
                                              sa.Integer(), nullable=True))
            # device_type and interface_labels are nullable=False with
            # Python-side defaults, which a raw INSERT does not get.
            bind.execute(sa.text("""
                INSERT INTO network_device
                    (tenant_id, name, host, api_port, use_tls, username, password,
                     status, last_checked_at, last_status, service_name,
                     device_type, interface_labels,
                     migrated_from_mikrotik_server_id)
                SELECT tenant_id, name, host, api_port, use_tls, username, password,
                       status, last_checked_at, last_status, service_name,
                       'mikrotik_ccr', '{}', id
                FROM mikrotik_server
            """))
            if _has_column('customer', 'mikrotik_server_id'):
                # A correlated subquery rather than UPDATE ... FROM: this has to
                # run on SQLite in the migration test as well as on Postgres.
                bind.execute(sa.text("""
                    UPDATE customer SET network_device_id = (
                        SELECT nd.id FROM network_device nd
                        WHERE nd.migrated_from_mikrotik_server_id
                              = customer.mikrotik_server_id
                    )
                    WHERE customer.mikrotik_server_id IS NOT NULL
                """))
            with op.batch_alter_table('network_device', schema=None) as batch_op:
                batch_op.drop_column('migrated_from_mikrotik_server_id')
            kept_old_table = True
            print('mikrotik_server had {} row(s): copied into network_device and '
                  'KEPT the original table as a backup. Drop it by hand once you '
                  'have checked the migrated rows.'.format(rows))

    # Before the table drop: this removes the FK that would otherwise block it.
    if _has_column('customer', 'mikrotik_server_id'):
        with op.batch_alter_table('customer', schema=None) as batch_op:
            batch_op.drop_column('mikrotik_server_id')

    if _has_table('mikrotik_server') and not kept_old_table:
        op.drop_table('mikrotik_server')


def downgrade():
    if not _has_table('mikrotik_server'):
        op.create_table(
            'mikrotik_server',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('host', sa.String(length=255), nullable=False),
            sa.Column('api_port', sa.Integer(), nullable=False),
            sa.Column('use_tls', sa.Boolean(), nullable=False),
            sa.Column('username', sa.String(length=100), nullable=False),
            sa.Column('password', sa.Text(), nullable=False),
            sa.Column('service_name', sa.String(length=100), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=True),
            sa.Column('last_checked_at', sa.DateTime(), nullable=True),
            sa.Column('last_status', sa.String(length=20), nullable=True),
            sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_mikrotik_server_tenant_id', 'mikrotik_server',
                        ['tenant_id'], unique=False)

    if not _has_column('customer', 'mikrotik_server_id'):
        with op.batch_alter_table('customer', schema=None) as batch_op:
            batch_op.add_column(sa.Column('mikrotik_server_id', sa.Integer(),
                                          nullable=True))
            batch_op.create_foreign_key('fk_customer_mikrotik_server_id',
                                        'mikrotik_server', ['mikrotik_server_id'], ['id'])

    if _has_column('customer', 'network_device_id'):
        with op.batch_alter_table('customer', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_customer_network_device_id'))
            batch_op.drop_column('network_device_id')

    if _has_column('network_device', 'service_name'):
        with op.batch_alter_table('network_device', schema=None) as batch_op:
            batch_op.drop_column('service_name')
```

- [ ] **Step 16: Confirm there is exactly one head**

Run: `JWT_SECRET_KEY=x RUN_SCHEDULER=0 DATABASE_PATH=":memory:" python -m flask db heads`
Expected: a single line ending `f2b6c9d4e703 (head)`.

- [ ] **Step 17: Write the migration test**

Append to `tests/test_topology_migration.py`. Copy the bootstrap shape from `test_cpe_linking_migration_upgrade_downgrade_upgrade` already in that file.

```python

MIKROTIK_CONSOLIDATION_REVISION = "f2b6c9d4e703"


def test_mikrotik_consolidation_migration_upgrade_downgrade_upgrade():
    """Same bootstrap reasoning as the migration tests above: the real chain
    cannot be walked on SQLite, so build the current schema, stamp at this
    revision, and drive its real downgrade()/upgrade() from there."""
    tmpdir = tempfile.mkdtemp(prefix="mikrotik_consolidation_migration_test_")
    db_path = os.path.join(tmpdir, "mikrotik_consolidation.db")
    mig_app = Flask("test_mikrotik_consolidation_migration")
    mig_app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + db_path.replace("\\", "/"))
    mig_db = SQLAlchemy(mig_app)
    Migrate(mig_app, mig_db, directory=MIGRATIONS_DIR, render_as_batch=True)

    try:
        with mig_app.app_context():
            engine = mig_db.engine
            appmod.db.metadata.create_all(bind=engine)
            stamp(directory=MIGRATIONS_DIR, revision=MIKROTIK_CONSOLIDATION_REVISION)

            downgrade(directory=MIGRATIONS_DIR, revision="-1")
            assert "mikrotik_server" in _table_names(engine)
            assert "mikrotik_server_id" in _table_columns(engine, "customer")
            assert "network_device_id" not in _table_columns(engine, "customer")
            assert "service_name" not in _table_columns(engine, "network_device")

            upgrade(directory=MIGRATIONS_DIR, revision=MIKROTIK_CONSOLIDATION_REVISION)
            assert "mikrotik_server" not in _table_names(engine)
            assert "mikrotik_server_id" not in _table_columns(engine, "customer")
            assert "network_device_id" in _table_columns(engine, "customer")
            assert "service_name" in _table_columns(engine, "network_device")

            engine.dispose()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_mikrotik_consolidation_migration_carries_rows_forward_and_keeps_the_table():
    """The branch production will not take, and the one that must not lose data.

    The container runs `flask db upgrade && exec gunicorn`, so refusing to
    proceed on an unexpected row would be a full outage rather than a warning.
    Copying forward and keeping the original is the only outcome that neither
    fails the deploy nor destroys anything.
    """
    tmpdir = tempfile.mkdtemp(prefix="mikrotik_consolidation_rows_test_")
    db_path = os.path.join(tmpdir, "mikrotik_consolidation_rows.db")
    mig_app = Flask("test_mikrotik_consolidation_rows")
    mig_app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + db_path.replace("\\", "/"))
    mig_db = SQLAlchemy(mig_app)
    Migrate(mig_app, mig_db, directory=MIGRATIONS_DIR, render_as_batch=True)

    try:
        with mig_app.app_context():
            engine = mig_db.engine
            appmod.db.metadata.create_all(bind=engine)
            stamp(directory=MIGRATIONS_DIR, revision=MIKROTIK_CONSOLIDATION_REVISION)
            downgrade(directory=MIGRATIONS_DIR, revision="-1")

            with engine.begin() as conn:
                conn.execute(sa.text(
                    "INSERT INTO tenant (id, name, slug, status, plan) "
                    "VALUES (1, 'T', 't-slug', 'active', 'free')"))
                conn.execute(sa.text(
                    "INSERT INTO mikrotik_server "
                    "(id, tenant_id, name, host, api_port, use_tls, username, "
                    " password, service_name, status) "
                    "VALUES (7, 1, 'Old CCR', '192.168.100.1', 8728, 0, 'admin', "
                    "'secret', 'BCH', 'active')"))
                conn.execute(sa.text(
                    "INSERT INTO customer (id, tenant_id, name, mikrotik_server_id, "
                    " pppoe_username) VALUES (3, 1, 'Bach', 7, 'bach1')"))

            upgrade(directory=MIGRATIONS_DIR, revision=MIKROTIK_CONSOLIDATION_REVISION)

            with engine.begin() as conn:
                device = conn.execute(sa.text(
                    "SELECT id, name, host, service_name, device_type "
                    "FROM network_device")).fetchall()
                assert len(device) == 1
                new_id, name, host, service_name, device_type = device[0]
                assert (name, host, service_name, device_type) == (
                    'Old CCR', '192.168.100.1', 'BCH', 'mikrotik_ccr')

                linked = conn.execute(sa.text(
                    "SELECT network_device_id FROM customer WHERE id = 3")).scalar()
                assert linked == new_id, "the customer FK must follow the copied row"

            # The original survives as a backup rather than being dropped.
            assert "mikrotik_server" in _table_names(engine)
            # The stale column is still removed -- only the table is kept.
            assert "mikrotik_server_id" not in _table_columns(engine, "customer")

            engine.dispose()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
```

- [ ] **Step 18: Run the migration tests**

Run: `python -m pytest tests/test_topology_migration.py -q`
Expected: 7 passed.

- [ ] **Step 19: Run the whole backend suite and commit**

Run: `python -m pytest -q`
Expected: all pass, ≥ 690.

```bash
git add app.py migrations/versions/f2b6c9d4e703_consolidate_mikrotik_server_into_network_device.py tests/
git commit -m "refactor: consolidate MikrotikServer into NetworkDevice"
```

---

### Task 2: Reject operations a device cannot serve

**Files:**
- Modify: `app.py` (`_create_device_job`, ~10096; add `DEVICE_TYPE_OPERATIONS` beside `AGENT_OPERATIONS`, ~414)
- Modify: `tests/test_mikrotik_consolidation.py`

**Interfaces:**
- Consumes: `make_device(app, tenant_name, **over)` from Task 1's test module.
- Produces: `DEVICE_TYPE_OPERATIONS` (dict of `device_type -> tuple[str, ...]`); `_create_device_job` returns `(None, message)` for an unsupported pairing.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mikrotik_consolidation.py`:

```python
def test_an_olt_cannot_be_asked_a_pppoe_question(app, client):
    """secret_status against an OLT is a job nobody can serve. Until the three
    read operations became reachable this was theoretical; now it isn't."""
    make_tenant(client, "Guard A", "guard_a_admin")
    device_id = make_device(app, "Guard A", device_type="vsol_olt",
                            api_port=161, username="")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        job, error = appmod._create_device_job(device, "secret_status",
                                               {"pppoe_username": "bach1"})
        assert job is None
        assert "vsol_olt" in error and "secret_status" in error


def test_a_mikrotik_cannot_be_asked_an_snmp_question(app, client):
    make_tenant(client, "Guard B", "guard_b_admin")
    device_id = make_device(app, "Guard B")
    with app.app_context():
        device = appmod.db.session.get(appmod.NetworkDevice, device_id)
        job, error = appmod._create_device_job(device, "olt_status")
        assert job is None
        assert "mikrotik_ccr" in error and "olt_status" in error


def test_every_supported_pairing_is_accepted(app, client):
    """The mapping is total -- NETWORK_DEVICE_TYPES is a closed set of two --
    so there is no fallback branch, and every listed pairing must work."""
    make_tenant(client, "Guard C", "guard_c_admin")
    olt_id = make_device(app, "Guard C", name="OLT", device_type="vsol_olt",
                         api_port=161, username="")
    ccr_id = make_device(app, "Guard C", name="CCR2", host="192.168.100.2")
    assert set(appmod.DEVICE_TYPE_OPERATIONS) == set(appmod.NETWORK_DEVICE_TYPES)
    assert (set(appmod.DEVICE_TYPE_OPERATIONS['vsol_olt'])
            | set(appmod.DEVICE_TYPE_OPERATIONS['mikrotik_ccr'])) == set(appmod.AGENT_OPERATIONS)
    with app.app_context():
        for device_id, device_type in ((olt_id, 'vsol_olt'), (ccr_id, 'mikrotik_ccr')):
            device = appmod.db.session.get(appmod.NetworkDevice, device_id)
            for operation in appmod.DEVICE_TYPE_OPERATIONS[device_type]:
                job, error = appmod._create_device_job(
                    device, operation, {"pppoe_username": "bach1"})
                assert error is None, "{} rejected on {}: {}".format(
                    operation, device_type, error)
                assert job is not None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_mikrotik_consolidation.py -q -k "guard or pairing"`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'DEVICE_TYPE_OPERATIONS'`.

- [ ] **Step 3: Add the mapping**

In `app.py`, immediately after the `AGENT_OPERATIONS` tuple (~line 417):

```python
# Which operations each kind of device can actually answer. NETWORK_DEVICE_TYPES
# is a closed set of exactly two, so this mapping is total and there is no
# fallback branch to get wrong. Together the two rows cover AGENT_OPERATIONS
# exactly -- a test asserts that, so adding an operation without placing it here
# fails loudly rather than becoming quietly unreachable.
DEVICE_TYPE_OPERATIONS = {
    'vsol_olt': ('olt_status', 'cpe_locations'),
    'mikrotik_ccr': ('device_health', 'test_connection',
                     'secret_status', 'active_session'),
}
```

- [ ] **Step 4: Enforce it in the job factory**

In `_create_device_job`, directly after the existing `AGENT_OPERATIONS` check:

```python
    permitted = DEVICE_TYPE_OPERATIONS.get(device.device_type, ())
    if operation not in permitted:
        return None, 'A {} device cannot perform {}.'.format(
            device.device_type, operation)
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_mikrotik_consolidation.py -q`
Expected: all pass.

- [ ] **Step 6: Run the whole suite and commit**

Run: `python -m pytest -q`
Expected: all pass.

```bash
git add app.py tests/test_mikrotik_consolidation.py
git commit -m "feat: reject device operations a device type cannot serve"
```

---

### Task 3: Relay the three read operations

**Files:**
- Modify: `app.py` (add `POST /api/network-devices/<id>/test-connection` after `check-now` ~9330; replace `get_customer_mikrotik_status` ~10605)
- Modify: `tests/test_mikrotik_consolidation.py`

**Interfaces:**
- Consumes: `_create_device_job`, `DEVICE_TYPE_OPERATIONS`, `_customer_network_context` from Tasks 1-2.
- Produces: `POST /api/network-devices/<id>/test-connection` returning `{'ok', 'message', 'job_id', 'device'}`; `POST /api/customers/<id>/network-status` returning `{'ok': True, 'jobs': {'secret': int, 'session': int}}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mikrotik_consolidation.py`:

```python
def _admin(client, business, username):
    return make_tenant(client, business, username)


def test_device_test_connection_creates_a_job(app, client):
    """The one genuinely useful button the deleted Mikrotik Servers page had.
    It moves here rather than being lost."""
    hdr = _admin(client, "Relay A", "relay_a_admin")
    device_id = make_device(app, "Relay A")
    r = client.post("/api/network-devices/{}/test-connection".format(device_id),
                    headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["job_id"] is not None
    with app.app_context():
        job = appmod.db.session.get(appmod.NetworkAgentJob, body["job_id"])
        assert job.operation == "test_connection"


def test_test_connection_is_refused_on_an_olt(app, client):
    hdr = _admin(client, "Relay B", "relay_b_admin")
    device_id = make_device(app, "Relay B", device_type="vsol_olt",
                            api_port=161, username="")
    r = client.post("/api/network-devices/{}/test-connection".format(device_id),
                    headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is False
    assert body["job_id"] is None
    assert "vsol_olt" in body["message"]


def test_customer_network_status_creates_two_jobs(app, client):
    """Two existing operations rather than one new combined one: a new
    operation would force an agent update, and the shipped agent already
    dispatches both of these."""
    hdr = _admin(client, "Relay C", "relay_c_admin")
    device_id = make_device(app, "Relay C")
    with app.app_context():
        tenant = _tenant("Relay C")
        customer = appmod.Customer(tenant_id=tenant.id, name="Bach",
                                   network_device_id=device_id,
                                   pppoe_username="bach1")
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        customer_id = customer.id

    r = client.post("/api/customers/{}/network-status".format(customer_id),
                    headers=hdr)
    assert r.status_code == 200
    jobs = r.get_json()["jobs"]
    with app.app_context():
        secret = appmod.db.session.get(appmod.NetworkAgentJob, jobs["secret"])
        session = appmod.db.session.get(appmod.NetworkAgentJob, jobs["session"])
        assert secret.operation == "secret_status"
        assert session.operation == "active_session"
        assert secret.params == {"pppoe_username": "bach1"}
        assert session.params == {"pppoe_username": "bach1"}
        # Direct mode: both are already terminal, so the frontend's first poll
        # answers immediately and it needs only one code path.
        assert secret.status == "done"
        assert session.status == "done"


def test_customer_network_status_needs_a_linked_device(app, client):
    hdr = _admin(client, "Relay D", "relay_d_admin")
    with app.app_context():
        tenant = _tenant("Relay D")
        customer = appmod.Customer(tenant_id=tenant.id, name="Unlinked")
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        customer_id = customer.id
    r = client.post("/api/customers/{}/network-status".format(customer_id),
                    headers=hdr)
    assert r.status_code == 400
    assert "not linked" in r.get_json()["error"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_mikrotik_consolidation.py -q -k "relay or network_status or test_connection"`
Expected: FAIL — 404 on both new routes.

- [ ] **Step 3: Add the device test-connection endpoint**

In `app.py`, immediately after `check_network_device_now`:

```python
@app.route('/api/network-devices/<int:device_id>/test-connection', methods=['POST'])
@jwt_required()
@admin_or_finance_required()
def test_network_device_connection(device_id):
    """Ask the device to prove it is reachable and the credential works.

    Goes through _create_device_job like every other device call, so it works
    identically in direct mode and through the on-prem agent. In direct mode
    the returned job is already terminal; in agent mode the caller polls it.
    """
    device = tenant_query(NetworkDevice).filter_by(id=device_id).first()
    if not device:
        return jsonify({'message': 'Network device not found!'}), 404
    job, error = _create_device_job(device, 'test_connection')
    if error:
        return jsonify({'ok': False, 'message': error, 'job_id': None,
                        'device': device.to_dict()}), 200
    return jsonify({'ok': True, 'message': None, 'job_id': job.id,
                    'device': device.to_dict()}), 200
```

- [ ] **Step 4: Replace the customer status endpoint**

Replace `get_customer_mikrotik_status` (`app.py:10605`) entirely:

```python
@app.route('/api/customers/<int:customer_id>/network-status', methods=['POST'])
@jwt_required()
def get_customer_network_status(customer_id):
    """Queue the two reads that describe a customer's PPPoE state.

    POST, not GET, because it now creates jobs. Two jobs rather than one
    combined operation: a new operation would have to be added to the on-prem
    agent, forcing the owner to hand-copy files onto the box, whereas
    secret_status and active_session already ship in it.

    In direct mode both jobs come back already terminal, so the caller's first
    poll answers immediately and the frontend needs only one code path. In
    agent mode the agent handles one job per 2-second poll, so a status check
    resolves in up to about four seconds.
    """
    customer, device, err = _customer_network_context(customer_id)
    if err:
        return jsonify(err[0]), err[1]

    params = {'pppoe_username': customer.pppoe_username}
    secret_job, error = _create_device_job(device, 'secret_status', params)
    if error:
        return jsonify({'ok': False, 'message': error, 'jobs': None}), 200
    session_job, error = _create_device_job(device, 'active_session', params)
    if error:
        return jsonify({'ok': False, 'message': error, 'jobs': None}), 200

    return jsonify({'ok': True, 'message': None,
                    'jobs': {'secret': secret_job.id,
                             'session': session_job.id}}), 200
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_mikrotik_consolidation.py -q`
Expected: all pass.

- [ ] **Step 6: Run the whole suite and commit**

Run: `python -m pytest -q`
Expected: all pass.

```bash
git add app.py tests/test_mikrotik_consolidation.py
git commit -m "feat: relay device test-connection and customer PPPoE status"
```

---

### Task 4: Writes refuse honestly in agent mode

**Files:**
- Modify: `app.py` (`suspend_customer_mikrotik` / `unsuspend_customer_mikrotik` ~10622-10645, `_maybe_restore_mikrotik_access` ~4343)
- Modify: `tests/test_mikrotik_consolidation.py`

**Interfaces:**
- Consumes: `_customer_network_context`, `_tenant_access_mode()`.
- Produces: `POST /api/customers/<id>/network-suspend`, `POST /api/customers/<id>/network-unsuspend`; module constant `AGENT_WRITE_UNSUPPORTED_MESSAGE`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mikrotik_consolidation.py`:

```python
def _set_agent_mode(app, tenant_name):
    with app.app_context():
        tenant = _tenant(tenant_name)
        settings = appmod.BusinessSettings.query.filter_by(tenant_id=tenant.id).first()
        if settings is None:
            settings = appmod.BusinessSettings(
                tenant_id=tenant.id, business_name=tenant_name,
                address="Beirut", mobile="+96170000000")
            appmod.db.session.add(settings)
        settings.network_access_mode = "agent"
        appmod.db.session.commit()


def _linked_customer(app, tenant_name, device_id):
    with app.app_context():
        tenant = _tenant(tenant_name)
        customer = appmod.Customer(tenant_id=tenant.id, name="Bach",
                                   network_device_id=device_id,
                                   pppoe_username="bach1")
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        return customer.id


def test_suspend_refuses_in_agent_mode_without_touching_the_router(app, client, monkeypatch):
    """Today this would call the router from the cloud, find it unreachable and
    hang until the connector times out. A fast, honest refusal beats a
    13-second timeout that looks like a network fault."""
    called = []
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda *a, **k: called.append(a) or (True, "ok"))
    hdr = _admin(client, "Write A", "write_a_admin")
    device_id = make_device(app, "Write A")
    customer_id = _linked_customer(app, "Write A", device_id)
    _set_agent_mode(app, "Write A")

    r = client.post("/api/customers/{}/network-suspend".format(customer_id),
                    headers=hdr)
    assert r.status_code == 501
    assert r.get_json()["ok"] is False
    assert "direct connection" in r.get_json()["message"]
    assert called == [], "the connector must not be reached at all"


def test_unsuspend_refuses_in_agent_mode(app, client, monkeypatch):
    called = []
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda *a, **k: called.append(a) or (True, "ok"))
    hdr = _admin(client, "Write B", "write_b_admin")
    device_id = make_device(app, "Write B")
    customer_id = _linked_customer(app, "Write B", device_id)
    _set_agent_mode(app, "Write B")

    r = client.post("/api/customers/{}/network-unsuspend".format(customer_id),
                    headers=hdr)
    assert r.status_code == 501
    assert called == []


def test_suspend_still_works_in_direct_mode(app, client, monkeypatch):
    monkeypatch.setattr(appmod.mikrotik, "set_secret_enabled",
                        lambda *a, **k: (True, "disabled"))
    hdr = _admin(client, "Write C", "write_c_admin")
    device_id = make_device(app, "Write C")
    customer_id = _linked_customer(app, "Write C", device_id)

    r = client.post("/api/customers/{}/network-suspend".format(customer_id),
                    headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_payment_restore_skips_in_agent_mode(app, client, monkeypatch):
    """Runs after every settling payment, so a burned timeout here is a tax on
    the billing path, not just on one click."""
    called = []
    monkeypatch.setattr(appmod.mikrotik, "get_secret_status",
                        lambda *a, **k: called.append(a) or (True, "disabled"))
    hdr = _admin(client, "Write D", "write_d_admin")
    device_id = make_device(app, "Write D")
    customer_id = _linked_customer(app, "Write D", device_id)
    _set_agent_mode(app, "Write D")

    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        result = appmod._maybe_restore_mikrotik_access(customer)
    assert result is None
    assert called == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_mikrotik_consolidation.py -q -k write`
Expected: FAIL — 404 on `network-suspend` (the routes are still named `mikrotik-suspend`).

- [ ] **Step 3: Add the shared refusal message**

In `app.py`, immediately after `DEVICE_TYPE_OPERATIONS`:

```python
# Suspend/unsuspend write to the router, and the agent deliberately does not
# relay writes: mikrotik.set_secret_enabled is absent from its ALLOWED_OPERATIONS
# so that a compromised cloud can read the network but never disconnect anyone.
# Until that gets its own design round, agent-mode tenants get an immediate,
# honest refusal instead of a connection attempt from the cloud that can only
# time out. See the spec's "Reads now, writes later".
AGENT_WRITE_UNSUPPORTED_MESSAGE = (
    'This action needs a direct connection to the router and is not yet '
    'available through the on-prem agent.'
)
```

- [ ] **Step 4: Rename and guard the two write endpoints**

Replace both functions (`app.py:10622-10645`):

```python
@app.route('/api/customers/<int:customer_id>/network-suspend', methods=['POST'])
@jwt_required()
def suspend_customer_network(customer_id):
    customer, device, err = _customer_network_context(customer_id)
    if err:
        return jsonify(err[0]), err[1]
    if _tenant_access_mode() == 'agent':
        return jsonify({'ok': False, 'message': AGENT_WRITE_UNSUPPORTED_MESSAGE}), 501

    ok, message = mikrotik.set_secret_enabled(device, customer.pppoe_username, False)
    return jsonify({'ok': ok, 'message': message}), (200 if ok else 502)

@app.route('/api/customers/<int:customer_id>/network-unsuspend', methods=['POST'])
@jwt_required()
def unsuspend_customer_network(customer_id):
    customer, device, err = _customer_network_context(customer_id)
    if err:
        return jsonify(err[0]), err[1]
    if _tenant_access_mode() == 'agent':
        return jsonify({'ok': False, 'message': AGENT_WRITE_UNSUPPORTED_MESSAGE}), 501

    ok, message = mikrotik.set_secret_enabled(device, customer.pppoe_username, True)
    return jsonify({'ok': ok, 'message': message}), (200 if ok else 502)
```

- [ ] **Step 5: Guard the payment-restore path**

In `_maybe_restore_mikrotik_access`, immediately after the existing `if not customer.network_device_id: return None`:

```python
    if _tenant_access_mode() == 'agent':
        # Re-enabling is a write, which the agent does not relay. Skip before
        # opening anything: this runs after every settling payment, so a
        # timeout here would tax the billing path on every transaction.
        return None
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_mikrotik_consolidation.py -q`
Expected: all pass.

- [ ] **Step 7: Run the whole suite and commit**

Run: `python -m pytest -q`
Expected: all pass.

```bash
git add app.py tests/test_mikrotik_consolidation.py
git commit -m "feat: refuse router writes in agent mode instead of timing out"
```

---

### Task 5: Frontend — one device page

**Files:**
- Delete: `frontend/src/components/MikrotikServerManagementView.js`
- Modify: `frontend/src/App.js` (nav entry line 78, and the view's import/render)
- Modify: `frontend/src/context/AppContext.js` (lines 135-140, 167-170)
- Modify: `frontend/src/components/NetworkDeviceManagementView.js`

**Interfaces:**
- Consumes: `POST /api/network-devices/<id>/test-connection` from Task 3.
- Produces: `apiService.testNetworkDeviceConnection(id)`; `service_name` on the device form.

- [ ] **Step 1: Replace the API methods**

In `frontend/src/context/AppContext.js`, delete the five Mikrotik server methods (lines 135-140) and the three customer action methods (167-170), replacing them with:

```javascript
    // Router/device live actions. The Mikrotik Server model was folded into
    // NetworkDevice -- see
    // docs/superpowers/specs/2026-09-07-mikrotik-device-consolidation-design.md
    testNetworkDeviceConnection: (id) => api.post(`/network-devices/${id}/test-connection`),
    fetchCustomerNetworkStatus: (customerId) => api.post(`/customers/${customerId}/network-status`),
    suspendCustomerNetwork: (customerId) => api.post(`/customers/${customerId}/network-suspend`),
    unsuspendCustomerNetwork: (customerId) => api.post(`/customers/${customerId}/network-unsuspend`),
```

- [ ] **Step 2: Remove the page and its nav entry**

Delete `frontend/src/components/MikrotikServerManagementView.js`.

In `frontend/src/App.js`, delete the nav entry at line 78 (`key: 'mikrotik-servers'`), the `MikrotikServerManagementView` import, and the render branch that mounts it. Leave the `network-devices` entry untouched — it has no `visibleWhen`, so Network Devices stays visible to admin/finance regardless of `network_mode`.

- [ ] **Step 3: Add the service name field**

In `frontend/src/components/NetworkDeviceManagementView.js`, inside the edit dialog's `<Grid container>`, after the username/password fields and inside the existing `{editingDevice?.device_type !== 'vsol_olt' && (...)}` guard region (an OLT has no PPPoE server):

```jsx
                        {editingDevice?.device_type !== 'vsol_olt' && (
                            <Grid item xs={12}>
                                <TextField fullWidth label="Service Name (Optional)"
                                    value={editingDevice?.service_name || ''}
                                    helperText="Only needed if this router runs more than one PPPoE server instance, or its network is shared with another ISP. Leave blank to match by PPPoE username alone."
                                    onChange={(e) => setEditingDevice({ ...editingDevice, service_name: e.target.value })} />
                            </Grid>
                        )}
```

Add `service_name: ''` to the new-device initial state on the Add Device button (line ~231).

- [ ] **Step 4: Add the Test connection action**

In the same file, add a handler beside the existing check-now handler:

```jsx
    const [testingId, setTestingId] = useState(null);

    const handleTestConnection = async (device) => {
        setTestingId(device.id);
        try {
            const { data } = await apiService.testNetworkDeviceConnection(device.id);
            setSnackbar({
                open: true,
                message: data.ok ? 'Connection test queued.' : data.message,
                severity: data.ok ? 'success' : 'error',
            });
            if (data.ok) loadDevices();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Connection test failed', severity: 'error' });
        } finally {
            setTestingId(null);
        }
    };
```

Render it as a row action, shown only for Mikrotik devices:

```jsx
                                        {d.device_type !== 'vsol_olt' && (
                                            <Tooltip title="Test Connection">
                                                <IconButton color="info" onClick={() => handleTestConnection(d)} disabled={testingId === d.id}>
                                                    {testingId === d.id ? <CircularProgress size={18} /> : <TestConnectionIcon fontSize="small" />}
                                                </IconButton>
                                            </Tooltip>
                                        )}
```

Import the icon: `Wifi as TestConnectionIcon` from `@mui/icons-material`, matching the deleted page's naming.

- [ ] **Step 5: Confirm nothing still references the deleted module**

Run: `grep -rn "MikrotikServerManagementView\|fetchMikrotikServers\|mikrotik-servers" frontend/src`
Expected: no output.

- [ ] **Step 6: Build**

Run: `cd frontend && npx react-scripts build`
Expected: "Compiled with warnings" and no new warnings naming `App.js` or `NetworkDeviceManagementView.js`.

Then revert the artifacts: `git checkout -- frontend/build build && git clean -fdq frontend/build build`

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat: fold the Mikrotik Servers page into Network Devices"
```

---

### Task 6: Frontend — customer form and status panel

**Files:**
- Modify: `frontend/src/components/SubscriptionsView.js` (268-281, 629, 668-690, 952-975, 1332-1405)
- Create: `frontend/src/components/mergeNetworkStatus.js`
- Create: `frontend/src/components/mergeNetworkStatus.test.js`

**Interfaces:**
- Consumes: `apiService.fetchCustomerNetworkStatus`, `suspendCustomerNetwork`, `unsuspendCustomerNetwork` (Task 5); `POST /api/customers/<id>/network-status` returning `{ok, jobs: {secret, session}}` (Task 3); `GET /api/network-jobs/<id>` (existing).
- Produces: `mergeNetworkStatus(secretJob, sessionJob)` -> `{secret_status, secret_error, active_session, session_error, pending}`.

- [ ] **Step 1: Write the failing helper test**

Create `frontend/src/components/mergeNetworkStatus.test.js`:

```javascript
import { mergeNetworkStatus } from './mergeNetworkStatus';

const done = (result) => ({ status: 'done', result, error: null });
const failed = (error) => ({ status: 'done', result: null, error });

describe('mergeNetworkStatus', () => {
    test('folds two finished jobs into the old single-response shape', () => {
        expect(mergeNetworkStatus(done('enabled'), done({ address: '10.0.0.9' })))
            .toEqual({
                secret_status: 'enabled',
                secret_error: null,
                active_session: { address: '10.0.0.9' },
                session_error: null,
                pending: false,
            });
    });

    test('reports each job’s error independently', () => {
        // One connector call can fail while the other succeeds -- the old
        // inline endpoint had the same property and the UI relies on it.
        const merged = mergeNetworkStatus(failed('auth failed'), done(null));
        expect(merged.secret_status).toBeNull();
        expect(merged.secret_error).toBe('auth failed');
        expect(merged.active_session).toBeNull();
        expect(merged.session_error).toBeNull();
    });

    test('is pending until both jobs are terminal', () => {
        expect(mergeNetworkStatus({ status: 'pending' }, done(null)).pending).toBe(true);
        expect(mergeNetworkStatus(done('enabled'), { status: 'claimed' }).pending).toBe(true);
        expect(mergeNetworkStatus(done('enabled'), done(null)).pending).toBe(false);
    });

    test('treats a missing job as still pending rather than throwing', () => {
        // The two polls do not resolve together; the first render has one.
        expect(mergeNetworkStatus(null, null).pending).toBe(true);
        expect(mergeNetworkStatus(done('enabled'), undefined).pending).toBe(true);
    });

    test('an expired job surfaces as an error, not a silent blank', () => {
        expect(mergeNetworkStatus({ status: 'expired', result: null, error: 'Job expired' },
                                  done(null)).secret_error).toBe('Job expired');
    });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/mergeNetworkStatus.test.js"`
Expected: FAIL — cannot resolve `./mergeNetworkStatus`.

- [ ] **Step 3: Write the helper**

Create `frontend/src/components/mergeNetworkStatus.js`:

```javascript
// The customer status endpoint used to make two connector calls inline and
// return one blob. Under the relay it queues two jobs instead -- deliberately
// two operations the on-prem agent already ships, rather than one new combined
// one that would force everybody to update their agent. This folds the two job
// results back into the shape the UI already renders.
//
// See docs/superpowers/specs/2026-09-07-mikrotik-device-consolidation-design.md

const TERMINAL = ['done', 'failed', 'expired'];

function isTerminal(job) {
    return !!job && TERMINAL.includes(job.status);
}

export function mergeNetworkStatus(secretJob, sessionJob) {
    return {
        secret_status: secretJob && !secretJob.error ? (secretJob.result ?? null) : null,
        secret_error: (secretJob && secretJob.error) || null,
        active_session: sessionJob && !sessionJob.error ? (sessionJob.result ?? null) : null,
        session_error: (sessionJob && sessionJob.error) || null,
        // Both polls have to land before the panel stops showing a spinner;
        // they do not resolve together, and in agent mode the agent handles one
        // job per 2-second poll.
        pending: !isTerminal(secretJob) || !isTerminal(sessionJob),
    };
}
```

- [ ] **Step 4: Run the helper test**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/mergeNetworkStatus.test.js"`
Expected: 5 passed.

- [ ] **Step 5: Switch the customer form to network devices**

In `SubscriptionsView.js`, replace the state and load (lines 268, 281):

```javascript
    const [networkDevices, setNetworkDevices] = useState([]);
```

```javascript
        apiService.fetchNetworkDevices().then(res => setNetworkDevices((res.data || []).filter(d => d.device_type !== 'vsol_olt'))).catch(err => console.error("Failed to load network devices", err));
```

In **both** the add form (line ~965) and the edit form (line ~1345), replace the Mikrotik Server select:

```jsx
                                <Grid item xs={12} md={6}>
                                    <TextField fullWidth select label="Router (Optional)" value={newCustomer.network_device_id || ''} onChange={(e) => setNewCustomer({ ...newCustomer, network_device_id: e.target.value })}>
                                        <MenuItem value="">None</MenuItem>
                                        {networkDevices.map(d => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}
                                    </TextField>
                                </Grid>
```

The edit form's copy is identical except it reads `editingCustomer?.network_device_id` and calls `setEditingCustomer`. Leave both `network_mode === 'local_mikrotik'` guards exactly as they are.

Update the status panel's visibility condition at line ~1373 to `editingCustomer?.network_device_id`.

- [ ] **Step 6: Poll both jobs**

Replace `fetchMikrotikStatus` and `handleMikrotikAction` (lines 668-690):

```javascript
    const fetchNetworkStatus = async (customerId) => {
        setMikrotikStatusLoading(true);
        try {
            const { data } = await apiService.fetchCustomerNetworkStatus(customerId);
            if (!data.ok) {
                setMikrotikStatus({ secret_error: data.message, pending: false });
                return;
            }
            // Both jobs are already terminal in direct mode, so this usually
            // settles on the first pass. In agent mode the agent handles one
            // job per 2-second poll, so allow a few rounds.
            for (let attempt = 0; attempt < 15; attempt++) {
                const [secret, session] = await Promise.all([
                    apiService.fetchNetworkJob(data.jobs.secret),
                    apiService.fetchNetworkJob(data.jobs.session),
                ]);
                const merged = mergeNetworkStatus(secret.data, session.data);
                setMikrotikStatus(merged);
                if (!merged.pending) return;
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
        } catch (err) {
            setMikrotikStatus({ secret_error: err.response?.data?.error || 'Status check failed', pending: false });
        } finally {
            setMikrotikStatusLoading(false);
        }
    };

    const handleNetworkAction = async (customerId, action) => {
        setMikrotikActionLoading(true);
        try {
            const call = action === 'suspend' ? apiService.suspendCustomerNetwork : apiService.unsuspendCustomerNetwork;
            const { data } = await call(customerId);
            setSnackbar({ open: true, message: data.message, severity: data.ok ? 'success' : 'warning' });
            if (data.ok) await fetchNetworkStatus(customerId);
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.message || 'Action failed', severity: 'error' });
        } finally {
            setMikrotikActionLoading(false);
        }
    };
```

Import the helper at the top of the file:

```javascript
import { mergeNetworkStatus } from './mergeNetworkStatus';
```

Update the three call sites that used the old names: line 629 (`fetchMikrotikStatus`), line 1378 (Refresh button), and lines 1395/1399 (`handleMikrotikAction`).

The suspend/unsuspend buttons need no special casing for agent mode — the endpoint returns `ok: false` with an explanatory message, which the existing snackbar already surfaces as a warning.

- [ ] **Step 7: Nothing to add for job polling**

`apiService.fetchNetworkJob(jobId)` already exists at
`frontend/src/context/AppContext.js:158`, against `/network-jobs/${jobId}`.
Verified while writing this plan — use it as-is, do not redefine it.

- [ ] **Step 8: Check nothing stale remains**

Run: `grep -rn "mikrotik_server_id\|mikrotik-status\|mikrotik-suspend\|mikrotik-unsuspend" frontend/src`
Expected: no output.

- [ ] **Step 9: Run the frontend suite and build**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/*.test.js"`
Expected: ≥ 66 passed. `src/App.test.js` fails to resolve `@testing-library/react` — pre-existing and unrelated.

Run: `cd frontend && npx react-scripts build`
Expected: compiles; no new warnings on `SubscriptionsView.js` or `mergeNetworkStatus.js`.

Then: `git checkout -- frontend/build build && git clean -fdq frontend/build build`

- [ ] **Step 10: Commit**

```bash
git add frontend/src
git commit -m "feat: link customers to network devices and poll status through the relay"
```

---

## Final verification

- [ ] `python -m pytest -q` — all pass, ≥ 700
- [ ] `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/*.test.js"` — ≥ 66 pass
- [ ] `cd frontend && npx react-scripts build` — compiles, then revert `frontend/build` and `build`
- [ ] `git diff --stat origin/main..HEAD` shows **no** changes to `agent/`, `mikrotik.py` or `vsol_olt.py`
- [ ] `grep -rn "MikrotikServer\|mikrotik_server" app.py tests/ frontend/src` returns only the migration file and spec/plan references
- [ ] `JWT_SECRET_KEY=x RUN_SCHEDULER=0 DATABASE_PATH=":memory:" python -m flask db heads` shows one head
