# WhatsApp Template Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a tenant create, edit, delete, and track the Meta approval status of their own WhatsApp Cloud API message templates directly from ServiceBills, instead of using Meta Business Manager.

**Architecture:** A new tenant-scoped `WhatsAppTemplate` table caches each tenant's Meta templates locally (Meta stays the source of truth). Backend routes proxy create/edit/delete to Meta's Message Templates API using the tenant's existing `WhatsAppSettings.access_token`/`business_account_id`, and a manual sync route reconciles the local cache against Meta's live `GET`. The existing WhatsApp webhook handler gains a branch for Meta's `message_template_status_update` field so approval/rejection updates arrive in real time. A new Settings tab (`WhatsAppTemplatesManager.js`) provides the list + builder UI, and the existing 9 free-text "Approved Template Names" fields become dropdowns sourced from this new table.

**Tech Stack:** Flask + SQLAlchemy + Alembic (backend, all in `app.py`), React + MUI (frontend), `requests` for Meta Graph API calls, pytest + Flask test client for tests.

## Global Constraints

- Only `MARKETING` and `UTILITY` template categories are supported — `AUTHENTICATION` is rejected everywhere (server-side validation and the frontend builder never offer it).
- Editing is only allowed while a template's local `status` is `PENDING` or `REJECTED` — never `APPROVED` (create a new template instead).
- Every new route requires `@jwt_required()` + `@admin_or_finance_required()`, plus the tenant must have `WhatsAppSettings.mode == 'api'` and `plans.limits(current_tenant().plan)["whatsapp_api"]` true (the existing Pro-plan gate at `app.py:4624`, inherited, not duplicated with new logic).
- Every Meta API call uses the tenant's own `WhatsAppSettings.access_token`/`business_account_id`/`app_id` — no platform-level Meta credential is introduced anywhere in this plan.
- Every route that can fail against Meta parses `error.error_user_msg`/`error.message` from Meta's JSON error body and returns it verbatim to the tenant, via the shared `_parse_meta_error(resp)` helper (Task 2) — never a generic failure message.
- `WhatsAppTemplate` is added to both `TENANT_OWNED_MODELS` (`app.py:1018`) and `_TENANT_DELETE_ORDER` (`app.py:1708`) in the same task that introduces the model (Task 1) — this is the exact bug class already found and fixed twice before (`MonthlyProfitEstimate`, `BillingPaymentAttempt`), and Task 1 also adds a regression test guarding against it recurring.

---

## Task 1: `WhatsAppTemplate` model, migration, and tenant-delete wiring

**Files:**
- Modify: `app.py:851` (insert new model class after `WhatsAppSettings.to_dict()`, before `class ServiceStatus`)
- Modify: `app.py:1018-1028` (`TENANT_OWNED_MODELS`)
- Modify: `app.py:1708-1723` (`_TENANT_DELETE_ORDER`)
- Create: `migrations/versions/e1a9c4f7b3d2_add_whatsapp_template.py`
- Modify: `tests/test_lifecycle.py` (append regression test)

**Interfaces:**
- Produces: `WhatsAppTemplate` model with columns `id, tenant_id, name, language, category, status, rejected_reason, components, meta_template_id, created_at, updated_at` and a `to_dict()` method returning all of them (dates as `'%Y-%m-%d %H:%M:%S'` strings or `None`). All later tasks (2-5) read/write this model.

- [ ] **Step 1: Add the `WhatsAppTemplate` model**

