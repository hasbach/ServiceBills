# servicesBills — Phase 3: Postgres, Storage & Hosting (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **Prerequisite:** Phases 1+2 merged to `main` (multi-tenant, tenant-isolated, 19 tests green). Do Phase 3 on a `phase-3-hosting` branch off `main`.

**Goal:** Make servicesBills cloud-native: move the persistent store from SQLite to PostgreSQL, reconcile the deferred schema constraints (FKs + composite-unique), move uploads to object storage, encrypt tenant secrets at rest, retire the Electron desktop packaging, and deploy the React SPA + Flask API as a hosted web app.

**Architecture:** One managed Postgres instance (shared-DB multi-tenancy, unchanged). Alembic remains the schema source of truth; `create_all` is retired. Uploads move behind a `storage.py` abstraction (local disk in dev, S3-compatible object storage in prod) with tenant-prefixed keys. Per-tenant WhatsApp credentials are encrypted with Fernet. The app runs under gunicorn (containerized), with the APScheduler jobs driven by a single runner to avoid duplicate firing across workers.

**Tech Stack:** PostgreSQL · SQLAlchemy · Flask-Migrate/Alembic · psycopg2 · boto3 (S3/R2) · cryptography (Fernet) · gunicorn · Docker · React 18.

## Global Constraints

- **All paths absolute** under `C:\Users\InfoCenter\source\repos\delta-net-saas\`; `delta-net` base folder untouched.
- **No behavior change to tenant isolation** — everything from Phases 1+2 must still hold; the isolation test suite must stay green throughout.
- **Secrets from env only:** `DATABASE_URL`, `FERNET_KEY`, `STORAGE_*`, `JWT_SECRET_KEY`. No secret in source or committed config.
- **Schema changes via Alembic only.** After Task 3.6, `create_all` is gone; fresh databases are built by `flask db upgrade`.
- **Data preservation:** the existing `instance/database.db` (254 customers, 1971 payments, all under the `default` tenant) must migrate to Postgres with IDs and relationships intact.
- **Migrations must run on BOTH SQLite (dev/CI in-memory tests) and Postgres (prod).** Keep `render_as_batch=True`; use a metadata naming convention so constraints are namable.
- **Tests:** in-memory SQLite via `tests/conftest.py` stays the CI path; add Postgres-specific verification as a manual/CI smoke, not a unit-test dependency.
- **Frequent commits**, one per task.

---

## Task 3.0: Metadata naming convention + config surface

Sets up clean constraint naming (so Alembic can add/drop FKs and unique constraints by name on Postgres) and the new env config.

**Files:** `app.py` (SQLAlchemy metadata), `config.py`.

**Interfaces:** Produces `config.Config.FERNET_KEY`, `STORAGE_BACKEND`, `STORAGE_BUCKET`, `STORAGE_PREFIX`, S3 creds; a `MetaData` naming convention on `db`.

- [ ] **Step 1:** Configure a naming convention BEFORE models are defined. In `app.py`, replace `db = SQLAlchemy(app)` with:
```python
from sqlalchemy import MetaData
_naming = MetaData(naming_convention={
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})
db = SQLAlchemy(app, metadata=_naming)
```
- [ ] **Step 2:** Add config fields to `config.py`:
```python
    FERNET_KEY = os.environ.get("FERNET_KEY")  # required in prod; see Task 3.5
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")  # "local" | "s3"
    STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET")
    STORAGE_PREFIX = os.environ.get("STORAGE_PREFIX", "uploads")
    AWS_REGION = os.environ.get("AWS_REGION")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")  # set for Cloudflare R2 / MinIO
