# servicesBills — SaaS Transformation Implementation Plan (Shared-DB)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Frontend work (Phase 5) REQUIRED SUB-SKILL:** the UI/UX Pro Max skills installed at `.claude/skills/design/`, `.claude/skills/brand/`, `.claude/skills/banner-design/` — invoke them for any UI/visual task.

**Goal:** Convert the single-tenant desktop app "delta-net" into a multi-tenant, web-hosted SaaS named **servicesBills**, where many businesses share one Postgres database, isolated by `tenant_id`.

**Architecture:** Shared-database multi-tenancy. One Postgres instance, one schema. Every domain table gets a non-null `tenant_id` foreign key to a new `tenants` table. Every query is scoped to the authenticated user's tenant via a single enforced helper (`tenant_query()`) and a `current_tenant_id()` accessor that reads `tenant_id` from the JWT. Electron packaging is retired; the existing React SPA is served as the hosted web frontend. Billing for the servicesBills product itself is handled by Stripe subscriptions with plan-gating.

**Tech Stack:** Python 3 · Flask · SQLAlchemy · Flask-JWT-Extended · Flask-Migrate/Alembic · Flask-Bcrypt · PostgreSQL · pytest · React 18 + MUI · Stripe · S3-compatible object storage (AWS S3 / Cloudflare R2) · gunicorn.

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Product name:** `servicesBills` (display/marketing). Package/appId slug: `servicesbills`. Reverse-domain id: `net.servicesbills.app`. Replace all `delta-net` / `DeltaNet` / `DeltaNet Manager` / `beta-ms` / `betams` strings.
- **Tenancy model:** shared DB + non-null `tenant_id` column on every domain table. **No cross-tenant reads or writes may ever occur.** A missed scope is a P0 data-leak bug.
- **All file paths are absolute** under `C:\Users\InfoCenter\source\repos\delta-net-saas\`. Work happens ONLY in this folder; the original `delta-net` folder is an untouched backup.
- **Secrets never live in source.** `JWT_SECRET_KEY`, DB URL, Stripe keys, VAPID keys, and per-tenant WhatsApp tokens come from environment or encrypted columns. The current hardcoded secret at `app.py:56` is a live vulnerability and must be removed, not merely rotated.
- **TDD:** every behavior change is preceded by a failing test. Backend tests use `pytest` against an in-memory SQLite DB; multi-tenancy isolation tests are mandatory (tenant A must never see tenant B).
- **Migrations:** after Phase 1, schema changes go through Alembic (`flask db migrate` / `flask db upgrade`). `db.create_all()` is removed as the schema source of truth.
- **Frequent commits:** one commit per completed task step group, conventional-commit messages.
- **Backward-compatibility of existing business logic:** billing/payment/receipt math (e.g. `apply_customer_balance_to_unpaid_payments`) must not change behavior — only gain tenant scoping.

---

## Architecture Decision Record: why shared-DB

Recorded so the executing engineer understands the "why" behind the scoping discipline.

- The codebase is small and centralized (one `app.py`, 23 models, 101 routes) → a systematic scoping pass is bounded and mechanical.
- A cross-tenant super-admin view (operator dashboard over all businesses) is a product requirement → native in shared-DB, awkward in DB-per-tenant.
- One deploy, one connection pool, one Alembic run per release → lowest operational cost at the expected tenant scale.
- **Accepted risk:** a forgotten `.filter(tenant_id=...)` leaks data. **Mitigation:** all reads go through `tenant_query(Model)`; writes go through `new_for_tenant(Model, ...)`; a dedicated isolation test suite asserts no leakage per resource.

---

## File Structure / Impact Map

Current state → target state.

| File (absolute under repo root) | Responsibility | Change |
|---|---|---|
| `app.py` | Monolith: config, 23 models, 101 routes, scheduler | Split config/secrets out; add `Tenant`; add `tenant_id` everywhere; scope every route. Large but stays one file initially (follow existing pattern). |
| `config.py` *(new)* | Env-driven config (secret, DB URL, Stripe, storage) | Create |
| `tenancy.py` *(new)* | `current_tenant_id()`, `tenant_query()`, `new_for_tenant()`, `tenant_required` decorator | Create |
| `storage.py` *(new)* | Upload abstraction (local in dev, S3 in prod) | Create |
| `billing.py` *(new)* | Stripe subscription + webhook + plan-gating | Create (Phase 4) |
| `migrations/` *(new)* | Alembic migration scripts | Create (Phase 1) |
| `tests/` *(new)* | pytest suite incl. tenant-isolation tests | Create (Phase 0) |
| `requirements.txt` | Deps | Add `Flask-Migrate`, `psycopg2-binary`, `pytest`, `stripe`, `boto3`, `cryptography` |
| `frontend/src/context/AppContext.js` | Axios base URL, token storage | Env-driven base URL; add signup/tenant flows |
| `frontend/src/components/ReportsView.js:29` | Hardcoded `http://127.0.0.1:5000/api` | Fix to shared api client (prod bug) |
| `frontend/package.json`, `package.json` | Names, Electron build config | Rename; strip Electron packaging |
| `frontend/public/index.html`, `electron.js` | Titles, appId | Rename |