Insert into `app.py` immediately after line 851 (`WhatsAppSettings.to_dict()`'s closing `}` and blank line), before `class ServiceStatus(db.Model):`:

```python
class WhatsAppTemplate(db.Model):
    """Local cache of a tenant's Meta WhatsApp message templates. Meta is the
    source of truth; this table exists for fast list rendering and as the
    target of the message_template_status_update webhook. Reconciled against
    Meta's live GET on manual refresh (POST /api/whatsapp/templates/sync).
    See docs/superpowers/specs/2026-08-28-whatsapp-template-management-design.md."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    language = db.Column(db.String(10), nullable=False)
    category = db.Column(db.String(20), nullable=False)  # 'MARKETING' | 'UTILITY'
    status = db.Column(db.String(20), nullable=False, default='PENDING')  # PENDING, APPROVED, REJECTED, PAUSED, DISABLED
    rejected_reason = db.Column(db.String(500), nullable=True)
    components = db.Column(db.JSON, nullable=False)
    meta_template_id = db.Column(db.String(64), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'language': self.language,
            'category': self.category,
            'status': self.status,
            'rejected_reason': self.rejected_reason,
            'components': self.components,
            'meta_template_id': self.meta_template_id,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None,
        }
```

- [ ] **Step 2: Write the failing regression test**

Append to `tests/test_lifecycle.py`:

```python
def test_tenant_owned_models_all_in_delete_order():
    """Guards against the exact bug found and fixed twice before (Phase 3:
    MonthlyProfitEstimate, Phase 4a: BillingPaymentAttempt) -- a model present
    in TENANT_OWNED_MODELS but missing from _TENANT_DELETE_ORDER causes a
    ForeignKeyViolation on Postgres (not SQLite, which doesn't enforce FKs)
    when a tenant is deleted. Known pre-existing gaps are excluded here (see
    the separate _TENANT_DELETE_ORDER cleanup follow-up, not part of this
    plan) so this test only guards against NEW regressions -- starting with
    WhatsAppTemplate, added by this task."""
    known_pre_existing_gaps = {
        appmod.Employee, appmod.SalaryCharge, appmod.SalaryPayment,
        appmod.UpstreamProvider, appmod.UpstreamProviderPayment, appmod.MikrotikServer,
        appmod.ExchangeRate,
    }
    missing = set(appmod.TENANT_OWNED_MODELS) - set(appmod._TENANT_DELETE_ORDER) - known_pre_existing_gaps
    assert missing == set(), f"Models missing from _TENANT_DELETE_ORDER: {missing}"
```

- [ ] **Step 3: Add `WhatsAppTemplate` to `TENANT_OWNED_MODELS` only, then run the test to verify it fails**

In `app.py:1018-1028`, change:
```python
TENANT_OWNED_MODELS = (
    Reseller, ResellerPayment, Customer, SubscriptionPlan, Sector, Supplier,
    SupplierPayment, ExpenseCategory, Expense, Payment, GeneratedReceipt,
    AddonPurchase, BusinessSettings, WhatsAppSettings,
    ServiceStatus, SupportTicket, TicketLog, PushSubscription, ServiceOutage,
    CustomerFeedback, PaymentReminder, UpgradeRequest, BillingPaymentAttempt,
    Employee, SalaryCharge, SalaryPayment,
    MonthlyProfitEstimate,
    UpstreamProvider, UpstreamProviderPayment, MikrotikServer,
    ExchangeRate,
)
```
to:
```python
TENANT_OWNED_MODELS = (
    Reseller, ResellerPayment, Customer, SubscriptionPlan, Sector, Supplier,
    SupplierPayment, ExpenseCategory, Expense, Payment, GeneratedReceipt,
    AddonPurchase, BusinessSettings, WhatsAppSettings, WhatsAppTemplate,
    ServiceStatus, SupportTicket, TicketLog, PushSubscription, ServiceOutage,
    CustomerFeedback, PaymentReminder, UpgradeRequest, BillingPaymentAttempt,
    Employee, SalaryCharge, SalaryPayment,
    MonthlyProfitEstimate,
    UpstreamProvider, UpstreamProviderPayment, MikrotikServer,
    ExchangeRate,
)
```

Run: `pytest tests/test_lifecycle.py::test_tenant_owned_models_all_in_delete_order -v`
Expected: FAIL — `WhatsAppTemplate` is now in `TENANT_OWNED_MODELS` but not yet in `_TENANT_DELETE_ORDER`.

- [ ] **Step 4: Add `WhatsAppTemplate` to `_TENANT_DELETE_ORDER`**

In `app.py:1708-1723`, change:
```python
_TENANT_DELETE_ORDER = [
    UpgradeRequest, BillingPaymentAttempt, PaymentReminder, GeneratedReceipt, AddonPurchase, TicketLog, SupportTicket,
    CustomerFeedback, ServiceStatus, Payment, ResellerPayment, SupplierPayment,
    Expense, Customer, ServiceOutage, PushSubscription, BusinessSettings,
    WhatsAppSettings, ExpenseCategory, Sector,
    SubscriptionPlan, Reseller, Supplier,
    MonthlyProfitEstimate,
]
```
to:
```python
_TENANT_DELETE_ORDER = [
    UpgradeRequest, BillingPaymentAttempt, PaymentReminder, GeneratedReceipt, AddonPurchase, TicketLog, SupportTicket,
    CustomerFeedback, ServiceStatus, Payment, ResellerPayment, SupplierPayment,
    Expense, Customer, ServiceOutage, PushSubscription, BusinessSettings,
    WhatsAppSettings, WhatsAppTemplate, ExpenseCategory, Sector,
    SubscriptionPlan, Reseller, Supplier,
    MonthlyProfitEstimate,
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_lifecycle.py::test_tenant_owned_models_all_in_delete_order -v`
Expected: PASS

- [ ] **Step 6: Create the migration**

Create `migrations/versions/e1a9c4f7b3d2_add_whatsapp_template.py`:

```python
"""add whatsapp_template table

Revision ID: e1a9c4f7b3d2
Revises: 1282420125d2
Create Date: 2026-08-28

Local cache of a tenant's Meta WhatsApp message templates (see
docs/superpowers/specs/2026-08-28-whatsapp-template-management-design.md).
Additive-only: one new table. Follows this repo's defensive-migration
discipline (existence checks, skip-with-NOTE rather than crash) per
c57bc44a51d0's documented rationale.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'e1a9c4f7b3d2'
down_revision = '1282420125d2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if 'whatsapp_template' not in existing_tables:
        op.create_table(
            'whatsapp_template',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('language', sa.String(length=10), nullable=False),
            sa.Column('category', sa.String(length=20), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
            sa.Column('rejected_reason', sa.String(length=500), nullable=True),
            sa.Column('components', sa.JSON(), nullable=False),
            sa.Column('meta_template_id', sa.String(length=64), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_whatsapp_template_tenant_id', 'whatsapp_template', ['tenant_id'])
        op.create_index('ix_whatsapp_template_meta_template_id', 'whatsapp_template', ['meta_template_id'])
    else:
        print("NOTE: whatsapp_template table already exists -- skipping create (nothing to do).")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'whatsapp_template' in set(inspector.get_table_names()):
        op.drop_table('whatsapp_template')
```

Run: `flask db upgrade` (against a local/dev database, not the in-memory test DB which uses `create_all()`)
Expected: migration applies cleanly, `whatsapp_template` table exists.

- [ ] **Step 7: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass (no regressions from the model/list changes).

- [ ] **Step 8: Commit**

```bash
git add app.py migrations/versions/e1a9c4f7b3d2_add_whatsapp_template.py tests/test_lifecycle.py
git commit -m "feat: add WhatsAppTemplate model, migration, and tenant-delete wiring"
```

---

## Task 2: `GET /api/whatsapp/templates` (local cache) and `POST /api/whatsapp/templates/sync`

**Files:**
- Modify: `app.py:4676-4724` (replace `get_meta_templates`)
- Create: `tests/test_whatsapp_templates.py`

**Interfaces:**
- Consumes: `WhatsAppTemplate` (Task 1), `tenant_query`, `new_for_tenant`, `admin_or_finance_required`, `plans.limits`, `current_tenant`.
- Produces: `_parse_meta_error(resp)` helper — used by every later task's Meta-facing route (3, 4, 5, 8). `GET /api/whatsapp/templates` returns `{'templates': [<WhatsAppTemplate.to_dict()>, ...]}`, all statuses included. `POST /api/whatsapp/templates/sync` returns `{'message': str, 'templates': [...]}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_whatsapp_templates.py`:

```python
import app as appmod
from tests.conftest import make_tenant


def _pro_api_mode(app, client, hdr, slug, **overrides):
    with app.app_context():
        appmod.Tenant.query.filter_by(slug=slug).update({"plan": "pro"})
        appmod.db.session.commit()
    payload = {"enabled": True, "mode": "api", "phone_number_id": "123",
               "business_account_id": "WABA1", "app_id": "APP1", "access_token": "tok"}
    payload.update(overrides)
    client.post("/api/whatsapp-settings", headers=hdr, json=payload)


class FakeResponse:
    def __init__(self, ok=True, status_code=200, json_data=None, text=""):
        self.ok = ok
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text or str(json_data)

    def json(self):
        return self._json


def test_get_templates_returns_all_statuses_from_local_cache(app, client):
    hdr = make_tenant(client, "Biz T1", "t1_admin")
    _pro_api_mode(app, client, hdr, "biz-t1")
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-t1").first().id
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=tid, name="pending_one", language="en", category="UTILITY",
            status="PENDING", components=[{"type": "BODY", "text": "hi"}]))
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=tid, name="rejected_one", language="en", category="MARKETING",
            status="REJECTED", rejected_reason="Policy violation",
            components=[{"type": "BODY", "text": "hi"}]))
        appmod.db.session.commit()

    r = client.get("/api/whatsapp/templates", headers=hdr)
    assert r.status_code == 200
    names_and_statuses = {(t["name"], t["status"]) for t in r.get_json()["templates"]}
    assert ("pending_one", "PENDING") in names_and_statuses
    assert ("rejected_one", "REJECTED") in names_and_statuses


def test_sync_upserts_local_rows_from_meta(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz T2", "t2_admin")
    _pro_api_mode(app, client, hdr, "biz-t2")

    remote_templates = [
        {"id": "meta_1", "name": "greeting", "language": "en", "category": "UTILITY",
         "status": "APPROVED", "components": [{"type": "BODY", "text": "Hi {{1}}"}]},
    ]
    monkeypatch.setattr(appmod.requests, "get",
                         lambda url, headers, timeout: FakeResponse(json_data={"data": remote_templates}))

    r = client.post("/api/whatsapp/templates/sync", headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["templates"][0]["name"] == "greeting"
    assert r.get_json()["templates"][0]["status"] == "APPROVED"
    assert r.get_json()["templates"][0]["meta_template_id"] == "meta_1"

    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-t2").first().id
        row = appmod.WhatsAppTemplate.query.filter_by(tenant_id=tid, name="greeting").first()
        assert row is not None and row.status == "APPROVED"


def test_sync_requires_pro_plan(app, client):
    hdr = make_tenant(client, "Biz T3", "t3_admin")  # free by default
    client.post("/api/whatsapp-settings", headers=hdr,
                json={"enabled": True, "mode": "deeplink"})
    r = client.post("/api/whatsapp/templates/sync", headers=hdr)
    assert r.status_code in (400, 402)  # not in api mode -> 400; free plan would be 402 if mode were api
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_whatsapp_templates.py -v`
Expected: FAIL — `sync` route doesn't exist (404), and `GET /api/whatsapp/templates` still returns the old Meta-live/fallback-stub shape, not the local cache.

- [ ] **Step 3: Replace `get_meta_templates` with the local-cache `GET` route and add `_parse_meta_error` + `sync`**

Replace `app.py:4676-4724` (the entire existing `get_meta_templates` function) with:

```python
def _parse_meta_error(resp):
    """Meta's template-management errors carry the actionable message under
    error.error_user_msg (shown to end users) or error.message (developer-
    facing) -- surface it verbatim rather than a generic failure, matching
    the pattern already used for Whish checkout rejections."""
    try:
        err = resp.json().get('error', {})
        return err.get('error_user_msg') or err.get('message') or resp.text
    except Exception:
        return resp.text


@app.route('/api/whatsapp/templates', methods=['GET'])
@jwt_required()
def get_whatsapp_templates():
    templates = tenant_query(WhatsAppTemplate).order_by(WhatsAppTemplate.created_at.desc()).all()
    return jsonify({'templates': [t.to_dict() for t in templates]}), 200


@app.route('/api/whatsapp/templates/sync', methods=['POST'])
@jwt_required()
@admin_or_finance_required()
def sync_whatsapp_templates():
    settings = tenant_query(WhatsAppSettings).first()
    if not settings or settings.mode != 'api':
        return jsonify({'error': 'WhatsApp API mode is not configured for this account.'}), 400
    if not plans.limits(current_tenant().plan)["whatsapp_api"]:
        return jsonify({'error': 'WhatsApp API mode requires an upgraded plan.'}), 402
    if not settings.access_token or not settings.business_account_id:
        return jsonify({'error': 'Please configure your WABA ID and Access Token first.'}), 400
    try:
        api_version = settings.api_version or 'v19.0'
        url = f'https://graph.facebook.com/{api_version}/{settings.business_account_id}/message_templates?limit=100'
        headers = {'Authorization': f'Bearer {settings.access_token}'}
        resp = requests.get(url, headers=headers, timeout=10)
        if not resp.ok:
            return jsonify({'error': _parse_meta_error(resp)}), 400
        remote = resp.json().get('data', [])
        synced = 0
        for t in remote:
            name = t.get('name')
            language = t.get('language', 'en')
            if not name:
                continue
            row = tenant_query(WhatsAppTemplate).filter_by(name=name, language=language).first()
            if not row:
                row = new_for_tenant(WhatsAppTemplate, name=name, language=language, components=t.get('components', []))
                db.session.add(row)
            row.category = t.get('category', 'MARKETING')
            row.status = t.get('status', 'PENDING')
            row.components = t.get('components', [])
            row.meta_template_id = t.get('id')
            row.updated_at = datetime.utcnow()
            synced += 1
        db.session.commit()
        return jsonify({'message': f'Synced {synced} template(s) from Meta.',
                         'templates': [t.to_dict() for t in tenant_query(WhatsAppTemplate).all()]}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
```

Note: `plans` is imported locally inside other functions in this file (`import plans` at `app.py:1302`) — if that import is function-local rather than module-level, add `import plans` at the top of `sync_whatsapp_templates` (and every later task's route that calls `plans.limits`) to match the existing pattern; if `plans` is already available at module scope by the time this code runs (check by searching for `import plans` placement), omit the redundant import. Verify by running the tests in Step 4 — an `ImportError`/`NameError` there means the import needs to be added.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_whatsapp_templates.py -v`
Expected: PASS

- [ ] **Step 5: Fix `MessagingView.js`'s now-broken assumption that `GET` only ever returns approved templates**

`GET /api/whatsapp/templates` used to filter to `APPROVED` server-side; it now returns every status. `frontend/src/components/MessagingView.js`'s `handleSyncTemplates` (around line 48-64) calls `apiService.fetchMetaTemplates()` (which hits this same `GET` route) to populate a bulk-send template picker — it must not offer a `PENDING`/`REJECTED` template for sending. Modify `frontend/src/components/MessagingView.js`:

Change:
```js
    const handleSyncTemplates = useCallback(async () => {
        setSyncing(true);
        try {
            const res = await apiService.fetchMetaTemplates();
            const loaded = res.data.templates || [];
```
to:
```js
    const handleSyncTemplates = useCallback(async () => {
        setSyncing(true);
        try {
            const res = await apiService.syncWhatsAppTemplates();
            const loaded = (res.data.templates || []).filter(t => t.status === 'APPROVED');
```

This also switches the picker's refresh action to call the new `sync` route (a live Meta pull) instead of the old passive `GET`, preserving its original "always pulls fresh from Meta on load" behavior — `apiService.syncWhatsAppTemplates` is added to `AppContext.js` in Task 6.

(This step's frontend change is exercised manually/in Task 6-9's browser verification, not by a Python test — `MessagingView.js` has no existing test coverage to extend.)

- [ ] **Step 6: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_whatsapp_templates.py frontend/src/components/MessagingView.js
git commit -m "feat: read WhatsApp templates from local cache, add Meta sync route"
```

---

## Task 3: `POST /api/whatsapp/templates` (create)

**Files:**
- Modify: `app.py` (insert after the `sync_whatsapp_templates` route added in Task 2)
- Modify: `tests/test_whatsapp_templates.py`

**Interfaces:**
- Consumes: `_parse_meta_error` (Task 2).
- Produces: `_ALLOWED_TEMPLATE_CATEGORIES`, `_validate_template_components(components)` — both reused by Task 4's edit route.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_whatsapp_templates.py`:

```python
def test_create_template_rejects_authentication_category(app, client):
    hdr = make_tenant(client, "Biz T4", "t4_admin")
    _pro_api_mode(app, client, hdr, "biz-t4")
    r = client.post("/api/whatsapp/templates", headers=hdr, json={
        "name": "otp_code", "language": "en", "category": "AUTHENTICATION",
        "components": [{"type": "BODY", "text": "Your code is {{1}}"}],
    })
    assert r.status_code == 400
    assert "MARKETING" in r.get_json()["error"] or "UTILITY" in r.get_json()["error"]


def test_create_template_rejects_missing_body(app, client):
    hdr = make_tenant(client, "Biz T5", "t5_admin")
    _pro_api_mode(app, client, hdr, "biz-t5")
    r = client.post("/api/whatsapp/templates", headers=hdr, json={
        "name": "no_body", "language": "en", "category": "UTILITY",
        "components": [{"type": "FOOTER", "text": "footer only"}],
    })
    assert r.status_code == 400
    assert "BODY" in r.get_json()["error"]


def test_create_template_rejects_variable_without_sample(app, client):
    hdr = make_tenant(client, "Biz T6", "t6_admin")
    _pro_api_mode(app, client, hdr, "biz-t6")
    r = client.post("/api/whatsapp/templates", headers=hdr, json={
        "name": "no_sample", "language": "en", "category": "UTILITY",
        "components": [{"type": "BODY", "text": "Hi {{1}}"}],
    })
    assert r.status_code == 400
    assert "sample" in r.get_json()["error"].lower()


def test_create_template_success(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz T7", "t7_admin")
    _pro_api_mode(app, client, hdr, "biz-t7")
    monkeypatch.setattr(appmod.requests, "post",
                         lambda url, headers, json, timeout: FakeResponse(
                             json_data={"id": "meta_new_1", "status": "PENDING"}))

    r = client.post("/api/whatsapp/templates", headers=hdr, json={
        "name": "greeting", "language": "en", "category": "UTILITY",
        "components": [{"type": "BODY", "text": "Hi {{1}}",
                         "example": {"body_text": [["Alex"]]}}],
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body["template"]["status"] == "PENDING"
    assert body["template"]["meta_template_id"] == "meta_new_1"

    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="biz-t7").first().id
        assert appmod.WhatsAppTemplate.query.filter_by(tenant_id=tid, name="greeting").count() == 1


def test_create_template_surfaces_meta_error(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz T8", "t8_admin")
    _pro_api_mode(app, client, hdr, "biz-t8")
    monkeypatch.setattr(appmod.requests, "post",
                         lambda url, headers, json, timeout: FakeResponse(
                             ok=False, status_code=400,
                             json_data={"error": {"error_user_msg": "Template name already exists"}}))

    r = client.post("/api/whatsapp/templates", headers=hdr, json={
        "name": "greeting", "language": "en", "category": "UTILITY",
        "components": [{"type": "BODY", "text": "Hi"}],
    })
    assert r.status_code == 400
    assert r.get_json()["error"] == "Template name already exists"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_whatsapp_templates.py -v`
Expected: FAIL — the create route doesn't exist yet (404 on all new tests).

- [ ] **Step 3: Add the create route**

Insert into `app.py`, immediately after the `sync_whatsapp_templates` function added in Task 2:

```python
_ALLOWED_TEMPLATE_CATEGORIES = {'MARKETING', 'UTILITY'}


def _validate_template_components(components):
    """Server-side shape validation before ever calling Meta -- catches the
    cheap mistakes locally so a tenant isn't waiting on a round-trip to Meta
    just to learn BODY was missing. Meta's own API remains the final word on
    anything more subtle (policy, wording, button-count limits)."""
    has_body = False
    for c in components or []:
        if c.get('type', '').upper() == 'BODY':
            has_body = True
            text = c.get('text', '')
            var_count = len(re.findall(r'\{\{(\d+)\}\}', text))
            example = (c.get('example') or {}).get('body_text')
            if var_count > 0 and not example:
                return f"BODY has {var_count} variable(s) but no sample values were provided."
    if not has_body:
        return "A template must have a BODY component."
    return None


@app.route('/api/whatsapp/templates', methods=['POST'])
@jwt_required()
@admin_or_finance_required()
def create_whatsapp_template():
    settings = tenant_query(WhatsAppSettings).first()
    if not settings or settings.mode != 'api':
        return jsonify({'error': 'WhatsApp API mode is not configured for this account.'}), 400
    if not plans.limits(current_tenant().plan)["whatsapp_api"]:
        return jsonify({'error': 'WhatsApp API mode requires an upgraded plan.'}), 402
    if not settings.access_token or not settings.business_account_id:
        return jsonify({'error': 'Please configure your WABA ID and Access Token first.'}), 400

    data = request.json or {}
    name = (data.get('name') or '').strip()
    language = (data.get('language') or '').strip()
    category = (data.get('category') or '').strip().upper()
    components = data.get('components') or []

    if not name or not language:
        return jsonify({'error': 'Name and language are required.'}), 400
    if category not in _ALLOWED_TEMPLATE_CATEGORIES:
        return jsonify({'error': f"Category must be one of {sorted(_ALLOWED_TEMPLATE_CATEGORIES)}."}), 400
    error = _validate_template_components(components)
    if error:
        return jsonify({'error': error}), 400

    try:
        api_version = settings.api_version or 'v19.0'
        url = f'https://graph.facebook.com/{api_version}/{settings.business_account_id}/message_templates'
        headers = {'Authorization': f'Bearer {settings.access_token}', 'Content-Type': 'application/json'}
        payload = {'name': name, 'language': language, 'category': category, 'components': components}
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if not resp.ok:
            return jsonify({'error': _parse_meta_error(resp)}), 400
        body = resp.json()
        row = new_for_tenant(WhatsAppTemplate, name=name, language=language, category=category,
                              status=body.get('status', 'PENDING'), components=components,
                              meta_template_id=body.get('id'))
        db.session.add(row)
        db.session.commit()
        return jsonify({'message': 'Template submitted to Meta for review.', 'template': row.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_whatsapp_templates.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_whatsapp_templates.py
git commit -m "feat: add POST /api/whatsapp/templates to create/submit a template"
```

---

## Task 4: `PUT /api/whatsapp/templates/<id>` (edit) and `DELETE /api/whatsapp/templates/<id>`

**Files:**
- Modify: `app.py` (insert after the create route added in Task 3)
- Modify: `tests/test_whatsapp_templates.py`

**Interfaces:**
- Consumes: `_parse_meta_error`, `_validate_template_components` (Task 2/3).
- Produces: nothing new consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_whatsapp_templates.py`:

```python
def _seed_template(app, slug, status="PENDING", meta_id="meta_x"):
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug=slug).first().id
        row = appmod.WhatsAppTemplate(tenant_id=tid, name="editable", language="en", category="UTILITY",
                                       status=status, meta_template_id=meta_id,
                                       components=[{"type": "BODY", "text": "Hi"}])
        appmod.db.session.add(row)
        appmod.db.session.commit()
        return row.id


def test_update_template_blocked_when_approved(app, client):
    hdr = make_tenant(client, "Biz T9", "t9_admin")
    _pro_api_mode(app, client, hdr, "biz-t9")
    tpl_id = _seed_template(app, "biz-t9", status="APPROVED")
    r = client.put(f"/api/whatsapp/templates/{tpl_id}", headers=hdr,
                   json={"components": [{"type": "BODY", "text": "New text"}]})
    assert r.status_code == 400
    assert "approved" in r.get_json()["error"].lower()


def test_update_template_success_resets_to_pending(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz T10", "t10_admin")
    _pro_api_mode(app, client, hdr, "biz-t10")
    tpl_id = _seed_template(app, "biz-t10", status="REJECTED")
    monkeypatch.setattr(appmod.requests, "post",
                         lambda url, headers, json, timeout: FakeResponse(json_data={"success": True}))

    r = client.put(f"/api/whatsapp/templates/{tpl_id}", headers=hdr,
                   json={"components": [{"type": "BODY", "text": "New text"}]})
    assert r.status_code == 200
    assert r.get_json()["template"]["status"] == "PENDING"
    assert r.get_json()["template"]["rejected_reason"] is None


def test_delete_template_calls_meta_and_removes_local_row(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz T11", "t11_admin")
    _pro_api_mode(app, client, hdr, "biz-t11")
    tpl_id = _seed_template(app, "biz-t11", status="PENDING")
    calls = []
    monkeypatch.setattr(appmod.requests, "delete",
                         lambda url, headers, timeout: (calls.append(url), FakeResponse())[1])

    r = client.delete(f"/api/whatsapp/templates/{tpl_id}", headers=hdr)
    assert r.status_code == 200
    assert len(calls) == 1 and "editable" in calls[0]
    with app.app_context():
        assert appmod.db.session.get(appmod.WhatsAppTemplate, tpl_id) is None


def test_delete_template_tenant_isolation(app, client):
    hdr_a = make_tenant(client, "Biz T12A", "t12a_admin")
    hdr_b = make_tenant(client, "Biz T12B", "t12b_admin")
    _pro_api_mode(app, client, hdr_a, "biz-t12a")
    _pro_api_mode(app, client, hdr_b, "biz-t12b")
    tpl_id = _seed_template(app, "biz-t12a", status="PENDING")

    r = client.delete(f"/api/whatsapp/templates/{tpl_id}", headers=hdr_b)
    assert r.status_code == 404
    with app.app_context():
        assert appmod.db.session.get(appmod.WhatsAppTemplate, tpl_id) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_whatsapp_templates.py -v`
Expected: FAIL — `PUT`/`DELETE` routes don't exist yet (404).

- [ ] **Step 3: Add the edit and delete routes**

Insert into `app.py`, immediately after `create_whatsapp_template` (Task 3):

```python
@app.route('/api/whatsapp/templates/<int:template_id>', methods=['PUT'])
@jwt_required()
@admin_or_finance_required()
def update_whatsapp_template(template_id):
    settings = tenant_query(WhatsAppSettings).first()
    if not settings or settings.mode != 'api':
        return jsonify({'error': 'WhatsApp API mode is not configured for this account.'}), 400
    if not plans.limits(current_tenant().plan)["whatsapp_api"]:
        return jsonify({'error': 'WhatsApp API mode requires an upgraded plan.'}), 402
    row = tenant_query(WhatsAppTemplate).filter_by(id=template_id).first()
    if not row:
        return jsonify({'error': 'Template not found.'}), 404
    if row.status == 'APPROVED':
        return jsonify({'error': 'An approved template cannot be edited -- create a new template instead.'}), 400
    if not row.meta_template_id:
        return jsonify({'error': 'This template has no known Meta template ID to edit.'}), 400

    data = request.json or {}
    components = data.get('components') or row.components
    error = _validate_template_components(components)
    if error:
        return jsonify({'error': error}), 400

    try:
        api_version = settings.api_version or 'v19.0'
        url = f'https://graph.facebook.com/{api_version}/{row.meta_template_id}'
        headers = {'Authorization': f'Bearer {settings.access_token}', 'Content-Type': 'application/json'}
        resp = requests.post(url, headers=headers, json={'components': components}, timeout=15)
        if not resp.ok:
            return jsonify({'error': _parse_meta_error(resp)}), 400
        row.components = components
        row.status = 'PENDING'
        row.rejected_reason = None
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'message': 'Template updated and resubmitted for review.', 'template': row.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/whatsapp/templates/<int:template_id>', methods=['DELETE'])
@jwt_required()
@admin_or_finance_required()
def delete_whatsapp_template(template_id):
    settings = tenant_query(WhatsAppSettings).first()
    if not settings or settings.mode != 'api':
        return jsonify({'error': 'WhatsApp API mode is not configured for this account.'}), 400
    row = tenant_query(WhatsAppTemplate).filter_by(id=template_id).first()
    if not row:
        return jsonify({'error': 'Template not found.'}), 404
    try:
        if settings.access_token and settings.business_account_id:
            api_version = settings.api_version or 'v19.0'
            url = f'https://graph.facebook.com/{api_version}/{settings.business_account_id}/message_templates?name={row.name}'
            headers = {'Authorization': f'Bearer {settings.access_token}'}
            resp = requests.delete(url, headers=headers, timeout=10)
            if not resp.ok:
                return jsonify({'error': _parse_meta_error(resp)}), 400
        db.session.delete(row)
        db.session.commit()
        return jsonify({'message': 'Template deleted.'}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_whatsapp_templates.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_whatsapp_templates.py
git commit -m "feat: add PUT/DELETE routes for editing and deleting a WhatsApp template"
```

---

## Task 5: `message_template_status_update` webhook branch

**Files:**
- Modify: `app.py:5766-5768` (inside `whatsapp_webhook`'s POST handling, per-`change` loop)
- Modify: `tests/test_iso_webhook.py`

**Interfaces:**
- Consumes: `WhatsAppTemplate` (Task 1), the existing `whatsapp_webhook` view's `raw_body`/`signature_header` (already in scope at this point in the function).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_iso_webhook.py`:

```python
def test_template_status_update_updates_local_row(app, client):
    a = make_tenant(client, "Biz TE", "te_admin")
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-te").first().id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=a_tid, business_account_id="WABA_TE", enabled=True, mode="api",
            app_secret="shh-te-secret"))
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=a_tid, name="greeting", language="en", category="UTILITY",
            status="PENDING", components=[{"type": "BODY", "text": "Hi"}]))
        appmod.db.session.commit()

    payload = {"entry": [{"id": "WABA_TE", "changes": [{
        "field": "message_template_status_update",
        "value": {"message_template_name": "greeting", "message_template_language": "en",
                  "event": "APPROVED"},
    }]}]}
    r = _signed_post(client, payload, "shh-te-secret")
    assert r.status_code == 200

    with app.app_context():
        row = appmod.WhatsAppTemplate.query.filter_by(tenant_id=a_tid, name="greeting").first()
        assert row.status == "APPROVED"


def test_template_status_update_rejects_bad_signature(app, client):
    a = make_tenant(client, "Biz TF", "tf_admin")
    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-tf").first().id
        appmod.db.session.add(appmod.WhatsAppSettings(
            tenant_id=a_tid, business_account_id="WABA_TF", enabled=True, mode="api",
            app_secret="shh-tf-secret"))
        appmod.db.session.add(appmod.WhatsAppTemplate(
            tenant_id=a_tid, name="greeting", language="en", category="UTILITY",
            status="PENDING", components=[{"type": "BODY", "text": "Hi"}]))
        appmod.db.session.commit()

    payload = {"entry": [{"id": "WABA_TF", "changes": [{
        "field": "message_template_status_update",
        "value": {"message_template_name": "greeting", "message_template_language": "en",
                  "event": "APPROVED"},
    }]}]}
    r = _signed_post(client, payload, "wrong-secret")
    assert r.status_code == 401
    with app.app_context():
        row = appmod.WhatsAppTemplate.query.filter_by(tenant_id=a_tid, name="greeting").first()
        assert row.status == "PENDING"  # untouched


def test_template_status_update_unmatched_waba_is_a_noop(app, client):
    payload = {"entry": [{"id": "NO_SUCH_WABA", "changes": [{
        "field": "message_template_status_update",
        "value": {"message_template_name": "greeting", "message_template_language": "en",
                  "event": "APPROVED"},
    }]}]}
    # No signature needed to reach the "no tenant found" branch -- it's checked
    # before signature verification, mirroring the existing phone_number_id path.
    r = client.post("/api/whatsapp/webhook", json=payload)
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_iso_webhook.py -v`
Expected: FAIL — the webhook currently has no handling for the `message_template_status_update` field, so `test_template_status_update_updates_local_row` finds the row still `PENDING`, and the bad-signature test currently returns 200 (falls through the existing messaging-only logic, since `incoming_pnid` is `None` for this payload shape and the existing code just logs-and-skips rather than 401ing).

- [ ] **Step 3: Add the webhook branch**

In `app.py`, inside `whatsapp_webhook`'s POST handling, the loop currently reads (`app.py:5765-5768`):

```python
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    val = change.get('value', {})
                    messages = val.get('messages', [])
```

Change to:

```python
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    val = change.get('value', {})

                    if change.get('field') == 'message_template_status_update':
                        waba_id = entry.get('id')
                        tpl_settings = WhatsAppSettings.query.filter_by(business_account_id=waba_id).first() if waba_id else None
                        if not tpl_settings:
                            logging.warning(f"WhatsApp template status webhook: no tenant for business_account_id={waba_id}; skipping.")
                            continue
                        if not tpl_settings.app_secret or not signature_header.startswith('sha256='):
                            logging.warning(f"WhatsApp template status webhook: missing app_secret/signature for tenant_id={tpl_settings.tenant_id}; rejecting.")
                            return jsonify({'error': 'Invalid signature'}), 401
                        tpl_expected_sig = hmac.new(tpl_settings.app_secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
                        tpl_provided_sig = signature_header[len('sha256='):]
                        if not hmac.compare_digest(tpl_expected_sig, tpl_provided_sig):
                            logging.warning(f"WhatsApp template status webhook: signature mismatch for tenant_id={tpl_settings.tenant_id}; rejecting.")
                            return jsonify({'error': 'Invalid signature'}), 401

                        tpl_name = val.get('message_template_name')
                        tpl_language = val.get('message_template_language')
                        tpl_new_status = val.get('event')
                        tpl_reason = val.get('reason')
                        if tpl_name and tpl_new_status:
                            tpl_row = WhatsAppTemplate.query.filter_by(
                                tenant_id=tpl_settings.tenant_id, name=tpl_name, language=tpl_language).first()
                            if tpl_row:
                                tpl_row.status = tpl_new_status
                                tpl_row.rejected_reason = tpl_reason
                                tpl_row.updated_at = datetime.utcnow()
                                db.session.commit()
                                logging.info(f"WhatsApp template status webhook: tenant_id={tpl_settings.tenant_id} name={tpl_name} -> {tpl_new_status}")
                            else:
                                logging.warning(f"WhatsApp template status webhook: no local WhatsAppTemplate for tenant_id={tpl_settings.tenant_id} name={tpl_name} language={tpl_language}; skipping.")
                        continue

                    messages = val.get('messages', [])
```

(Local variable names inside this branch are prefixed `tpl_`/`tpl_settings` deliberately, to avoid shadowing the `settings`/`expected_sig`/`provided_sig` names the existing messaging branch below assigns later in the same loop body.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_iso_webhook.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_iso_webhook.py
git commit -m "feat: handle Meta's message_template_status_update webhook field"
```

---

## Task 6: Frontend apiService methods, `WhatsAppTemplatesManager.js` list view, Settings tab wiring

**Files:**
- Modify: `frontend/src/context/AppContext.js` (add apiService methods)
- Create: `frontend/src/components/WhatsAppTemplatesManager.js`
- Modify: `frontend/src/components/SettingsView.js` (import, new Tab, render)

**Interfaces:**
- Consumes: `GET/POST/PUT/DELETE /api/whatsapp/templates*` (Tasks 2-4).
- Produces: `WhatsAppTemplatesManager` component (default export, no required props — reads `apiService`/`setSnackbar` from `useAppContext()` like `ExpenseCategoryManager`) — Task 7 adds the create/edit dialog into this same file, Task 9 reads templates via the same `apiService.fetchWhatsAppTemplates` method this task adds.

- [ ] **Step 1: Add apiService methods**

In `frontend/src/context/AppContext.js`, add near the existing `fetchMetaTemplates: () => api.get('/whatsapp/templates'),` line (`AppContext.js:265`):

```js
    fetchWhatsAppTemplates: () => api.get('/whatsapp/templates'),
    syncWhatsAppTemplates: () => api.post('/whatsapp/templates/sync'),
    createWhatsAppTemplate: (data) => api.post('/whatsapp/templates', data),
    updateWhatsAppTemplate: (id, data) => api.put(`/whatsapp/templates/${id}`, data),
    deleteWhatsAppTemplate: (id) => api.delete(`/whatsapp/templates/${id}`),
```

(Leave the existing `fetchMetaTemplates` line in place — nothing else references it after Task 2 Step 5's `MessagingView.js` change, but removing it is an unrelated cleanup outside this plan's scope.)

- [ ] **Step 2: Create `WhatsAppTemplatesManager.js` (list view only — the create/edit dialog is added in Task 7)**

Create `frontend/src/components/WhatsAppTemplatesManager.js`:

```jsx
import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Button, Paper, Table, TableHead, TableRow, TableCell, TableBody,
    Chip, IconButton, Tooltip, CircularProgress,
} from '@mui/material';
import { Add as AddIcon, Refresh as RefreshIcon, Edit as EditIcon, Delete as DeleteIcon } from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';

const STATUS_COLOR = {
    APPROVED: 'success',
    PENDING: 'default',
    REJECTED: 'error',
    PAUSED: 'warning',
    DISABLED: 'warning',
};

const WhatsAppTemplatesManager = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [templates, setTemplates] = useState([]);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);

    const fetchTemplates = useCallback(async () => {
        setLoading(true);
        try {
            const res = await apiService.fetchWhatsAppTemplates();
            setTemplates(res.data.templates || []);
        } catch (error) {
            setSnackbar({ open: true, message: 'Failed to load templates.', severity: 'error' });
        } finally {
            setLoading(false);
        }
    }, [apiService, setSnackbar]);

    useEffect(() => { fetchTemplates(); }, [fetchTemplates]);

    const handleSync = async () => {
        setSyncing(true);
        try {
            const res = await apiService.syncWhatsAppTemplates();
            setTemplates(res.data.templates || []);
            setSnackbar({ open: true, message: res.data.message || 'Synced.', severity: 'success' });
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to sync from Meta.', severity: 'error' });
        } finally {
            setSyncing(false);
        }
    };

    const handleDelete = async (template) => {
        if (!window.confirm(`Delete template "${template.name}"? This also deletes it from Meta.`)) return;
        try {
            await apiService.deleteWhatsAppTemplate(template.id);
            setSnackbar({ open: true, message: 'Template deleted.', severity: 'success' });
            fetchTemplates();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to delete template.', severity: 'error' });
        }
    };

    return (
        <Paper sx={{ p: 3, mt: 4 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
                <Typography variant="h6">WhatsApp Templates</Typography>
                <Box sx={{ display: 'flex', gap: 1 }}>
                    <Button variant="outlined" startIcon={syncing ? <CircularProgress size={16} /> : <RefreshIcon />}
                        onClick={handleSync} disabled={syncing}>
                        Refresh from Meta
                    </Button>
                    <Button variant="contained" startIcon={<AddIcon />} disabled>
                        New Template
                    </Button>
                </Box>
            </Box>
            {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}><CircularProgress /></Box>
            ) : (
                <Table size="small">
                    <TableHead>
                        <TableRow>
                            <TableCell>Name</TableCell>
                            <TableCell>Category</TableCell>
                            <TableCell>Language</TableCell>
                            <TableCell>Status</TableCell>
                            <TableCell align="right">Actions</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {templates.map((t) => (
                            <TableRow key={t.id}>
                                <TableCell>{t.name}</TableCell>
                                <TableCell>{t.category}</TableCell>
                                <TableCell>{t.language}</TableCell>
                                <TableCell>
                                    <Tooltip title={t.status === 'REJECTED' ? (t.rejected_reason || '') : ''}>
                                        <Chip size="small" label={t.status} color={STATUS_COLOR[t.status] || 'default'} />
                                    </Tooltip>
                                </TableCell>
                                <TableCell align="right">
                                    <IconButton size="small" disabled={t.status === 'APPROVED'}>
                                        <EditIcon fontSize="small" />
                                    </IconButton>
                                    <IconButton size="small" onClick={() => handleDelete(t)}>
                                        <DeleteIcon fontSize="small" color="error" />
                                    </IconButton>
                                </TableCell>
                            </TableRow>
                        ))}
                        {templates.length === 0 && (
                            <TableRow><TableCell colSpan={5} align="center">No templates yet.</TableCell></TableRow>
                        )}
                    </TableBody>
                </Table>
            )}
        </Paper>
    );
};

export default WhatsAppTemplatesManager;
```

(The "New Template" button is `disabled` and the Edit `IconButton` has no `onClick` yet — Task 7 wires both up to the builder dialog. This keeps this task's deliverable — the list, refresh, and delete — independently testable in the browser before the more complex builder is added.)

- [ ] **Step 3: Wire the new Settings tab**

In `frontend/src/components/SettingsView.js`:

Add the import near the other manager imports (`SettingsView.js:24-26`):
```js
import WhatsAppTemplatesManager from './WhatsAppTemplatesManager.js';
```

Add a new `Tab` to the `Tabs` bar (`SettingsView.js:206-212`), after "WhatsApp Notifications":
```jsx
                    <Tab icon={<WhatsAppIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="WhatsApp Templates" />
```
(This becomes `tab === 2`, shifting "Expense Categories" to `tab === 3`, "User Management" to `tab === 4`, "Sectors" to `tab === 5` — update those three tabs' `{tab === N && (...)}` guards accordingly.)

Add the tab panel, after the closing `)}` of the WhatsApp Notifications tab block (currently `tab === 1`, ending around `SettingsView.js:535` per the design doc's earlier exploration):
```jsx
            {/* ── Tab 2: WhatsApp Templates ── */}
            {tab === 2 && <WhatsAppTemplatesManager />}
```

- [ ] **Step 4: Manual browser verification**

Run: `npm start` in `frontend/` (or use the project's existing dev-server launch config)
Steps: log in as a Pro-plan tenant with `WhatsAppSettings.mode == 'api'` configured, open Settings → WhatsApp Templates tab, click "Refresh from Meta" (expect either a real Meta response or a clear error if credentials are placeholders), confirm the list renders with status chips.
Expected: no console errors, tab renders, refresh button round-trips to the backend.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/context/AppContext.js frontend/src/components/WhatsAppTemplatesManager.js frontend/src/components/SettingsView.js
git commit -m "feat: add WhatsApp Templates Settings tab with list and refresh"
```

---

## Task 7: "New/Edit Template" builder dialog

**Files:**
- Modify: `frontend/src/components/WhatsAppTemplatesManager.js`

**Interfaces:**
- Consumes: `apiService.createWhatsAppTemplate`, `apiService.updateWhatsAppTemplate` (Task 6).
- Produces: nothing new consumed by later tasks (Task 8 extends this same dialog for media headers).

- [ ] **Step 1: Add the builder dialog component and wire it into the list**

Modify `frontend/src/components/WhatsAppTemplatesManager.js` — add these imports:
```js
import {
    Box, Typography, Button, Paper, Table, TableHead, TableRow, TableCell, TableBody,
    Chip, IconButton, Tooltip, CircularProgress, Dialog, DialogTitle, DialogContent,
    DialogActions, TextField, MenuItem, Divider, Alert,
} from '@mui/material';
```

Add, above the `WhatsAppTemplatesManager` component definition:
```jsx
const EMPTY_TEMPLATE_FORM = {
    name: '', language: 'en', category: 'UTILITY',
    headerType: 'NONE', headerText: '',
    bodyText: '', bodySamples: [],
    footerText: '',
    buttons: [], // [{ type: 'URL'|'PHONE_NUMBER'|'QUICK_REPLY', text, value }]
};

function buildComponents(form) {
    const components = [];
    if (form.headerType === 'TEXT' && form.headerText) {
        components.push({ type: 'HEADER', format: 'TEXT', text: form.headerText });
    } else if (['IMAGE', 'VIDEO', 'DOCUMENT'].includes(form.headerType)) {
        components.push({ type: 'HEADER', format: form.headerType,
            example: form.headerHandle ? { header_handle: [form.headerHandle] } : undefined });
    }
    const bodyComponent = { type: 'BODY', text: form.bodyText };
    if (form.bodySamples.length > 0) {
        bodyComponent.example = { body_text: [form.bodySamples] };
    }
    components.push(bodyComponent);
    if (form.footerText) {
        components.push({ type: 'FOOTER', text: form.footerText });
    }
    if (form.buttons.length > 0) {
        components.push({
            type: 'BUTTONS',
            buttons: form.buttons.map(b => {
                if (b.type === 'URL') return { type: 'URL', text: b.text, url: b.value };
                if (b.type === 'PHONE_NUMBER') return { type: 'PHONE_NUMBER', text: b.text, phone_number: b.value };
                return { type: 'QUICK_REPLY', text: b.text };
            }),
        });
    }
    return components;
}

function countBodyVariables(text) {
    const matches = text.match(/\{\{(\d+)\}\}/g) || [];
    return matches.length;
}
```

Inside `WhatsAppTemplatesManager`, add state and handlers:
```jsx
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingTemplate, setEditingTemplate] = useState(null);
    const [form, setForm] = useState(EMPTY_TEMPLATE_FORM);
    const [saving, setSaving] = useState(false);

    const openCreateDialog = () => {
        setEditingTemplate(null);
        setForm(EMPTY_TEMPLATE_FORM);
        setDialogOpen(true);
    };

    const openEditDialog = (template) => {
        setEditingTemplate(template);
        const body = (template.components || []).find(c => c.type === 'BODY') || {};
        const header = (template.components || []).find(c => c.type === 'HEADER') || {};
        const footer = (template.components || []).find(c => c.type === 'FOOTER') || {};
        const buttonsComp = (template.components || []).find(c => c.type === 'BUTTONS') || { buttons: [] };
        setForm({
            name: template.name, language: template.language, category: template.category,
            headerType: header.format || 'NONE', headerText: header.text || '',
            bodyText: body.text || '', bodySamples: (body.example?.body_text?.[0]) || [],
            footerText: footer.text || '',
            buttons: (buttonsComp.buttons || []).map(b => ({
                type: b.type, text: b.text, value: b.url || b.phone_number || '',
            })),
        });
        setDialogOpen(true);
    };

    const addButton = () => setForm(f => ({ ...f, buttons: [...f.buttons, { type: 'QUICK_REPLY', text: '', value: '' }] }));
    const removeButton = (idx) => setForm(f => ({ ...f, buttons: f.buttons.filter((_, i) => i !== idx) }));
    const updateButton = (idx, patch) => setForm(f => ({
        ...f, buttons: f.buttons.map((b, i) => (i === idx ? { ...b, ...patch } : b)),
    }));

    const bodyVarCount = countBodyVariables(form.bodyText);
    useEffect(() => {
        setForm(f => {
            const samples = [...f.bodySamples];
            samples.length = bodyVarCount;
            return { ...f, bodySamples: samples.map(s => s || '') };
        });
    }, [bodyVarCount]);

    const handleSaveTemplate = async () => {
        setSaving(true);
        try {
            const components = buildComponents(form);
            if (editingTemplate) {
                await apiService.updateWhatsAppTemplate(editingTemplate.id, { components });
                setSnackbar({ open: true, message: 'Template updated and resubmitted for review.', severity: 'success' });
            } else {
                await apiService.createWhatsAppTemplate({
                    name: form.name, language: form.language, category: form.category, components,
                });
                setSnackbar({ open: true, message: 'Template submitted to Meta for review.', severity: 'success' });
            }
            setDialogOpen(false);
            fetchTemplates();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to save template.', severity: 'error' });
        } finally {
            setSaving(false);
        }
    };
```

Wire the list's buttons (replace the two disabled/no-op controls from Task 6):
```jsx
                    <Button variant="contained" startIcon={<AddIcon />} onClick={openCreateDialog}>
                        New Template
                    </Button>
```
and
```jsx
                                    <IconButton size="small" disabled={t.status === 'APPROVED'} onClick={() => openEditDialog(t)}>
                                        <EditIcon fontSize="small" />
                                    </IconButton>
```

Add the dialog JSX, as a sibling of the closing `</Paper>`:
```jsx
            <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
                <DialogTitle>{editingTemplate ? `Edit "${editingTemplate.name}"` : 'New Template'}</DialogTitle>
                <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
                    {!editingTemplate && (
                        <>
                            <TextField label="Name" value={form.name}
                                onChange={e => setForm(f => ({ ...f, name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_') }))}
                                helperText="Lowercase letters, numbers, and underscores only" fullWidth />
                            <Box sx={{ display: 'flex', gap: 2 }}>
                                <TextField select label="Category" value={form.category}
                                    onChange={e => setForm(f => ({ ...f, category: e.target.value }))} fullWidth>
                                    <MenuItem value="UTILITY">Utility</MenuItem>
                                    <MenuItem value="MARKETING">Marketing</MenuItem>
                                </TextField>
                                <TextField label="Language" value={form.language}
                                    onChange={e => setForm(f => ({ ...f, language: e.target.value }))}
                                    helperText="e.g. en, ar, fr" fullWidth />
                            </Box>
                        </>
                    )}

                    <Divider />
                    <TextField select label="Header" value={form.headerType}
                        onChange={e => setForm(f => ({ ...f, headerType: e.target.value }))} fullWidth>
                        <MenuItem value="NONE">None</MenuItem>
                        <MenuItem value="TEXT">Text</MenuItem>
                        <MenuItem value="IMAGE">Image</MenuItem>
                        <MenuItem value="VIDEO">Video</MenuItem>
                        <MenuItem value="DOCUMENT">Document</MenuItem>
                    </TextField>
                    {form.headerType === 'TEXT' && (
                        <TextField label="Header Text" value={form.headerText}
                            onChange={e => setForm(f => ({ ...f, headerText: e.target.value }))} fullWidth />
                    )}

                    <Divider />
                    <TextField label="Body" value={form.bodyText} multiline minRows={3}
                        onChange={e => setForm(f => ({ ...f, bodyText: e.target.value }))}
                        helperText='Use {{1}}, {{2}}... for variables' fullWidth />
                    <Button size="small" sx={{ alignSelf: 'flex-start' }}
                        onClick={() => setForm(f => ({ ...f, bodyText: f.bodyText + `{{${countBodyVariables(f.bodyText) + 1}}}` }))}>
                        Insert Variable
                    </Button>
                    {form.bodySamples.map((sample, idx) => (
                        <TextField key={idx} size="small" label={`Sample value for {{${idx + 1}}}`} value={sample}
                            onChange={e => setForm(f => ({
                                ...f, bodySamples: f.bodySamples.map((s, i) => (i === idx ? e.target.value : s)),
                            }))} fullWidth />
                    ))}

                    <Divider />
                    <TextField label="Footer (optional)" value={form.footerText}
                        onChange={e => setForm(f => ({ ...f, footerText: e.target.value }))} fullWidth />

                    <Divider />
                    <Typography variant="subtitle2">Buttons</Typography>
                    {form.buttons.map((b, idx) => (
                        <Box key={idx} sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                            <TextField select size="small" value={b.type}
                                onChange={e => updateButton(idx, { type: e.target.value })} sx={{ width: 160 }}>
                                <MenuItem value="QUICK_REPLY">Quick Reply</MenuItem>
                                <MenuItem value="URL">URL</MenuItem>
                                <MenuItem value="PHONE_NUMBER">Phone Number</MenuItem>
                            </TextField>
                            <TextField size="small" label="Label" value={b.text}
                                onChange={e => updateButton(idx, { text: e.target.value })} />
                            {b.type !== 'QUICK_REPLY' && (
                                <TextField size="small" label={b.type === 'URL' ? 'URL' : 'Phone number'} value={b.value}
                                    onChange={e => updateButton(idx, { value: e.target.value })} sx={{ flexGrow: 1 }} />
                            )}
                            <IconButton size="small" onClick={() => removeButton(idx)}><DeleteIcon fontSize="small" /></IconButton>
                        </Box>
                    ))}
                    <Button size="small" sx={{ alignSelf: 'flex-start' }} onClick={addButton}>Add Button</Button>

                    <Alert severity="info">
                        Meta enforces exact limits on button count/combinations and reviews wording for policy
                        compliance — any rejection will show Meta's own message here after you submit.
                    </Alert>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleSaveTemplate} disabled={saving || !form.bodyText}>
                        {saving ? <CircularProgress size={20} /> : (editingTemplate ? 'Save & Resubmit' : 'Submit for Review')}
                    </Button>
                </DialogActions>
            </Dialog>
```

- [ ] **Step 2: Manual browser verification**

Run: `npm start` in `frontend/`
Steps: open Settings → WhatsApp Templates, click "New Template", fill Name/Category/Language, type a body with `{{1}}`, fill the sample value, submit. Confirm a Meta API error (expected with placeholder credentials) surfaces in the snackbar with Meta's own message text, not a generic failure. Confirm the button-list add/remove and header-type switch all update the preview state without console errors.
Expected: dialog behaves correctly; the actual Meta round-trip fails gracefully with a real, informative error until real credentials exist.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/WhatsAppTemplatesManager.js
git commit -m "feat: add template builder dialog (header/body/footer/buttons)"
```

---

## Task 8: Media header sample upload (Meta resumable upload)

**Files:**
- Modify: `app.py` (insert after `delete_whatsapp_template`, Task 4)
- Modify: `tests/test_whatsapp_templates.py`
- Modify: `frontend/src/context/AppContext.js`
- Modify: `frontend/src/components/WhatsAppTemplatesManager.js`

**Interfaces:**
- Consumes: `_parse_meta_error` (Task 2).
- Produces: `POST /api/whatsapp/templates/upload-sample` returning `{'header_handle': str}`, consumed by the builder dialog's media-header file input.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_whatsapp_templates.py`:

```python
import io


def test_upload_sample_two_step_flow(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz T13", "t13_admin")
    _pro_api_mode(app, client, hdr, "biz-t13")

    calls = []

    def fake_post(url, timeout, headers=None, params=None, data=None):
        calls.append((url, params, headers))
        if len(calls) == 1:
            return FakeResponse(json_data={"id": "upload:session123"})
        return FakeResponse(json_data={"h": "HANDLE_ABC"})

    monkeypatch.setattr(appmod.requests, "post", fake_post)

    r = client.post("/api/whatsapp/templates/upload-sample", headers=hdr,
                    data={"file": (io.BytesIO(b"fake-image-bytes"), "sample.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.get_json()["header_handle"] == "HANDLE_ABC"
    assert len(calls) == 2
    assert calls[0][1]["file_type"]  # file_type param sent on session creation
    assert calls[1][2]["Authorization"].startswith("OAuth ")


def test_upload_sample_requires_file(app, client):
    hdr = make_tenant(client, "Biz T14", "t14_admin")
    _pro_api_mode(app, client, hdr, "biz-t14")
    r = client.post("/api/whatsapp/templates/upload-sample", headers=hdr, data={}, content_type="multipart/form-data")
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_whatsapp_templates.py -v`
Expected: FAIL — route doesn't exist (404).

- [ ] **Step 3: Add the upload route**

Insert into `app.py`, after `delete_whatsapp_template` (Task 4):

```python
@app.route('/api/whatsapp/templates/upload-sample', methods=['POST'])
@jwt_required()
@admin_or_finance_required()
def upload_whatsapp_template_sample():
    """Uploads a sample media file for a media (image/video/document) HEADER
    component via Meta's resumable-upload API, returning the 'handle' Meta
    requires in the template's example.header_handle at submission time.
    Two-step flow: create an upload session, then upload the file bytes."""
    settings = tenant_query(WhatsAppSettings).first()
    if not settings or settings.mode != 'api':
        return jsonify({'error': 'WhatsApp API mode is not configured for this account.'}), 400
    if not plans.limits(current_tenant().plan)["whatsapp_api"]:
        return jsonify({'error': 'WhatsApp API mode requires an upgraded plan.'}), 402
    if not settings.access_token or not settings.app_id:
        return jsonify({'error': 'Please configure your App ID and Access Token first.'}), 400

    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file provided.'}), 400
    file_bytes = file.read()

    try:
        api_version = settings.api_version or 'v19.0'
        session_url = f'https://graph.facebook.com/{api_version}/{settings.app_id}/uploads'
        session_resp = requests.post(session_url, params={
            'file_length': len(file_bytes),
            'file_type': file.mimetype or 'application/octet-stream',
            'access_token': settings.access_token,
        }, timeout=15)
        if not session_resp.ok:
            return jsonify({'error': _parse_meta_error(session_resp)}), 400
        upload_session_id = session_resp.json().get('id')
        if not upload_session_id:
            return jsonify({'error': 'Meta did not return an upload session ID.'}), 400

        upload_url = f'https://graph.facebook.com/{api_version}/{upload_session_id}'
        upload_headers = {'Authorization': f'OAuth {settings.access_token}'}
        upload_resp = requests.post(upload_url, headers=upload_headers, data=file_bytes, timeout=30)
        if not upload_resp.ok:
            return jsonify({'error': _parse_meta_error(upload_resp)}), 400
        handle = upload_resp.json().get('h')
        if not handle:
            return jsonify({'error': 'Meta did not return an upload handle.'}), 400
        return jsonify({'header_handle': handle}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_whatsapp_templates.py -v`
Expected: PASS

- [ ] **Step 5: Wire the frontend file input**

Add to `frontend/src/context/AppContext.js`, near the other new WhatsApp template methods:
```js
    uploadWhatsAppTemplateSample: (formData) => api.post('/whatsapp/templates/upload-sample', formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }),
```

In `frontend/src/components/WhatsAppTemplatesManager.js`, add a file input shown only for media header types, inside the dialog right after the `headerType` select:
```jsx
                    {['IMAGE', 'VIDEO', 'DOCUMENT'].includes(form.headerType) && (
                        <Button variant="outlined" component="label" size="small" sx={{ alignSelf: 'flex-start' }}>
                            {form.headerHandle ? 'Sample uploaded ✓' : 'Upload Sample File'}
                            <input type="file" hidden onChange={async (e) => {
                                const f = e.target.files[0];
                                if (!f) return;
                                const formData = new FormData();
                                formData.append('file', f);
                                try {
                                    const res = await apiService.uploadWhatsAppTemplateSample(formData);
                                    setForm(prev => ({ ...prev, headerHandle: res.data.header_handle }));
                                    setSnackbar({ open: true, message: 'Sample uploaded.', severity: 'success' });
                                } catch (error) {
                                    setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to upload sample.', severity: 'error' });
                                }
                            }} />
                        </Button>
                    )}
```

- [ ] **Step 6: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Manual browser verification**

Run: `npm start` in `frontend/`
Steps: open the builder dialog, select header type "Image", click "Upload Sample File", pick an image. Confirm either a success snackbar (real credentials) or a clear Meta-sourced error (placeholder credentials) — never a generic/silent failure.

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_whatsapp_templates.py frontend/src/context/AppContext.js frontend/src/components/WhatsAppTemplatesManager.js
git commit -m "feat: add media header sample upload via Meta's resumable upload API"
```

---

## Task 9: Convert "Approved Template Names" fields to dropdowns

**Files:**
- Modify: `frontend/src/components/SettingsView.js:407-454`

**Interfaces:**
- Consumes: `apiService.fetchWhatsAppTemplates` (Task 6).
- Produces: nothing consumed by later tasks (final task in this plan).

- [ ] **Step 1: Fetch approved templates into `SettingsView`**

Add state and a fetch effect near the top of the `SettingsView` component (after the existing `waForm`/`waFetching` state, `SettingsView.js:135-140`):
```jsx
    const [approvedTemplates, setApprovedTemplates] = useState([]);
    useEffect(() => {
        apiService.fetchWhatsAppTemplates()
            .then(res => setApprovedTemplates((res.data.templates || []).filter(t => t.status === 'APPROVED')))
            .catch(() => {}); // Settings page still works with free-text fallback if this fails
    }, [apiService]);
```

- [ ] **Step 2: Convert the 9 fields to dropdowns**

In `frontend/src/components/SettingsView.js:412-454`, replace each of the 9 `TextField` blocks. For example, change:
```jsx
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Payment Received Template" placeholder="payment_confirmation" {...waField('template_payment_paid')}
                                                helperText="Triggered when a payment is marked as paid"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
```
to:
```jsx
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth select label="Payment Received Template" {...waField('template_payment_paid')}
                                                helperText="Triggered when a payment is marked as paid"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }}>
                                                <MenuItem value={waForm.template_payment_paid}>{waForm.template_payment_paid || '(none selected)'}</MenuItem>
                                                {approvedTemplates.filter(t => t.name !== waForm.template_payment_paid).map(t => (
                                                    <MenuItem key={t.name} value={t.name}>{t.name}</MenuItem>
                                                ))}
                                            </TextField>
                                        </Grid>