```
- [ ] **Step 3:** Run `python -m pytest -q`. Expected: 19 pass (naming convention doesn't change behavior for existing tests). If autogenerate later renames existing indexes, that is expected and handled in Task 3.1.
- [ ] **Step 4: Commit.** `git commit -am "chore(db): add metadata naming convention and Phase 3 config surface"`

## Task 3.1: FK-constraint reconciliation migration

Adds the 22 `tenant_id` foreign keys (+ `user.tenant_id`) intentionally omitted from the Phase-1 SQLite migration. On Postgres these apply as real ALTERs; on SQLite (tests build via create_all, which already includes the FKs) this migration is only exercised on Postgres.

**Files:** new `migrations/versions/<rev>_add_tenant_fks.py`.

- [ ] **Step 1:** Autogenerate against the SQLite DB to capture the FK diffs: `DISABLE_AUTO_CREATE_ALL=1 JWT_SECRET_KEY=dev flask db migrate -m "add tenant_id FKs"`. It will emit `create_foreign_key` (and possibly index renames) for all 22 tables + user.
- [ ] **Step 2:** Hand-review the generated file. Ensure each FK is `op.create_foreign_key(None, "<table>", "tenant", ["tenant_id"], ["id"])` inside a `with op.batch_alter_table("<table>") as b:` block (batch needed for SQLite; harmless on Postgres). Remove any spurious index churn from the naming-convention change unless intended.
- [ ] **Step 3: Verify the full chain builds on a fresh SQLite DB:** `rm -f <scratch>/fk_chain.db && DISABLE_AUTO_CREATE_ALL=1 DATABASE_URL="sqlite:///<scratch>/fk_chain.db" JWT_SECRET_KEY=dev flask db upgrade` → no errors; `flask db current` at the new head.
- [ ] **Step 4: Commit.** `git commit -am "feat(db): add tenant_id foreign keys (Phase 3 reconciliation)"`

## Task 3.2: Composite-unique swap for Sector / ExpenseCategory

The models already declare `UniqueConstraint('tenant_id', 'name')` (Phase 2); the real DB still has the old global unique. This migration drops the global unique and adds the composite.

**Files:** new `migrations/versions/<rev>_sector_category_composite_unique.py`.

- [ ] **Step 1:** Autogenerate (`flask db migrate -m "per-tenant unique sector/category names"`). Expect `drop_constraint` (old unnamed unique) + `create_unique_constraint("uq_sector_tenant_name", ...)` for both tables.
- [ ] **Step 2:** On SQLite the old constraint is unnamed; wrap in `with op.batch_alter_table("sector", recreate="always") as b:` and, since batch rebuilds from reflected schema, explicitly `b.create_unique_constraint("uq_sector_tenant_name", ["tenant_id", "name"])` and drop the old one via the batch's reflected constraints. On Postgres (with the naming convention) the drop-by-name works directly. Verify both engines: build a fresh SQLite chain (as in 3.1 Step 3) AND confirm the name-reuse isolation test (`tests/test_iso_resources.py::test_sector_and_category_isolation_and_name_reuse`) still passes.
- [ ] **Step 3: Commit.** `git commit -am "feat(db): per-tenant unique names for sectors and expense categories"`

## Task 3.3: Provision Postgres and migrate data from SQLite

**Files:** new `scripts/migrate_sqlite_to_postgres.py`.

- [ ] **Step 1 (ops):** Provision managed Postgres (RDS / Supabase / Neon). Obtain `DATABASE_URL` (`postgresql+psycopg2://user:pass@host:5432/servicesbills`). Do NOT commit it.
- [ ] **Step 2:** Build the schema on Postgres: `DISABLE_AUTO_CREATE_ALL=1 DATABASE_URL=<pg> JWT_SECRET_KEY=<secret> flask db upgrade`. Verify all 25 tables + `alembic_version` at head.
- [ ] **Step 3:** Write `scripts/migrate_sqlite_to_postgres.py` that copies every table from `instance/database.db` to Postgres preserving primary keys and FK order (tenant → users/plans/resellers/suppliers/… → customers → payments → dependent rows). After load, reset each Postgres sequence: `SELECT setval(pg_get_serial_sequence('<t>','id'), (SELECT MAX(id) FROM <t>))`.
```python
# sketch: iterate tables in dependency order; SELECT * from sqlite, bulk INSERT into pg,
# then fix sequences. Wrap in one transaction; abort on any FK violation.
```
- [ ] **Step 4: Verify parity:** row counts per table match SQLite (`customer=254`, `payment=1971`, `generated_receipt=1685`, …), and a spot-check that `tenant`=1 row (`default`), all `tenant_id` non-null.
- [ ] **Step 5: Commit** the script (not the DB URL). `git commit -am "feat(db): SQLite->Postgres data migration script"`