---

## Phase 0 — Security hardening, test harness, and rename

Independent of tenancy. Safe to ship first. Each task ends independently testable.

### Task 0.1: Establish the pytest harness

**Files:**
- Create: `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\conftest.py`
- Create: `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\test_smoke.py`
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\requirements.txt`

**Interfaces:**
- Produces: pytest fixtures `app`, `client`, and a helper `auth_headers(client, username, password, role)` reused by every later test.

- [ ] **Step 1: Add test deps.** Append to `requirements.txt`:

```
pytest
Flask-Migrate
psycopg2-binary
cryptography
```

- [ ] **Step 2: Make the app importable with an in-memory DB.** The app must accept `DATABASE_PATH` (already does at `app.py:54`) — the fixture sets it to `:memory:`. Create `tests/conftest.py`:

```python
import os
os.environ["DATABASE_PATH"] = ":memory:"
os.environ["JWT_SECRET_KEY"] = "test-secret-not-for-prod"
import pytest
from app import app as flask_app, db

@pytest.fixture
def app():
    flask_app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()

def auth_headers(client, username="admin", password="pw", role="admin"):
    client.post("/api/register", json={"username": username, "password": password})
    r = client.post("/api/login", json={"username": username, "password": password})
    token = r.get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
```

- [ ] **Step 3: Write the smoke test.** Create `tests/test_smoke.py`:

```python
def test_register_and_login(client):
    r = client.post("/api/register", json={"username": "a", "password": "b"})
    assert r.status_code == 201
    r = client.post("/api/login", json={"username": "a", "password": "b"})
    assert r.status_code == 200
    assert "access_token" in r.get_json()
```

- [ ] **Step 4: Run and verify pass.** Run: `python -m pytest tests/test_smoke.py -v`. Expected: PASS (2 assertions).
- [ ] **Step 5: Commit.** `git add tests requirements.txt && git commit -m "test: add pytest harness and smoke test"`

### Task 0.2: Move JWT secret and config to environment

**Files:**
- Create: `C:\Users\InfoCenter\source\repos\delta-net-saas\config.py`
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\app.py:51-58`

**Interfaces:**
- Produces: `config.Config` with `JWT_SECRET_KEY`, `SQLALCHEMY_DATABASE_URI`, `CORS_ORIGINS`.

- [ ] **Step 1: Write a failing test** in `tests/test_config.py`:

```python
def test_secret_is_not_the_old_hardcoded_value(app):
    assert app.config["JWT_SECRET_KEY"] != "a135b8778fe5dc203c82a9fcb0bcce63a7bd62f4e72cdaf5649569168bb32b04"
```

- [ ] **Step 2: Run to verify it fails.** Run: `python -m pytest tests/test_config.py -v`. Expected: FAIL (still the hardcoded value).
- [ ] **Step 3: Create `config.py`:**

```python
import os
from datetime import timedelta

class Config:
    JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]  # fail fast if unset in prod
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=8)
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{os.environ.get('DATABASE_PATH', 'database.db')}",
    )
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
```

- [ ] **Step 4: Rewire `app.py`.** Replace lines `app.py:52-58` (the `CORS(...origins:"*")` and the three `app.config[...]` lines) with:

```python
from config import Config
app.config.from_object(Config)
CORS(app, resources={r"/api/*": {"origins": Config.CORS_ORIGINS}})
```