```

Apply the same transformation to the other 8 fields (`template_subscription_renewed`, `template_payment_reminder`, `template_current_balance`, `template_forward_alert`, `template_bulk_outage`, `template_bulk_maintenance`, `template_bulk_feature`, `template_bulk_offer`), each keeping its own existing `helperText`/label and swapping only `TextField fullWidth label="..." placeholder="..." {...waField(key)}` for `TextField fullWidth select label="..." {...waField(key)}` plus the same "current value as first option, then every other approved template" `MenuItem` pattern (the current value is always shown as an option even if it's since become unapproved/renamed, so a tenant's existing saved setting is never silently blanked by this change).

- [ ] **Step 3: Manual browser verification**

Run: `npm start` in `frontend/`
Steps: with at least one `APPROVED` template synced (Task 2/6), open Settings → WhatsApp Notifications, confirm all 9 fields render as dropdowns listing that template plus the currently-saved value, and saving persists correctly through `handleWASave`.
Expected: no console errors; a tenant with zero approved templates yet still sees their existing saved value as the sole option, never a blank/broken field.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SettingsView.js
git commit -m "feat: convert WhatsApp template name fields to dropdowns sourced from approved templates"
```

---

## Self-Review Notes

- **Spec coverage**: every section of `docs/superpowers/specs/2026-08-28-whatsapp-template-management-design.md` maps to a task — Architecture/data model → Task 1; create/edit/delete/sync/list API → Tasks 2-4; status webhook → Task 5; Settings tab + list + builder + media upload → Tasks 6-8; dropdown conversion → Task 9. The `AUTHENTICATION`-category exclusion and APPROVED-edit-block non-goals are enforced in Task 3/4's validation, not just documented.
- **Type/name consistency verified across tasks**: `WhatsAppTemplate.to_dict()` (Task 1) fields match what Tasks 2-4's routes return and what Tasks 6-9's frontend reads (`status`, `rejected_reason`, `components`, `meta_template_id`); `_parse_meta_error` (Task 2) is reused verbatim by Tasks 3, 4, 8; apiService method names introduced in Task 6 (`fetchWhatsAppTemplates`, `syncWhatsAppTemplates`, `createWhatsAppTemplate`, `updateWhatsAppTemplate`, `deleteWhatsAppTemplate`) match exactly what Tasks 7-9 call, and `uploadWhatsAppTemplateSample` (Task 8) matches the builder dialog's usage.
- **No placeholders**: every step shows complete, real code against the actual current file contents (verified by reading `app.py`, `SettingsView.js`, `AppContext.js`, `ExpenseCategoryManager.js`, `SubscriptionPlanForm.js`, `tenancy.py`, an existing migration, and `tests/conftest.py`/`test_gating.py`/`test_whatsapp_keepalive.py`/`test_iso_webhook.py`/`test_lifecycle.py` directly before writing this plan).