## Task 3.4: Object storage abstraction for uploads

**Files:** new `storage.py`; modify `app.py` upload/serve sites (`:705-713`, `:919-921`, `:2458-2460`, and logo_url refs `:343,:2200,:2311,:4637`).

**Interfaces:** `storage.save(file_storage, tenant_id) -> key`, `storage.url(key) -> str`, `storage.open(key) -> bytes/stream`. Keys are tenant-prefixed: `f"{STORAGE_PREFIX}/{tenant_id}/{uuid4()}-{secure_filename(name)}"`.

- [ ] **Step 1: Failing test** `tests/test_storage.py`: local backend saves under a tenant prefix and round-trips bytes; two tenants with the same filename get distinct keys.
- [ ] **Step 2:** Implement `storage.py` with a `LocalBackend` (writes under `UPLOAD_FOLDER`) and `S3Backend` (boto3, honoring `S3_ENDPOINT_URL` for R2/MinIO), selected by `Config.STORAGE_BACKEND`.
- [ ] **Step 3:** Rewire the logo upload route to `key = storage.save(file, current_tenant_id())` and store the **key** (not a bare filename) in `BusinessSettings.logo_url`. Replace the `/uploads/<filename>` handler and every `f"/uploads/{...}"` response with `storage.url(key)`. This closes the cross-tenant upload-guess gap (Phase 2 note): keys are namespaced by tenant and served via signed/backend URLs.
- [ ] **Step 4:** Run `python -m pytest -q`. Expected: green (local backend). Commit.

## Task 3.5: Encrypt WhatsApp secrets at rest

**Files:** `app.py` `WhatsAppSettings` (`app_secret :367`, `access_token :368`); a small `crypto.py`.

- [ ] **Step 1: Failing test** `tests/test_crypto.py`: `encrypt`/`decrypt` round-trip; ciphertext ≠ plaintext.
- [ ] **Step 2:** `crypto.py`: `Fernet(Config.FERNET_KEY)` wrappers `encrypt(str)->str` / `decrypt(str)->str` (no-op passthrough if `FERNET_KEY` unset, for dev, with a logged warning).
- [ ] **Step 3:** Store `access_token`/`app_secret` encrypted: encrypt on write (settings POST + webhook subscribe), decrypt on read (send paths, webhook). Add a one-off migration/script to encrypt existing plaintext values.
- [ ] **Step 4:** Run suite; commit.

## Task 3.6: Retire create_all as schema source

**Files:** `app.py` (`:524` bootstrap block + the `DISABLE_AUTO_CREATE_ALL` gate; the ~20 home-grown `ALTER TABLE` try/excepts).