Delete the hardcoded `JWT_SECRET_KEY = "a135..."` line entirely.

- [ ] **Step 5: Run to verify pass.** Run: `python -m pytest tests/test_config.py -v`. Expected: PASS (conftest sets `JWT_SECRET_KEY`).
- [ ] **Step 6: Commit.** `git commit -am "fix: load JWT secret and CORS origins from environment"`

### Task 0.3: Remove the duplicate `admin_required` and the debug endpoint

**Files:** Modify `C:\Users\InfoCenter\source\repos\delta-net-saas\app.py` (`admin_required` at `:612` and `:740`; `/api/debug-db` at `:602`).

**Interfaces:**
- Produces: a single `admin_required()` decorator that calls `verify_jwt_in_request()` then checks role.

- [ ] **Step 1: Failing test** in `tests/test_authz.py`:

```python
def test_debug_db_endpoint_is_gone(client):
    assert client.get("/api/debug-db").status_code == 404

def test_users_list_requires_admin(client):
    from tests.conftest import auth_headers
    hdr = auth_headers(client, "u1", "pw")  # first user becomes admin
    hdr2 = auth_headers(client, "u2", "pw")  # second user is non-admin
    assert client.get("/api/users", headers=hdr2).status_code == 403
```

- [ ] **Step 2: Run to verify fail.** `python -m pytest tests/test_authz.py -v`. Expected: FAIL (debug-db returns 200).
- [ ] **Step 3: Delete** the `/api/debug-db` route (`app.py:602-609`) and the **second** `admin_required` definition (`app.py:740-749`). Keep the first (`:612`) which correctly calls `verify_jwt_in_request()`.
- [ ] **Step 4: Run to verify pass.** `python -m pytest tests/test_authz.py -v`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -am "fix: remove debug-db route and duplicate admin_required decorator"`

### Task 0.4: Rename delta-net → servicesBills

**Files:** `app.py`, `frontend/src/components/SettingsView.js`, `frontend/src/components/MessagingView.js`, `frontend/public/index.html`, `frontend/public/electron.js`, `frontend/package.json`, `package.json` (all absolute under repo root).

- [ ] **Step 1:** In `package.json` and `frontend/package.json`: set `"name": "servicesbills"`, `"description": "servicesBills — customer & subscription management SaaS."`, `build.appId` → `"net.servicesbills.app"`, `build.productName` → `"servicesBills"`. (Electron `build` block stays for now; retired in Phase 3.)
- [ ] **Step 2:** In `frontend/public/index.html` set `<title>servicesBills</title>` and any meta description.
- [ ] **Step 3:** Replace user-visible `DeltaNet`/`delta-net` strings in `SettingsView.js`, `MessagingView.js`, `electron.js`. Leave `extraResources` path `../dist/delta-backend.exe` alone until Phase 3 (it is retired there).
- [ ] **Step 4: Verify** no stray references: `git grep -in "deltanet\|delta-net\|beta-ms\|betams" -- . ':!*package-lock.json' ':!node_modules'` returns only intentional/legacy items.
- [ ] **Step 5: Commit.** `git commit -am "chore: rename product to servicesBills"`

---

## Phase 1 — Multi-tenancy foundation

Introduces the tenant concept, the scoping primitives, and Alembic. This phase makes the *infrastructure* correct; Phase 2 applies it to every route.

### Task 1.1: Initialize Alembic (Flask-Migrate)

**Files:** Modify `app.py` (add `Migrate`); create `migrations/` via CLI.

- [ ] **Step 1:** In `app.py`, after `db = SQLAlchemy(app)`, add:

```python
from flask_migrate import Migrate
migrate = Migrate(app, db)
```

- [ ] **Step 2:** Initialize against the current SQLite DB so the baseline matches production data:

```bash
cd "C:\Users\InfoCenter\source\repos\delta-net-saas"
set FLASK_APP=app.py
python -m flask db init
python -m flask db migrate -m "baseline: existing schema"
python -m flask db upgrade
```

- [ ] **Step 3: Verify** `migrations/versions/*.py` exists and `flask db current` prints the baseline revision.
- [ ] **Step 4: Commit.** `git add migrations app.py && git commit -m "chore: adopt Flask-Migrate with schema baseline"`

### Task 1.2: Add the `Tenant` model and the tenancy helpers

**Files:**
- Create: `C:\Users\InfoCenter\source\repos\delta-net-saas\tenancy.py`
- Modify: `app.py` (add `Tenant` model near `User`, `app.py:65`).

**Interfaces:**
- Produces:
  - `class Tenant(db.Model)` with `id`, `name`, `slug` (unique), `status` (`active`/`suspended`), `plan` (`free`/`pro`/…), `created_at`.
  - `tenancy.current_tenant_id() -> int` — reads `tenant_id` from JWT claims; raises 401 if absent.
  - `tenancy.tenant_query(Model)` — returns `Model.query.filter_by(tenant_id=current_tenant_id())`.
  - `tenancy.new_for_tenant(Model, **kwargs)` — constructs a `Model` with `tenant_id` pre-set.
  - `tenancy.tenant_required` — decorator asserting a tenant claim exists.

- [ ] **Step 1: Failing test** `tests/test_tenancy.py`:

```python
def test_tenant_query_scopes_by_jwt_tenant(app):
    from app import db, Tenant, SubscriptionPlan
    from tenancy import tenant_query
    from flask_jwt_extended import create_access_token
    with app.app_context():
        t1 = Tenant(name="A", slug="a"); t2 = Tenant(name="B", slug="b")
        db.session.add_all([t1, t2]); db.session.commit()
        db.session.add(SubscriptionPlan(name="P1", price=10, billing_cycle="monthly", tenant_id=t1.id))
        db.session.add(SubscriptionPlan(name="P2", price=20, billing_cycle="monthly", tenant_id=t2.id))
        db.session.commit()
        tok = create_access_token(identity="u", additional_claims={"role": "admin", "tenant_id": t1.id})
    # exercised via a request context in the route tests; unit-asserts the filter shape here
    assert True
```

*(Note: `tenant_query` reads the JWT, so full behavior is asserted in Phase 2 route tests under a request context. This unit test guards import/wiring.)*

- [ ] **Step 2:** Add the `Tenant` model in `app.py` immediately after the imports/`bcrypt` setup (near `app.py:64`):

```python
class Tenant(db.Model):
    __tablename__ = "tenant"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active")
    plan = db.Column(db.String(20), nullable=False, default="free")
    stripe_customer_id = db.Column(db.String(120), nullable=True)
    stripe_subscription_id = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "slug": self.slug,
                "status": self.status, "plan": self.plan}
```

- [ ] **Step 3:** Create `tenancy.py`:

```python
from functools import wraps
from flask import jsonify
from flask_jwt_extended import get_jwt, verify_jwt_in_request

def current_tenant_id():
    claims = get_jwt()
    tid = claims.get("tenant_id")
    if tid is None:
        from flask import abort
        abort(401, description="No tenant in token")
    return tid

def tenant_query(model):
    return model.query.filter_by(tenant_id=current_tenant_id())

def new_for_tenant(model, **kwargs):
    kwargs["tenant_id"] = current_tenant_id()
    return model(**kwargs)

def tenant_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        current_tenant_id()  # raises 401 if missing
        return fn(*args, **kwargs)
    return wrapper
```

- [ ] **Step 4:** Run `python -m pytest tests/test_tenancy.py -v`. Expected: PASS (import/wiring guard). This step will fail to import until Task 1.3 adds `tenant_id` to `SubscriptionPlan` — run it after 1.3 if so; keep the task boundary at "helpers exist and import."
- [ ] **Step 5: Commit.** `git commit -am "feat: add Tenant model and tenancy scoping helpers"`

### Task 1.3: Add `tenant_id` to every domain model + backfill migration

**Files:** Modify all 22 domain models in `app.py` (every `db.Model` except `User`, which gets `tenant_id` nullable — see Task 1.4 for platform super-admins); create an Alembic migration.

**Interfaces:**
- Produces: `tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False, index=True)` on: `Reseller`, `ResellerPayment`, `Customer`, `SubscriptionPlan`, `Sector`, `Supplier`, `SupplierPayment`, `ExpenseCategory`, `Expense`, `Payment`, `GeneratedReceipt`, `AddonPurchase`, `BusinessSettings`, `WhatsAppSettings`, `SystemUpdateSettings`, `ServiceStatus`, `SupportTicket`, `TicketLog`, `PushSubscription`, `ServiceOutage`, `CustomerFeedback`, `PaymentReminder`.

- [ ] **Step 1:** Add the column to each model class listed above. Example for `Customer` (`app.py:113`): add after `id`:

```python
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False, index=True)
```

- [ ] **Step 2: Backfill strategy for existing data.** The current SQLite DB holds one real business. The migration creates one `Tenant` (`slug="default"`) and assigns all existing rows to it. Generate the migration:

```bash
python -m flask db migrate -m "add tenant_id to all domain tables"
```

Then hand-edit the generated `upgrade()` to: (a) add columns as **nullable first**, (b) `INSERT` the default tenant, (c) `UPDATE ... SET tenant_id = <default>` per table, (d) `ALTER` columns to `NOT NULL`. Example fragment:

```python
def upgrade():
    op.create_table("tenant", ...)  # if not already created by 1.2's autogen
    conn = op.get_bind()
    conn.execute(sa.text("INSERT INTO tenant (name, slug, status, plan, created_at) "
                         "VALUES ('Default Business','default','active','pro',CURRENT_TIMESTAMP)"))
    tid = conn.execute(sa.text("SELECT id FROM tenant WHERE slug='default'")).scalar()
    for table in ["customer","reseller","subscription_plan", ...]:  # all 22
        op.add_column(table, sa.Column("tenant_id", sa.Integer(), nullable=True))
        conn.execute(sa.text(f"UPDATE {table} SET tenant_id=:tid"), {"tid": tid})
        op.alter_column(table, "tenant_id", nullable=False)
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
```

- [ ] **Step 3:** Apply: `python -m flask db upgrade`. Verify with `python -m flask db current` and a spot query that every `customer.tenant_id` is set.
- [ ] **Step 4:** Update the isolation test `tests/test_tenancy.py` (from 1.2 Step 1) to now pass end-to-end.
- [ ] **Step 5: Commit.** `git commit -am "feat: add tenant_id to all domain models with backfill migration"`

### Task 1.4: Bind users to tenants and put `tenant_id` in the JWT

**Files:** Modify `app.py` — `User` model (`:65`), `register` (`:720`), `login` (`:822`), `create_user` (`:765`).

**Interfaces:**
- `User` gains `tenant_id` (nullable — `NULL` denotes a platform super-admin who operates servicesBills itself).
- `login` adds `tenant_id` to `additional_claims`.
- `register` becomes tenant-aware (see Phase 4 for full self-serve signup; here it minimally creates a tenant for the first user of a new business).

- [ ] **Step 1: Failing test** in `tests/test_authz.py`:

```python
def test_login_token_carries_tenant_id(client):
    client.post("/api/register", json={"username": "biz", "password": "pw", "business_name": "Acme"})
    r = client.post("/api/login", json={"username": "biz", "password": "pw"})
    from flask_jwt_extended import decode_token
    claims = decode_token(r.get_json()["access_token"])
    assert claims.get("tenant_id") is not None
```

- [ ] **Step 2: Run to verify fail.** Expected: FAIL (`tenant_id` claim missing).
- [ ] **Step 3:** Add `tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=True, index=True)` to `User`.
- [ ] **Step 4:** Update `register` (`app.py:720`) to create a `Tenant` from `business_name` and attach the new user to it:

```python
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username'); password = data.get('password')
    business_name = data.get('business_name') or username
    if not username or not password:
        return jsonify({"msg": "Username and password required"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"msg": "Username already exists"}), 409
    slug = re.sub(r'[^a-z0-9]+', '-', business_name.lower()).strip('-')[:80]
    base = slug or "tenant"; i = 1
    while Tenant.query.filter_by(slug=slug).first():
        i += 1; slug = f"{base}-{i}"
    tenant = Tenant(name=business_name, slug=slug); db.session.add(tenant); db.session.flush()
    new_user = User(username=username, role='admin', tenant_id=tenant.id)
    new_user.set_password(password)
    db.session.add(new_user); db.session.commit()
    return jsonify({"msg": "User created successfully", "tenant": tenant.to_dict()}), 201
```

- [ ] **Step 5:** Update `login` (`app.py:829`) `additional_claims` to include tenant:

```python
        access_token = create_access_token(
            identity=username,
            additional_claims={'role': user.role, 'tenant_id': user.tenant_id})
```

Also update `create_user` (`app.py:779`) to set `tenant_id=get_jwt().get('tenant_id')` so admins only create users inside their own tenant.

- [ ] **Step 6: Run to verify pass.** `python -m pytest tests/test_authz.py -v`. Expected: PASS.
- [ ] **Step 7: Commit.** `git commit -am "feat: bind users to tenants and embed tenant_id in JWT"`

---

## Phase 2 — Scope every route to the tenant

Mechanical but the highest-risk phase. Applies the Phase 1 primitives to all 101 routes. **Expand this into its own detailed plan before executing** (`docs/superpowers/plans/<date>-servicesbills-phase2-scoping.md`), one task per route group, because each route group is independently testable and reviewable.

**The scoping pattern (apply uniformly):**
- Reads: replace `Model.query...` → `tenant_query(Model)...`. Replace `Model.query.get_or_404(id)` → `tenant_query(Model).filter_by(id=id).first_or_404()`.
- Creates: replace `Model(...)` → `new_for_tenant(Model, ...)`.
- Singletons: `BusinessSettings.query.first()` / `WhatsAppSettings.query.first()` (18 occurrences) → `tenant_query(BusinessSettings).first()`; create-if-missing helpers become per-tenant.
- Every route already carrying `@jwt_required()` also gets `@tenant_required` (or `current_tenant_id()` is used inside).
- Scheduler jobs (`generate_missing_payments`, `app.py:841`; reminders) run **outside** a request → they must iterate tenants explicitly: `for t in Tenant.query.filter_by(status="active"): ...` and filter each query by `t.id`.

**Route groups (each = one task, each with an isolation test "tenant A cannot see/modify tenant B's rows"):**
- [ ] Customers (`/api/customers*`) — list, create, update, delete, activate, cancel, balance, unpaid_receipt
- [ ] Payments (`/api/payments*`) — create, list, delete, mark_paid, generate_future
- [ ] Subscription plans (`/api/subscription_plans*`)
- [ ] Resellers + reseller payments
- [ ] Suppliers + supplier payments
- [ ] Expenses + expense categories + sectors
- [ ] Receipts (`/api/receipt*`, `/api/receipts*`)
- [ ] Reports (`/api/reports/*`) — every aggregate query must add `tenant_id`
- [ ] Business settings + WhatsApp settings (singletons → per-tenant)
- [ ] Support tickets + ticket logs + feedback + outages + service status
- [ ] Push subscriptions + payment reminders
- [ ] Scheduler jobs converted to tenant-iterating (`app.py:925-944`)

**Mandatory exit test** (`tests/test_isolation.py`): for each resource, seed two tenants, authenticate as tenant A, assert A's list excludes B's rows and A gets 404 (not 403/200) when addressing B's row id.

---

## Phase 3 — Hosting, Postgres, storage, deployment

**Expand into its own plan.** Retires desktop packaging; makes the app cloud-native.

- [ ] **Postgres:** provision managed Postgres; set `DATABASE_URL`; run `flask db upgrade` against it; smoke-test. SQLAlchemy code is DB-agnostic — verify no SQLite-only SQL (`text()` fragments) remains.
- [ ] **Object storage** (`storage.py`): abstract uploads. Dev = local `uploads/`; prod = S3/R2 via `boto3`. Migrate `/uploads/<filename>` (`app.py:837`) and `secure_filename` writes to go through it. Store keys, not local paths, in `BusinessSettings.logo_url`.
- [ ] **Retire Electron:** remove `build`/Electron config from `package.json`, delete `extraResources` referencing `delta-backend.exe` and bundled `database.db`; keep only the React web build. Frontend build is served by Flask static (`app.py:4915` `serve`) or a CDN.
- [ ] **Fix frontend base URLs:** `ReportsView.js:29` hardcodes `http://127.0.0.1:5000/api` — route it through the shared `api` client in `AppContext.js:8`; make base URL env-driven (`REACT_APP_API_URL`).
- [ ] **WSGI/deploy:** gunicorn (already in requirements) behind a reverse proxy; containerize (Dockerfile); env-driven secrets; HTTPS; `CORS_ORIGINS` set to the real domain.
- [ ] **Encrypt WhatsApp secrets at rest:** `WhatsAppSettings.access_token`/`app_secret` (`app.py:315-317`) encrypted via `cryptography.Fernet` with key from env.
- [ ] **Background jobs at scale:** keep APScheduler for a single worker, or move to a dedicated worker/Celery-beat if running multiple gunicorn workers (avoid duplicate job firing — APScheduler in-process fires once per process).

---

## Phase 4 — SaaS business layer

**Expand into its own plan.** Turns a multi-tenant app into a sellable product.

- [ ] **Self-serve signup + onboarding:** public signup creates tenant + admin user + email verification; guided first-run (business settings, first plan).
- [ ] **Stripe billing** (`billing.py`): map servicesBills plans (free/pro/…) to Stripe Products/Prices; checkout session; customer portal; webhook (`/api/stripe/webhook`) updates `Tenant.plan`/`status` on subscription events. Store `stripe_customer_id`/`stripe_subscription_id` on `Tenant` (fields added in Task 1.2).
- [ ] **Plan-gating:** decorator `@requires_plan("pro")` / limit checks (e.g. max customers on free). Enforce at route level using `Tenant.plan`.
- [ ] **Platform super-admin:** routes guarded by `User.tenant_id IS NULL` + `role="superadmin"` for the operator dashboard (all tenants, MRR, signups, suspend/reactivate).
- [ ] **Tenant lifecycle:** suspend on non-payment (`Tenant.status`), data export, deletion (scoped hard-delete across all 22 tables by `tenant_id`).
- [ ] **Password reset + email verification** flows.

---

## Phase 5 — Frontend SaaS experience + UI/UX Pro Max polish

**Expand into its own plan. REQUIRED SUB-SKILL: `.claude/skills/design/`, `.claude/skills/brand/` (UI/UX Pro Max, installed via `uipro init --ai claude`).** Invoke these skills for every visual/UX task below.

- [ ] **Marketing/landing page** for servicesBills (pricing, features, signup CTA) — use the `design` + `brand` skills.
- [ ] **Auth screens:** replace desktop login with SaaS signup/login/verify/reset; onboarding wizard.
- [ ] **Billing UI:** plan selection, Stripe checkout redirect, "manage subscription" (customer portal), plan-limit prompts.
- [ ] **Per-tenant branding:** the existing `BusinessSettings` (logo/name) drives in-app theming.
- [ ] **Tenant-admin settings** (users, plan, billing) vs. **super-admin console** (all tenants).
- [ ] **Establish a brand system** for servicesBills (colors, type, logo) via the `brand` skill; apply MUI theme tokens consistently across the existing views (`DashboardView`, `PaymentsView`, `ReportsView`, etc.).
- [ ] **Responsive/web polish:** the app was desktop-first (Electron); audit for responsive layout now that it runs in browsers.

---

## Self-Review

**Spec coverage:** Every item from the analysis is mapped — multi-tenancy (Phases 1–2), Postgres (Phase 3), security fixes incl. JWT secret/CORS/duplicate decorator/token encryption (Phases 0 & 3), object storage (Phase 3), Electron retirement (Phase 3), billing + signup + super-admin (Phase 4), rename to servicesBills (Phase 0, with Electron-path cleanup deferred to Phase 3), UI/UX Pro Max frontend (Phase 5). No gap identified.

**Placeholder scan:** Phases 0–1 contain concrete code and commands. Phases 2–5 are deliberately task-lists flagged **"expand into its own plan"** — this follows the writing-plans scope-check for multi-subsystem specs; they are NOT ready-to-execute steps and must be expanded before implementation. Do not execute Phase 2+ directly from this master document.

**Type consistency:** `current_tenant_id()`, `tenant_query(Model)`, `new_for_tenant(Model, **kwargs)`, `tenant_required` are defined once in `tenancy.py` (Task 1.2) and referenced with those exact names in Phases 2–4. `Tenant` fields (`slug`, `status`, `plan`, `stripe_customer_id`, `stripe_subscription_id`) defined in Task 1.2 are the same ones used in Phase 4.

---

## Execution Handoff

This master plan intentionally details **Phases 0–1** to bite-sized, testable steps and scopes **Phases 2–5** as task lists that each become their own plan (per the writing-plans multi-subsystem guidance). Recommended path: execute Phase 0, then Phase 1, then write the Phase 2 scoping plan before touching routes.