- [ ] **Step 1:** Remove the import-time `db.create_all()` and the home-grown `ALTER TABLE` migration block (Alembic now owns the schema). Keep `tests/conftest.py` building the test DB via `db.create_all()` (that's the CI path and is fine).
- [ ] **Step 2:** Verify: a fresh Postgres built only by `flask db upgrade` boots the app and serves requests; the suite stays green (conftest still uses create_all). Commit.

## Task 3.7: Retire Electron; env-driven frontend API base URL

**Files:** `package.json`, `frontend/package.json` (`build`/Electron blocks, `extraResources` `delta-backend.exe`), `frontend/public/electron.js`, `frontend/public/preload.js`; `frontend/src/context/AppContext.js:7-9`, `frontend/src/components/ReportsView.js:29` (hardcoded `http://127.0.0.1:5000/api`), `frontend/src/components/SettingsView.js:31`.

- [ ] **Step 1:** Remove the Electron `build` config, `extraResources`, and electron/preload entry points; drop electron devDeps. The React SPA becomes the sole frontend.
- [ ] **Step 2:** Make the API base URL env-driven: a single `API_BASE_URL = process.env.REACT_APP_API_URL || ''` used by the shared axios client; route `ReportsView.js` through that shared client (fixes the hardcoded localhost prod bug).
- [ ] **Step 3:** `npm run build` succeeds; the built SPA served by Flask static (`app.py:5035` serve) loads and talks to `/api`. Commit.

## Task 3.8: WSGI containerization + single-runner scheduler

**Files:** new `Dockerfile`, `.dockerignore`; `app.py` scheduler start (`:1015-1017`).

- [ ] **Step 1:** `Dockerfile`: install deps, build the React bundle, run `gunicorn -w <N> app:app`. Env: `DATABASE_URL`, `JWT_SECRET_KEY`, `FERNET_KEY`, `STORAGE_*`, `CORS_ORIGINS`.
- [ ] **Step 2: Scheduler correctness under multiple workers.** APScheduler starts per-process → with `-w N` the daily jobs fire N times. Gate the scheduler to a single runner: start it only when `os.environ.get("RUN_SCHEDULER") == "1"` (run one dedicated container/worker with that flag), OR acquire a Postgres advisory lock before scheduling. Document the chosen approach.
- [ ] **Step 3:** Smoke: `docker build` + run with a test `DATABASE_URL`; hit `/api/login` and a scoped route. Commit.

## Task 3.9: Validate support-ticket customer_id is in-tenant (Phase 2 carry-over)

**Files:** `app.py` `create_support_ticket` (`:3274`).

- [ ] **Step 1: Failing test:** tenant A creating a ticket with tenant B's `customer_id` must 404/400, not succeed.
- [ ] **Step 2:** Fetch the customer via `tenant_query(Customer).filter_by(id=data['customer_id']).first_or_404()` before creating the ticket. Commit.

---

## Notes / deferred to later phases

- Signed URLs / private buckets and CDN fronting for uploads can be tightened in Phase 4/5; Task 3.4 establishes the tenant-namespaced abstraction.
- Connection pooling / PgBouncer sizing is an ops concern to tune after deploy.

## Self-Review

**Coverage:** All Phase 2 carry-overs are addressed — FKs (3.1), composite-unique swap (3.2), retire create_all (3.6), Electron + upload storage + support-ticket check (3.7/3.4/3.9). Plus the Phase-0/analysis items: Postgres (3.3), object storage (3.4), secret encryption (3.5), deploy (3.8), frontend base-URL bug (3.7).

**Placeholder scan:** Ops-dependent steps (provision Postgres, provision bucket, deploy) are explicitly marked `(ops)` and parameterized by env vars rather than hardcoded — this is intentional, not a placeholder. Code-bearing tasks (storage, crypto, support-ticket check, naming convention) carry concrete snippets and tests.

**Type consistency:** `storage.save/url/open`, `crypto.encrypt/decrypt`, and `Config` field names are defined once and referenced consistently. Migration revisions chain from the current head (`b9f…`→`c297…`→ the three new Phase-3 revisions).

## Execution Handoff

Suggested order: 3.0 → 3.1 → 3.2 (schema) → 3.6 (retire create_all) → 3.3 (Postgres + data) → 3.4/3.5 (storage/secrets) → 3.9 (ticket check) → 3.7/3.8 (frontend + deploy). Each task commits independently; the isolation suite must stay green throughout. When done, merge `phase-3-hosting` → `main`.
