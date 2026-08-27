# Multi-Currency Accounting for Tenant Customer Billing — Implementation Plan

**Spec:** [docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md](../specs/2026-08-27-multi-currency-accounting-design.md) — read it first for the full data model, FX semantics, and the reasoning behind every decision below.

**Goal:** Backend-only multi-currency support for a tenant's own customer billing (`Customer`/`SubscriptionPlan`/`Payment`), opt-in per tenant (`BusinessSettings.multi_currency_enabled`, default off), with historical FX-rate locking on `Payment`, manual FX rate entry, and reporting-currency conversion. Bundled with the separately-scoped Float→Numeric migration for all 23 money columns in `app.py`.

**Implementation-time amendment (see spec's Non-goals for full reasoning):** Task 4 wires FX-locking into `add_payment` (`POST /api/payments`) only. A `grep -n "Payment(" app.py` audit during implementation found ~11 total `Payment`-creation call sites; the others (recurring/backdated billing generation, renewals, partial-payment remainder splits, reseller debt reassignment) sit inside batch loops with a single top-level `try/except`/rollback per tenant, where making FX lookup raise would risk aborting a whole tenant's billing run over one missing rate. Left unwired, flagged as follow-up work, not silently dropped — see the spec's updated Non-goals and Open Questions #5.

## Global constraints

- No scheduler involvement — FX rates are entered manually, never refreshed on a timer. The 2026-08-26 crash-loop lesson (scheduler-registered functions must be defined before `scheduler.add_job()`) does not apply to this plan; no task here touches the scheduler block (`app.py` ~2183-2196).
- Follow this repo's defensive-migration pattern throughout: `inspect(bind)` existence checks before every `ADD COLUMN`/`CREATE TABLE`, `NOTE:`-and-skip rather than crash if already present.
- Every new tenant-scoped model goes into `TENANT_OWNED_MODELS` (`app.py` ~944-953) so the `before_flush` tenant-stamping listener covers it. `Currency` is the one exception — a shared reference table, not tenant-owned.
- Money columns use `db.Numeric(18, 4)` after Task 6; FX rate columns (`ExchangeRate.rate`, `Payment.fx_rate_to_reporting`) use `db.Numeric(18, 8)` — see spec's Precision section for why the scales differ.
- No frontend changes in this plan — backend only, per the spec's explicit non-goal. Every task is a backend model/route/migration/test.
- Current Alembic head at plan-writing time: `95dfe810650a` (confirm with the command in Task 1 before writing the first migration — earlier tasks in the same plan may shift this).
- Run `python -m pytest -q` after every task; all tasks land on branch `phase4b-multi-currency-accounting` off `main`.

---

## Task 1: `Currency` + `ExchangeRate` models, migration, `fx.py` lookup helper

**Files:**
- Modify: `app.py` (new models, placed directly after `BillingPaymentAttempt`, ~`app.py:936`, before the `TENANT_OWNED_MODELS` tuple)
- Create: `fx.py`
- Create: `migrations/versions/<rev>_add_currency_and_exchange_rate.py`
- Test: Create `tests/test_multi_currency.py`

**Interfaces:**
- Produces: `Currency` model (`code` PK, `name`, `decimal_places`, `active`), `ExchangeRate` model (tenant-scoped), `fx.get_rate(tenant_id, from_code, to_code, as_of=None) -> Decimal`, `fx.FxRateMissingError`.

- [ ] **Step 1: Confirm the current migration head**

```bash
python -c "
import re, glob
heads, downs = {}, set()
for f in glob.glob('migrations/versions/*.py'):
    text = open(f, encoding='utf-8').read()
    rev = re.search(r\"revision = '([^']+)'\", text)
    down = re.search(r\"down_revision = '?([^'\n]+)'?\", text)
    if rev: heads[rev.group(1)] = f
    if down and down.group(1) != 'None': downs.add(down.group(1))
print('HEAD:', set(heads.keys()) - downs)
"
```
Expected: `HEAD: {'95dfe810650a'}` (if different, use the real head — this is why it's a command, not a hardcoded assumption).

- [ ] **Step 2: Write the failing test**

Create `tests/test_multi_currency.py`:

```python
"""Multi-currency accounting for tenant customer billing -- see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md.
Platform-subscription (Whish) billing stays USD-only and is untouched here."""
from datetime import datetime, timedelta
from decimal import Decimal
import pytest
import app as appmod
from tests.conftest import make_tenant


def test_currency_seed_rows_exist(app, client):
    with app.app_context():
        usd = appmod.Currency.query.get('USD')
        lbp = appmod.Currency.query.get('LBP')
        assert usd is not None and usd.decimal_places == 2
        assert lbp is not None and lbp.decimal_places == 0


def test_exchange_rate_model_roundtrip(app, client):
    make_tenant(client, "Biz FX", "fx_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz FX").first()
        rate = appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('89542.37'),
        )
        appmod.db.session.add(rate)
        appmod.db.session.commit()
        fetched = appmod.ExchangeRate.query.filter_by(tenant_id=tenant.id).first()
        assert fetched.rate == Decimal('89542.37')
        assert fetched.source == 'manual'


def test_fx_get_rate_same_currency_is_always_one_with_no_query(app, client, monkeypatch):
    make_tenant(client, "Biz Same", "same_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Same").first()

        def _boom(*a, **k):
            raise AssertionError("must not query ExchangeRate when from==to")
        monkeypatch.setattr(appmod.fx.ExchangeRate, "query", property(_boom))
        assert appmod.fx.get_rate(tenant.id, 'USD', 'USD') == Decimal('1')


def test_fx_get_rate_direct_pair(app, client):
    make_tenant(client, "Biz Direct", "direct_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Direct").first()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('90000')))
        appmod.db.session.commit()
        assert appmod.fx.get_rate(tenant.id, 'USD', 'LBP') == Decimal('90000')


def test_fx_get_rate_inverse_pair_fallback(app, client):
    make_tenant(client, "Biz Inverse", "inverse_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Inverse").first()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('100000')))
        appmod.db.session.commit()
        assert appmod.fx.get_rate(tenant.id, 'LBP', 'USD') == Decimal('1') / Decimal('100000')


def test_fx_get_rate_as_of_uses_the_rate_effective_at_that_time(app, client):
    make_tenant(client, "Biz AsOf", "asof_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz AsOf").first()
        old = datetime.utcnow() - timedelta(days=10)
        new = datetime.utcnow()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('85000'), effective_at=old))
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency='USD', to_currency='LBP', rate=Decimal('90000'), effective_at=new))
        appmod.db.session.commit()
        as_of_before_new_rate = new - timedelta(days=1)
        assert appmod.fx.get_rate(tenant.id, 'USD', 'LBP', as_of=as_of_before_new_rate) == Decimal('85000')
        assert appmod.fx.get_rate(tenant.id, 'USD', 'LBP', as_of=new) == Decimal('90000')


def test_fx_get_rate_missing_raises(app, client):
    make_tenant(client, "Biz Missing", "missing_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Missing").first()
        with pytest.raises(appmod.fx.FxRateMissingError):
            appmod.fx.get_rate(tenant.id, 'USD', 'LBP')


def test_exchange_rate_is_tenant_isolated(app, client):
    make_tenant(client, "Biz IsoA", "isoa_admin")
    make_tenant(client, "Biz IsoB", "isob_admin")
    with app.app_context():
        tenant_a = appmod.Tenant.query.filter_by(name="Biz IsoA").first()
        tenant_b = appmod.Tenant.query.filter_by(name="Biz IsoB").first()
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant_a.id, from_currency='USD', to_currency='LBP', rate=Decimal('90000')))
        appmod.db.session.commit()
        with pytest.raises(appmod.fx.FxRateMissingError):
            appmod.fx.get_rate(tenant_b.id, 'USD', 'LBP')
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'Currency'`.

- [ ] **Step 4: Add the models**

In `app.py`, directly after `BillingPaymentAttempt`'s closing line (~`app.py:935`), before the `TENANT_OWNED_MODELS` comment block:

```python
class Currency(db.Model):
    """Reference table of currencies this deployment knows about -- NOT
    tenant-scoped (shared, like plans.PLANS). Seeded with USD/LBP by the
    migration; adding a third currency is a data insert, not a schema
    change. See docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md."""
    code = db.Column(db.String(3), primary_key=True)  # ISO 4217
    name = db.Column(db.String(50), nullable=False)
    decimal_places = db.Column(db.Integer, nullable=False, default=2)
    active = db.Column(db.Boolean, nullable=False, default=True)

    def to_dict(self):
        return {'code': self.code, 'name': self.name,
                'decimal_places': self.decimal_places, 'active': self.active}


class ExchangeRate(db.Model):
    """A tenant-entered FX rate, effective from a point in time until superseded
    by a later one. Historical Payment rows never re-read this table after
    creation (see Payment.fx_rate_to_reporting) -- this table is consulted only
    at payment-creation time (to pick the rate to lock) and for live (not
    historical) conversions. See
    docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    from_currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False)
    to_currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False)
    rate = db.Column(db.Numeric(18, 8), nullable=False)  # 1 unit of from_currency = `rate` units of to_currency
    effective_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    source = db.Column(db.String(20), nullable=False, default='manual')  # 'manual' today; reserved for a future API source
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_exchange_rate_tenant_pair_effective', 'tenant_id', 'from_currency', 'to_currency', 'effective_at'),
    )

    def to_dict(self):
        return {
            'id': self.id, 'from_currency': self.from_currency, 'to_currency': self.to_currency,
            'rate': float(self.rate), 'effective_at': self.effective_at.strftime('%Y-%m-%d %H:%M:%S'),
            'source': self.source, 'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        }
```

Add `ExchangeRate` (not `Currency`) to `TENANT_OWNED_MODELS` (~`app.py:944`):

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

- [ ] **Step 5: Write `fx.py`**

Create `fx.py`:

```python
"""FX rate lookup for tenant customer billing -- see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md.

Manual-entry only (no external API). Historical Payment rows never call back
into this module after creation -- they store their own locked
fx_rate_to_reporting. This module is consulted only (a) at payment-creation
time to pick the rate to lock, and (b) for live (non-historical) conversions.
"""
from decimal import Decimal
from datetime import datetime


class FxRateMissingError(Exception):
    """Raised when no applicable ExchangeRate (direct or inverse) exists for
    a tenant/currency-pair/as_of. Callers must not silently default to 1 or
    guess -- a missing rate blocks the operation that needed it."""


def get_rate(tenant_id, from_code, to_code, as_of=None):
    """Return the Decimal rate to convert 1 unit of from_code into to_code,
    as of `as_of` (default: now). Same-currency is always exactly 1, with no
    DB query. Otherwise looks up the latest direct-pair ExchangeRate row with
    effective_at <= as_of; falls back to the inverse pair (1/rate) if no
    direct row exists. Raises FxRateMissingError if neither exists."""
    from app import ExchangeRate  # local import: fx.py has no other app.py dependency

    if from_code == to_code:
        return Decimal('1')

    as_of = as_of or datetime.utcnow()

    direct = (
        ExchangeRate.query
        .filter(ExchangeRate.tenant_id == tenant_id,
                ExchangeRate.from_currency == from_code,
                ExchangeRate.to_currency == to_code,
                ExchangeRate.effective_at <= as_of)
        .order_by(ExchangeRate.effective_at.desc())
        .first()
    )
    if direct:
        return Decimal(direct.rate)

    inverse = (
        ExchangeRate.query
        .filter(ExchangeRate.tenant_id == tenant_id,
                ExchangeRate.from_currency == to_code,
                ExchangeRate.to_currency == from_code,
                ExchangeRate.effective_at <= as_of)
        .order_by(ExchangeRate.effective_at.desc())
        .first()
    )
    if inverse:
        return Decimal('1') / Decimal(inverse.rate)

    raise FxRateMissingError(
        f"No exchange rate on file for {from_code}->{to_code} (tenant {tenant_id}) as of {as_of}.")
```

Add `import fx` to `app.py`'s import block, alongside `import email_util` / `import upstream_portal` (~`app.py:107`).

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: still FAIL — the models exist in Python now, but `db.create_all()` in `tests/conftest.py` builds from live metadata so the tables *will* exist in the test DB; the Currency seed rows will NOT exist yet (nothing seeds them outside the migration). Confirm the failure is now specifically `test_currency_seed_rows_exist` failing on `assert usd is not None`, not an `AttributeError` — this is expected at this point, fixed in Step 7.

- [ ] **Step 7: Seed `Currency` rows for tests**

Tests build their schema via `db.create_all()`, not via Alembic (`tests/conftest.py` says so explicitly) — so the migration's seed data (Step 8) never runs in tests. Add seeding to the `app` fixture. Modify `tests/conftest.py`:

```python
@pytest.fixture
def app():
    flask_app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    with flask_app.app_context():
        db.create_all()
        # Currency is a small reference table normally seeded by its Alembic
        # migration (see migrations/versions/*_add_currency_and_exchange_rate.py);
        # tests build schema via create_all(), not migrations, so seed it here too.
        from app import Currency
        db.session.add(Currency(code='USD', name='US Dollar', decimal_places=2))
        db.session.add(Currency(code='LBP', name='Lebanese Pound', decimal_places=0))
        db.session.commit()
        yield flask_app
        db.session.remove()
        db.drop_all()
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: PASS (8 passed)

- [ ] **Step 9: Write the migration**

Create `migrations/versions/<new_revision>_add_currency_and_exchange_rate.py` (generate a fresh 12-char hex revision id; examples use `c1e4d9a02f6b` as a placeholder):

```python
"""add currency and exchange_rate tables

Revision ID: c1e4d9a02f6b
Revises: 95dfe810650a
Create Date: 2026-08-27

Multi-currency accounting for tenant customer billing (see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
Additive-only: two new tables, one seeded with USD/LBP. Follows this repo's
defensive-migration discipline (existence checks, skip-with-NOTE rather than
crash) per c57bc44a51d0's documented rationale.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'c1e4d9a02f6b'
down_revision = '95dfe810650a'
branch_labels = None
depends_on = None

_SEED_CURRENCIES = [
    ('USD', 'US Dollar', 2),
    ('LBP', 'Lebanese Pound', 0),
]


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if 'currency' not in existing_tables:
        op.create_table(
            'currency',
            sa.Column('code', sa.String(length=3), primary_key=True),
            sa.Column('name', sa.String(length=50), nullable=False),
            sa.Column('decimal_places', sa.Integer(), nullable=False, server_default='2'),
            sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    else:
        print("NOTE: currency table already exists -- skipping create (nothing to do).")

    currency_table = sa.table(
        'currency', sa.column('code', sa.String), sa.column('name', sa.String),
        sa.column('decimal_places', sa.Integer), sa.column('active', sa.Boolean),
    )
    existing_codes = {row[0] for row in bind.execute(sa.text("SELECT code FROM currency"))} \
        if 'currency' in set(inspect(bind).get_table_names()) else set()
    for code, name, decimals in _SEED_CURRENCIES:
        if code in existing_codes:
            print(f"NOTE: currency '{code}' already seeded -- skipping insert.")
            continue
        op.bulk_insert(currency_table, [{'code': code, 'name': name, 'decimal_places': decimals, 'active': True}])

    if 'exchange_rate' not in existing_tables:
        op.create_table(
            'exchange_rate',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
            sa.Column('from_currency', sa.String(length=3), sa.ForeignKey('currency.code'), nullable=False),
            sa.Column('to_currency', sa.String(length=3), sa.ForeignKey('currency.code'), nullable=False),
            sa.Column('rate', sa.Numeric(18, 8), nullable=False),
            sa.Column('effective_at', sa.DateTime(), nullable=False),
            sa.Column('source', sa.String(length=20), nullable=False, server_default='manual'),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_exchange_rate_tenant_id', 'exchange_rate', ['tenant_id'])
        op.create_index(
            'ix_exchange_rate_tenant_pair_effective', 'exchange_rate',
            ['tenant_id', 'from_currency', 'to_currency', 'effective_at'])
    else:
        print("NOTE: exchange_rate table already exists -- skipping create (nothing to do).")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if 'exchange_rate' in existing_tables:
        op.drop_table('exchange_rate')
    if 'currency' in existing_tables:
        op.drop_table('currency')
```

- [ ] **Step 10: Commit**

```bash
git checkout -b phase4b-multi-currency-accounting main
git add app.py fx.py tests/conftest.py tests/test_multi_currency.py migrations/versions/*_add_currency_and_exchange_rate.py
git commit -m "Add Currency/ExchangeRate models, fx.get_rate lookup, and migration"
```

---

## Task 2: `BusinessSettings` opt-in flag + reporting currency

**Files:**
- Modify: `app.py` (`BusinessSettings` model ~`app.py:698`, `save_business_settings`/`get_business_settings` routes ~`app.py:4275`)
- Modify: `migrations/versions/<rev-from-Task-1>_add_currency_and_exchange_rate.py`'s successor — a **new** migration, not amending Task 1's.
- Test: Add to `tests/test_multi_currency.py`

**Interfaces:**
- Produces: `BusinessSettings.multi_currency_enabled` (bool, default False), `BusinessSettings.reporting_currency` (str(3), default 'USD', FK to `currency.code`), both in `to_dict()`. `POST /api/business-settings` accepts both as form fields.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_multi_currency.py`:

```python
def test_business_settings_default_single_currency(app, client):
    hdr = make_tenant(client, "Biz Default", "default_admin")
    r = client.get("/api/business-settings", headers=hdr)
    body = r.get_json()['settings']
    assert body['multi_currency_enabled'] is False
    assert body['reporting_currency'] == 'USD'


def test_business_settings_can_opt_into_multi_currency(app, client):
    hdr = make_tenant(client, "Biz OptIn", "optin_admin")
    r = client.post("/api/business-settings", headers=hdr, data={
        "business_name": "Biz OptIn", "address": "addr", "mobile": "123",
        "multi_currency_enabled": "true", "reporting_currency": "LBP",
    })
    assert r.status_code == 200
    body = r.get_json()['settings']
    assert body['multi_currency_enabled'] is True
    assert body['reporting_currency'] == 'LBP'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_currency.py -k business_settings -v`
Expected: FAIL — `KeyError: 'multi_currency_enabled'`.

- [ ] **Step 3: Add the columns**

Modify `BusinessSettings` in `app.py` (~`app.py:720`, right after `upstream_sync_automation_enabled`):

```python
    upstream_sync_automation_enabled = db.Column(db.Boolean, nullable=False, default=False)
    # Multi-currency accounting for THIS tenant's own customer billing (see
    # docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
    # Off by default, same opt-in precedent as upstream_sync_automation_enabled
    # above -- an opted-out tenant sees zero behavior change. reporting_currency
    # exists regardless of the flag: it is "this tenant's one currency" for a
    # single-currency tenant too, so report-aggregation code has exactly one
    # path (always convert-and-sum) rather than a flag-gated branch.
    multi_currency_enabled = db.Column(db.Boolean, nullable=False, default=False)
    reporting_currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False, default='USD')
```

Add both fields to `to_dict()` (~`app.py:731`):

```python
            'upstream_sync_automation_enabled': bool(self.upstream_sync_automation_enabled),
            'multi_currency_enabled': bool(self.multi_currency_enabled),
            'reporting_currency': self.reporting_currency or 'USD',
```

- [ ] **Step 4: Wire the form fields into both routes**

In `save_business_settings` (~`app.py:4282`), add to the `if not settings:` construction block:

```python
                upstream_sync_automation_enabled=_parse_bool_form_field(
                    request.form.get('upstream_sync_automation_enabled'), default=False),
                multi_currency_enabled=_parse_bool_form_field(
                    request.form.get('multi_currency_enabled'), default=False),
                reporting_currency=request.form.get('reporting_currency', 'USD'),
```

And to the update-existing-settings block (~`app.py:4308`):

```python
        if 'upstream_sync_automation_enabled' in request.form:
            settings.upstream_sync_automation_enabled = _parse_bool_form_field(
                request.form.get('upstream_sync_automation_enabled'), default=settings.upstream_sync_automation_enabled)
        if 'multi_currency_enabled' in request.form:
            settings.multi_currency_enabled = _parse_bool_form_field(
                request.form.get('multi_currency_enabled'), default=settings.multi_currency_enabled)
        if 'reporting_currency' in request.form:
            new_reporting_currency = request.form.get('reporting_currency')
            if not Currency.query.filter_by(code=new_reporting_currency, active=True).first():
                return jsonify({'error': f"Unknown or inactive currency code '{new_reporting_currency}'."}), 400
            settings.reporting_currency = new_reporting_currency
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: PASS (10 passed)

- [ ] **Step 6: Write the migration**

Determine the new head (Task 1's revision id), then create `migrations/versions/<new_revision>_add_business_settings_multi_currency_fields.py` following the exact `inspect(bind)`-existence-check pattern used in `95dfe810650a_add_whish_billing_fields.py` (Task 1's Whish migration, same shape: `batch_alter_table('business_settings')`, add both columns with `server_default`, skip-with-NOTE if already present). Downgrade drops both columns the same way.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_multi_currency.py migrations/versions/*_add_business_settings_multi_currency_fields.py
git commit -m "Add BusinessSettings.multi_currency_enabled and reporting_currency"
```

---

## Task 3: `SubscriptionPlan.currency`

**Files:**
- Modify: `app.py` (`SubscriptionPlan` model ~`app.py:387`, `add_subscription_plan`/`update_subscription_plan` routes)
- Test: Add to `tests/test_multi_currency.py`

- [ ] **Step 1: Write the failing test**

```python
def test_subscription_plan_defaults_to_usd(app, client):
    hdr = make_tenant(client, "Biz PlanCur", "plancur_admin")
    r = client.post("/api/subscription_plans", headers=hdr, json={
        "name": "Fiber 50", "price": 30.0, "cost": 10.0, "billing_cycle": "monthly"})
    assert r.get_json()['plan']['currency'] == 'USD'


def test_subscription_plan_currency_can_be_set_explicitly(app, client):
    hdr = make_tenant(client, "Biz PlanLbp", "planlbp_admin")
    r = client.post("/api/subscription_plans", headers=hdr, json={
        "name": "Fiber 50 LBP", "price": 2700000.0, "cost": 900000.0,
        "billing_cycle": "monthly", "currency": "LBP"})
    assert r.get_json()['plan']['currency'] == 'LBP'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_currency.py -k subscription_plan_ -v`
Expected: FAIL — `KeyError: 'currency'`.

- [ ] **Step 3: Add the column, `to_dict`, and route wiring**

`SubscriptionPlan` (~`app.py:391`), right after `price`/`cost`:

```python
    price = db.Column(db.Float, nullable=False)
    cost = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False, default='USD')
```

`to_dict()` (~`app.py:401`): add `'currency': self.currency or 'USD',`.

Find `add_subscription_plan` (the route immediately before `update_subscription_plan`, ~`app.py:2940`-ish — locate with `grep -n "def add_subscription_plan" app.py`) and add currency handling: validate against `Currency` if provided, default `'USD'`:

```python
        currency = data.get('currency', 'USD')
        if not Currency.query.filter_by(code=currency, active=True).first():
            return jsonify({'error': f"Unknown or inactive currency code '{currency}'."}), 400
```
...and pass `currency=currency` into the `SubscriptionPlan(...)` / `new_for_tenant(SubscriptionPlan, ...)` construction call.

`update_subscription_plan` (~`app.py:2975`): add, after `plan.cost = ...`:
```python
        if 'currency' in data:
            new_currency = data['currency']
            if not Currency.query.filter_by(code=new_currency, active=True).first():
                return jsonify({'error': f"Unknown or inactive currency code '{new_currency}'."}), 400
            plan.currency = new_currency
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Migration + commit**

Create `migrations/versions/<new_revision>_add_subscription_plan_currency.py`, same defensive pattern (`batch_alter_table('subscription_plan')`, add `currency` with `server_default='USD'`, skip-with-NOTE).

```bash
git add app.py tests/test_multi_currency.py migrations/versions/*_add_subscription_plan_currency.py
git commit -m "Add SubscriptionPlan.currency"
```

---

## Task 4: `Payment.currency` + `Payment.fx_rate_to_reporting` — the rate-locking core

**This is the task the spec's "historical rate-locking" requirement lives in — read the spec's FX rate entry/lookup/locking semantics section again before editing.**

**Files:**
- Modify: `app.py` (`Payment` model ~`app.py:629`, `add_payment` route ~`app.py:3035`)
- Test: Add to `tests/test_multi_currency.py`

- [ ] **Step 1: Write the failing test**

```python
def test_add_payment_opted_out_tenant_locks_rate_one(app, client):
    hdr = make_tenant(client, "Biz NoMC", "nomc_admin")
    with app.app_context():
        plan = appmod.SubscriptionPlan.query.filter_by(name="Basic Plan").first() or \
            appmod.tenant_query(appmod.SubscriptionPlan).first()
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust A", "phone": "123", "address": "addr",
        "subscription_plan_id": plan.id})
    customer_id = r.get_json()['customer']['id'] if r.status_code == 201 else None
    assert customer_id, r.get_json()
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 10.0, "reason": "test", "pre_payment": True})
    assert r.status_code == 201
    with app.app_context():
        payment = appmod.Payment.query.filter_by(customer_id=customer_id).order_by(appmod.Payment.id.desc()).first()
        assert payment.currency == 'USD'
        assert payment.fx_rate_to_reporting == 1


def test_add_payment_opted_in_tenant_without_rate_returns_400(app, client):
    hdr = make_tenant(client, "Biz NoRate", "norate_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "Biz NoRate", "address": "a", "mobile": "1",
        "multi_currency_enabled": "true", "reporting_currency": "USD"})
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz NoRate").first()
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="LBP Plan", price=1000000.0,
                                        cost=0.0, billing_cycle="monthly", currency="LBP")
        appmod.db.session.add(plan)
        appmod.db.session.commit()
        plan_id = plan.id
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust B", "phone": "123", "address": "addr", "subscription_plan_id": plan_id})
    customer_id = r.get_json()['customer']['id']
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 500000.0, "reason": "test", "pre_payment": True})
    assert r.status_code == 400
    with app.app_context():
        assert appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).count() == 0


def test_add_payment_locks_rate_and_later_rate_changes_dont_affect_it(app, client):
    hdr = make_tenant(client, "Biz LockedRate", "lockedrate_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "Biz LockedRate", "address": "a", "mobile": "1",
        "multi_currency_enabled": "true", "reporting_currency": "USD"})
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz LockedRate").first()
        plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="LBP Plan 2", price=1000000.0,
                                        cost=0.0, billing_cycle="monthly", currency="LBP")
        appmod.db.session.add(plan)
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency="LBP", to_currency="USD", rate=0.0000111))
        appmod.db.session.commit()
        plan_id = plan.id
        tenant_id = tenant.id
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust C", "phone": "123", "address": "addr", "subscription_plan_id": plan_id})
    customer_id = r.get_json()['customer']['id']
    r = client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 500000.0, "reason": "test", "pre_payment": True})
    assert r.status_code == 201
    with app.app_context():
        payment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).first()
        first_rate = payment.fx_rate_to_reporting
        assert first_rate == pytest.approx(0.0000111)

        # A new rate is entered afterward -- must not retroactively change the locked payment.
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant_id, from_currency="LBP", to_currency="USD", rate=0.0000200))
        appmod.db.session.commit()
        payment = appmod.Payment.query.filter_by(customer_id=customer_id, pre_payment=True).first()
        assert payment.fx_rate_to_reporting == first_rate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_currency.py -k "add_payment" -v`
Expected: FAIL — `AttributeError: 'Payment' object has no attribute 'currency'`.

- [ ] **Step 3: Add the columns**

`Payment` model (~`app.py:633`), right after `amount`:

```python
    amount = db.Column(db.Float, nullable=False)
    # Multi-currency (see docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
    # `currency` is the currency `amount` is denominated in (inherited from the
    # customer's subscription_plan.currency at the moment this payment was
    # created). `fx_rate_to_reporting` is the rate used to convert `amount`
    # into the tenant's reporting_currency AT THAT MOMENT -- frozen forever,
    # never recomputed when new ExchangeRate rows are added later. This is the
    # actual historical rate-locking mechanism.
    currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False, default='USD')
    fx_rate_to_reporting = db.Column(db.Numeric(18, 8), nullable=False, default=1)
```

- [ ] **Step 4: Wire `add_payment`**

Modify `add_payment` (~`app.py:3054`), right after the customer lookup and before constructing `new_payment`:

```python
        settings = get_tenant_settings(BusinessSettings, business_name="Default Business", address="", mobile="")
        subscription_plan = tenant_query(SubscriptionPlan).filter_by(id=customer.subscription_plan_id).first()
        payment_currency = subscription_plan.currency if subscription_plan else 'USD'
        try:
            locked_rate = fx.get_rate(current_tenant_id(), payment_currency, settings.reporting_currency, as_of=payment_date)
        except fx.FxRateMissingError as e:
            return jsonify({'error': (
                f"No exchange rate on file for {payment_currency}->{settings.reporting_currency} "
                f"as of this payment's date; enter one under Settings -> Exchange Rates first.")}), 400
```

Then add `currency=payment_currency, fx_rate_to_reporting=locked_rate` to the `Payment(...)` construction call.

Note: for an opted-out (single-currency) tenant, `settings.reporting_currency` is `'USD'` and `subscription_plan.currency` is `'USD'` (Task 3's default), so `fx.get_rate` always hits the same-currency short-circuit and returns exactly `1` with no DB query — this is what makes Step 1's `test_add_payment_opted_out_tenant_locks_rate_one` pass without ever creating an `ExchangeRate` row, and is the concrete verification of the spec's "opted-out tenant behavior is unchanged" claim.

Also add `'currency': new_payment.currency` to the route's JSON response dict for completeness (not required by tests, but consistent with the rest of the payload).

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: PASS (15 passed)

- [ ] **Step 6: Migration + commit**

Create `migrations/versions/<new_revision>_add_payment_currency_and_fx_rate.py`: `batch_alter_table('payment')`, add `currency` (String(3), server_default `'USD'`) and `fx_rate_to_reporting` (Numeric(18,8), server_default `'1'`), skip-with-NOTE if present.

```bash
git add app.py tests/test_multi_currency.py migrations/versions/*_add_payment_currency_and_fx_rate.py
git commit -m "Add Payment.currency and fx_rate_to_reporting (historical rate-locking)"
```

---

## Task 5: Exchange rate CRUD API

**Files:**
- Modify: `app.py` (new routes, placed near the `business-settings` routes ~`app.py:4325`)
- Test: Add to `tests/test_multi_currency.py`

**Interfaces:**
- Produces: `POST /api/exchange-rates` (JWT + admin), `GET /api/exchange-rates` (JWT).

- [ ] **Step 1: Write the failing test**

```python
def test_post_exchange_rate_rejected_when_multi_currency_disabled(app, client):
    hdr = make_tenant(client, "Biz FxOff", "fxoff_admin")
    r = client.post("/api/exchange-rates", headers=hdr, json={
        "from_currency": "USD", "to_currency": "LBP", "rate": 90000})
    assert r.status_code == 400


def test_post_exchange_rate_rejects_unknown_currency(app, client):
    hdr = make_tenant(client, "Biz FxBadCode", "fxbadcode_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "x", "address": "a", "mobile": "1", "multi_currency_enabled": "true"})
    r = client.post("/api/exchange-rates", headers=hdr, json={
        "from_currency": "USD", "to_currency": "ZZZ", "rate": 90000})
    assert r.status_code == 400


def test_post_exchange_rate_rejects_non_positive_rate(app, client):
    hdr = make_tenant(client, "Biz FxNeg", "fxneg_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "x", "address": "a", "mobile": "1", "multi_currency_enabled": "true"})
    r = client.post("/api/exchange-rates", headers=hdr, json={
        "from_currency": "USD", "to_currency": "LBP", "rate": 0})
    assert r.status_code == 400


def test_post_and_get_exchange_rate_happy_path(app, client):
    hdr = make_tenant(client, "Biz FxOk", "fxok_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "x", "address": "a", "mobile": "1", "multi_currency_enabled": "true"})
    r = client.post("/api/exchange-rates", headers=hdr, json={
        "from_currency": "USD", "to_currency": "LBP", "rate": 89542.37})
    assert r.status_code == 201
    r = client.get("/api/exchange-rates", headers=hdr)
    rates = r.get_json()['exchange_rates']
    assert len(rates) == 1 and rates[0]['from_currency'] == 'USD' and rates[0]['rate'] == pytest.approx(89542.37)


def test_exchange_rates_are_tenant_isolated(app, client):
    hdr_a = make_tenant(client, "Biz FxIsoA", "fxisoa_admin")
    hdr_b = make_tenant(client, "Biz FxIsoB", "fxisob_admin")
    for hdr, name in ((hdr_a, "Biz FxIsoA"), (hdr_b, "Biz FxIsoB")):
        client.post("/api/business-settings", headers=hdr, data={
            "business_name": name, "address": "a", "mobile": "1", "multi_currency_enabled": "true"})
    client.post("/api/exchange-rates", headers=hdr_a, json={
        "from_currency": "USD", "to_currency": "LBP", "rate": 90000})
    r = client.get("/api/exchange-rates", headers=hdr_b)
    assert r.get_json()['exchange_rates'] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_currency.py -k exchange_rate -v` (the model-level tests from Task 1 will pass; the route tests fail 404).

- [ ] **Step 3: Add the routes**

```python
@app.route('/api/exchange-rates', methods=['POST'])
@jwt_required()
@admin_required()
def create_exchange_rate():
    settings = tenant_query(BusinessSettings).first()
    if not settings or not settings.multi_currency_enabled:
        return jsonify({'error': 'Multi-currency accounting is not enabled for this business.'}), 400

    data = request.json or {}
    from_currency = data.get('from_currency')
    to_currency = data.get('to_currency')
    if not Currency.query.filter_by(code=from_currency, active=True).first():
        return jsonify({'error': f"Unknown or inactive currency code '{from_currency}'."}), 400
    if not Currency.query.filter_by(code=to_currency, active=True).first():
        return jsonify({'error': f"Unknown or inactive currency code '{to_currency}'."}), 400
    try:
        rate = float(data.get('rate'))
    except (TypeError, ValueError):
        return jsonify({'error': 'rate must be a valid number.'}), 400
    if not math.isfinite(rate) or rate <= 0:
        return jsonify({'error': 'rate must be greater than zero.'}), 400

    effective_at = datetime.utcnow()
    if data.get('effective_at'):
        try:
            effective_at = datetime.fromisoformat(data['effective_at'].replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return jsonify({'error': 'effective_at must be a valid ISO-8601 datetime.'}), 400

    claims = get_jwt()
    row = new_for_tenant(
        ExchangeRate, from_currency=from_currency, to_currency=to_currency, rate=rate,
        effective_at=effective_at, created_by_id=claims.get('sub') if isinstance(claims.get('sub'), int) else None,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({'message': 'Exchange rate added.', 'exchange_rate': row.to_dict()}), 201


@app.route('/api/exchange-rates', methods=['GET'])
@jwt_required()
def list_exchange_rates():
    rows = tenant_query(ExchangeRate).order_by(ExchangeRate.effective_at.desc()).all()
    return jsonify({'exchange_rates': [r.to_dict() for r in rows]}), 200
```

Note: check how `get_jwt()`'s `sub` claim is populated elsewhere in this file (`grep -n "create_access_token" app.py`) before trusting `claims.get('sub')` is the numeric user id — if the identity claim is structured differently (e.g. a dict, or under a different key), adjust `created_by_id` accordingly; it's optional (`nullable=True`) so getting this wrong degrades to `None`, not a crash, but should still be correct.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: PASS (20 passed)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_multi_currency.py
git commit -m "Add exchange-rate CRUD API (POST/GET /api/exchange-rates)"
```

---

## Task 6: Float→Numeric migration for all 23 money columns

**Files:**
- Modify: `app.py` (23 `db.Column(db.Float...)` → `db.Column(db.Numeric(18, 4)...)`, listed in the spec's corrected table)
- Create: `migrations/versions/<new_revision>_convert_money_columns_to_numeric.py`
- Test: Add to `tests/test_multi_currency.py`

**Interfaces:** No behavioral interface change — every `to_dict()` already wraps these fields in `float(...)`, which remains correct for a `Decimal` input.

- [ ] **Step 1: Write the failing test**

```python
from decimal import Decimal as _Decimal


def test_money_columns_are_numeric_not_float(app, client):
    """Confirms the ORM-level column type, not just DB storage -- SQLite is
    dynamically typed so this is the meaningful check in this test env; the
    real type-safety confirmation is the Postgres dry-run (see the plan's
    migration-verification step, not exercised by pytest)."""
    import sqlalchemy as sa
    numeric_targets = [
        (appmod.Reseller, 'balance'), (appmod.ResellerPayment, 'amount'),
        (appmod.UpstreamProvider, 'balance'), (appmod.UpstreamProviderPayment, 'amount'),
        (appmod.Customer, 'balance'), (appmod.Customer, 'discount'), (appmod.Customer, 'cost_override'),
        (appmod.SubscriptionPlan, 'price'), (appmod.SubscriptionPlan, 'cost'),
        (appmod.Supplier, 'balance'), (appmod.SupplierPayment, 'amount'),
        (appmod.Expense, 'amount'),
        (appmod.Employee, 'monthly_salary'), (appmod.Employee, 'balance'),
        (appmod.SalaryCharge, 'amount'), (appmod.SalaryPayment, 'amount'),
        (appmod.MonthlyProfitEstimate, 'estimated_income'), (appmod.MonthlyProfitEstimate, 'estimated_cost'),
        (appmod.MonthlyProfitEstimate, 'estimated_profit'),
        (appmod.Payment, 'amount'), (appmod.Payment, 'collected_amount'),
        (appmod.AddonPurchase, 'amount'), (appmod.BillingPaymentAttempt, 'amount'),
    ]
    for model, colname in numeric_targets:
        coltype = getattr(model, colname).type
        assert isinstance(coltype, sa.Numeric), f"{model.__name__}.{colname} is {type(coltype)}, expected Numeric"


def test_money_values_still_round_trip_as_float_via_to_dict(app, client):
    hdr = make_tenant(client, "Biz NumericRT", "numericrt_admin")
    with app.app_context():
        plan = appmod.tenant_query(appmod.SubscriptionPlan).first()
        assert isinstance(plan.to_dict()['price'], float)
        assert plan.to_dict()['price'] == float(plan.price)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_currency.py -k "numeric or round_trip" -v`
Expected: FAIL — every `assert isinstance(coltype, sa.Numeric)` fails (columns are still `Float`).

- [ ] **Step 3: Convert the 23 columns**

In `app.py`, change each of the following from `db.Float` to `db.Numeric(18, 4)`, preserving every existing `nullable=`/`default=` argument unchanged:

```
Reseller.balance (~208), ResellerPayment.amount (~228),
UpstreamProvider.balance (~255), UpstreamProviderPayment.amount (~277),
Customer.balance (~348), Customer.discount (~349), Customer.cost_override (~350),
SubscriptionPlan.price (~391), SubscriptionPlan.cost (~392),
Supplier.balance (~423), SupplierPayment.amount (~443),
Expense.amount (~507),
Employee.monthly_salary (~536), Employee.balance (~540),
SalaryCharge.amount (~564), SalaryPayment.amount (~586),
MonthlyProfitEstimate.estimated_income (~613), MonthlyProfitEstimate.estimated_cost (~614),
MonthlyProfitEstimate.estimated_profit (~615),
Payment.amount (~633), Payment.collected_amount (~652),
AddonPurchase.amount (~692), BillingPaymentAttempt.amount (~929)
```

Re-confirm every line number with `grep -n "db.Column(db.Float" app.py` before editing — earlier tasks in this plan do not touch these lines, but line numbers should always be re-verified immediately before an edit, not assumed from a plan written before those edits landed.

Example (Reseller, unchanged `default=0.0`):
```python
    balance = db.Column(db.Numeric(18, 4), default=0.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: PASS (22 passed)

- [ ] **Step 5: Run the FULL suite — this task touches the most shared code of any task in this plan**

Run: `python -m pytest -q`
Expected: all green. If any existing test fails on a `Decimal`-vs-`float` comparison or arithmetic (`Decimal` doesn't mix with `float` in Python arithmetic — `Decimal('1') + 0.5` raises `TypeError`), the fix is almost always in application code doing `column + float_literal` arithmetic that now needs `float(column)` on one side, or accepting that SQLAlchemy already coerces bind params through the column's Python type — fix the specific failing call site, do not loosen the test. Document each such fix here if any are needed (expected candidates: any code path building an in-Python running total by adding a `Float` model attribute to a plain Python `float`, e.g. `reseller.balance += amount_due` — `Numeric` columns return `Decimal` in Python; check whether SQLAlchemy's `Numeric` type needs `asdecimal=False` to keep returning `float` instead, which would avoid touching arithmetic call sites at all. **Decide this explicitly and document the choice**: `asdecimal=False` (columns behave as `float` in Python, `NUMERIC` only at the DB/storage layer) is very likely the lower-risk choice here given how much existing arithmetic code (`balance +=`, `amount - discount`, etc.) assumes `float` semantics throughout this 7700-line file — re-typing all of it to `Decimal`-safe arithmetic is a much larger, riskier change than declaring `db.Numeric(18, 4, asdecimal=False)` everywhere in Step 3 instead. If choosing `asdecimal=False`, update Step 3's column definitions accordingly and note this explicitly in the migration's docstring and this plan.

- [ ] **Step 6: Write the migration**

Create `migrations/versions/<new_revision>_convert_money_columns_to_numeric.py`:

```python
"""convert money columns from Float to Numeric(18,4)

Revision ID: <new>
Revises: <previous task's revision>
Create Date: 2026-08-27

Bundled Float->Numeric fix, folded into the multi-currency accounting work
since both touch the same columns (see
docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
Each column is existence/type-checked before altering (skip-with-NOTE if
already Numeric) per this repo's defensive-migration discipline. On Postgres,
`Float`->`Numeric(18,4)` is a safe direct cast for every value ever stored via
this app's UI (existing data was already bounded by IEEE-754 double range,
comfortably inside Numeric(18,4)) -- confirmed against a real Postgres
instance via docker-compose before this migration was considered done, not
merely asserted. On SQLite (dev), this is a metadata-only no-op: SQLite has
no real NUMERIC type enforcement.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '<new>'
down_revision = '<previous>'
branch_labels = None
depends_on = None

_MONEY_COLUMNS = [
    ('reseller', 'balance'), ('reseller_payment', 'amount'),
    ('upstream_provider', 'balance'), ('upstream_provider_payment', 'amount'),
    ('customer', 'balance'), ('customer', 'discount'), ('customer', 'cost_override'),
    ('subscription_plan', 'price'), ('subscription_plan', 'cost'),
    ('supplier', 'balance'), ('supplier_payment', 'amount'),
    ('expense', 'amount'),
    ('employee', 'monthly_salary'), ('employee', 'balance'),
    ('salary_charge', 'amount'), ('salary_payment', 'amount'),
    ('monthly_profit_estimate', 'estimated_income'), ('monthly_profit_estimate', 'estimated_cost'),
    ('monthly_profit_estimate', 'estimated_profit'),
    ('payment', 'amount'), ('payment', 'collected_amount'),
    ('addon_purchase', 'amount'), ('billing_payment_attempt', 'amount'),
]


def _is_already_numeric(inspector, table, column):
    for col in inspector.get_columns(table):
        if col['name'] == column:
            return isinstance(col['type'], (sa.Numeric, sa.DECIMAL))
    return False  # column not found -- let the alter attempt surface a clear error


def upgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'
    for table, column in _MONEY_COLUMNS:
        inspector = inspect(bind)
        if _is_already_numeric(inspector, table, column):
            print(f"NOTE: {table}.{column} is already Numeric -- skipping.")
            continue
        if is_postgres:
            op.alter_column(
                table, column, type_=sa.Numeric(18, 4),
                postgresql_using=f'"{column}"::numeric(18,4)')
        else:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.alter_column(column, type_=sa.Numeric(18, 4))


def downgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'
    for table, column in _MONEY_COLUMNS:
        if is_postgres:
            op.alter_column(table, column, type_=sa.Float(), postgresql_using=f'"{column}"::double precision')
        else:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.alter_column(column, type_=sa.Float())
```

- [ ] **Step 7: Commit**

```bash
git add app.py migrations/versions/*_convert_money_columns_to_numeric.py tests/test_multi_currency.py
git commit -m "Convert 23 money columns from Float to Numeric(18,4)"
```

---

## Task 7: Cross-currency plan-change guard on `Customer`

**Files:**
- Modify: `app.py` (`update_customer`, subscription-plan-change block ~`app.py:2629`)
- Test: Add to `tests/test_multi_currency.py`

- [ ] **Step 1: Write the failing test**

```python
def test_plan_change_blocked_when_currency_differs_and_balance_nonzero(app, client):
    hdr = make_tenant(client, "Biz GuardBlock", "guardblock_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz GuardBlock").first()
        usd_plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="USD Plan", price=30.0, cost=10.0, billing_cycle="monthly", currency="USD")
        lbp_plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="LBP Plan", price=2700000.0, cost=900000.0, billing_cycle="monthly", currency="LBP")
        appmod.db.session.add_all([usd_plan, lbp_plan])
        appmod.db.session.commit()
        usd_plan_id, lbp_plan_id = usd_plan.id, lbp_plan.id
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust Guard", "phone": "1", "address": "a", "subscription_plan_id": usd_plan_id})
    customer_id = r.get_json()['customer']['id']
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        customer.balance = 15.0
        appmod.db.session.commit()
    r = client.put(f"/api/customers/{customer_id}", headers=hdr, json={"subscription_plan_id": lbp_plan_id})
    assert r.status_code == 400
    with app.app_context():
        assert appmod.db.session.get(appmod.Customer, customer_id).subscription_plan_id == usd_plan_id


def test_plan_change_allowed_across_currencies_when_balance_zero(app, client):
    hdr = make_tenant(client, "Biz GuardOk", "guardok_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz GuardOk").first()
        usd_plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="USD Plan 2", price=30.0, cost=10.0, billing_cycle="monthly", currency="USD")
        lbp_plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="LBP Plan 2", price=2700000.0, cost=900000.0, billing_cycle="monthly", currency="LBP")
        appmod.db.session.add_all([usd_plan, lbp_plan])
        appmod.db.session.commit()
        usd_plan_id, lbp_plan_id = usd_plan.id, lbp_plan.id
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust GuardOk", "phone": "1", "address": "a", "subscription_plan_id": usd_plan_id})
    customer_id = r.get_json()['customer']['id']
    with app.app_context():
        customer = appmod.db.session.get(appmod.Customer, customer_id)
        customer.balance = 0.0
        appmod.db.session.commit()
    r = client.put(f"/api/customers/{customer_id}", headers=hdr, json={"subscription_plan_id": lbp_plan_id})
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_currency.py -k plan_change -v`
Expected: `test_plan_change_blocked_...` FAILs (currently allowed, no guard); `test_plan_change_allowed_...` should already pass.

- [ ] **Step 3: Add the guard**

Modify `update_customer` (~`app.py:2630`):

```python
        # Handle subscription plan change
        if 'subscription_plan_id' in data and data['subscription_plan_id'] != customer.subscription_plan_id:
            new_plan = tenant_query(SubscriptionPlan).filter_by(id=data['subscription_plan_id']).first()
            if not new_plan:
                return jsonify({'message': 'Subscription plan not found!'}), 404

            old_plan = tenant_query(SubscriptionPlan).filter_by(id=customer.subscription_plan_id).first()
            if (old_plan and new_plan.currency != old_plan.currency and float(customer.balance or 0) != 0.0):
                return jsonify({'error': (
                    f"Cannot change this customer's plan from {old_plan.currency} to {new_plan.currency} "
                    f"while they have a non-zero balance ({float(customer.balance):.2f} {old_plan.currency}). "
                    f"Settle their balance to zero first, then change the plan.")}), 400

            old_plan_id = customer.subscription_plan_id
            customer.subscription_plan_id = data['subscription_plan_id']
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: PASS (24 passed)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_multi_currency.py
git commit -m "Block cross-currency plan changes for customers with a non-zero balance"
```

---

## Task 8: Reporting-currency conversion (`/api/reports/financial`)

**Files:**
- Modify: `app.py` (`get_financial_report`, ~`app.py:6496`)
- Test: Add to `tests/test_multi_currency.py`

**Scope note (from the spec):** this task converts the one representative, most-used report (`/api/reports/financial`) fully, end to end, with tests, as the concrete proof the conversion mechanism works correctly (opted-out byte-identical, opted-in correctly converted, per-payment historical rate used not a blended current rate). The remaining `func.sum(Payment.amount)` call sites identified during research (`app.py:3261` `/api/reports/total-sales`, `app.py:3280` `/api/reports/unpaid-payments`, `app.py:4229` monthly-revenue, `app.py:6473` collector-progress) are the **same mechanical change** (`Payment.amount` → `Payment.amount * Payment.fx_rate_to_reporting`, plus a `"currency"` field on the response) — apply it to all of them in this task while the pattern is fresh, and add one focused test per additional route confirming opted-out-tenant totals are unchanged. Do not skip these citing "out of scope" — they are the same one-line change repeated, not new design work, and leaving some report endpoints silently un-converted while others are would be a real correctness bug for any tenant who opts in.

- [ ] **Step 1: Write the failing test**

```python
def test_financial_report_opted_out_tenant_totals_unchanged(app, client):
    hdr = make_tenant(client, "Biz ReportOff", "reportoff_admin")
    with app.app_context():
        plan = appmod.tenant_query(appmod.SubscriptionPlan).first()
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust Report", "phone": "1", "address": "a", "subscription_plan_id": plan.id})
    customer_id = r.get_json()['customer']['id']
    client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 42.0, "reason": "t", "pre_payment": True})
    today = datetime.utcnow().strftime('%Y-%m-%d')
    r = client.get(f"/api/reports/financial?start_date={today}&end_date={today}", headers=hdr)
    body = r.get_json()
    assert body['currency'] == 'USD'
    total_income = sum(m['income'] for m in body['data']) if 'data' in body else None
    assert total_income is None or total_income >= 42.0  # exact shape asserted against the real route in Step 3/4


def test_financial_report_opted_in_tenant_converts_using_locked_rate(app, client):
    hdr = make_tenant(client, "Biz ReportOn", "reporton_admin")
    client.post("/api/business-settings", headers=hdr, data={
        "business_name": "x", "address": "a", "mobile": "1",
        "multi_currency_enabled": "true", "reporting_currency": "USD"})
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz ReportOn").first()
        lbp_plan = appmod.SubscriptionPlan(tenant_id=tenant.id, name="LBP Report Plan", price=1000000.0,
                                            cost=0.0, billing_cycle="monthly", currency="LBP")
        appmod.db.session.add(lbp_plan)
        appmod.db.session.add(appmod.ExchangeRate(
            tenant_id=tenant.id, from_currency="LBP", to_currency="USD", rate=0.00001))
        appmod.db.session.commit()
        plan_id = lbp_plan.id
    r = client.post("/api/customers", headers=hdr, json={
        "name": "Cust ReportLbp", "phone": "1", "address": "a", "subscription_plan_id": plan_id})
    customer_id = r.get_json()['customer']['id']
    client.post("/api/payments", headers=hdr, json={
        "customer_id": customer_id, "amount": 1000000.0, "reason": "t", "pre_payment": True})
    today = datetime.utcnow().strftime('%Y-%m-%d')
    r = client.get(f"/api/reports/financial?start_date={today}&end_date={today}", headers=hdr)
    body = r.get_json()
    assert body['currency'] == 'USD'
    # 1,000,000 LBP * 0.00001 = 10.00 USD -- assert against real response shape in Step 3/4.
```

Note: Step 1's assertions are deliberately loose placeholders on the exact response shape (`get_financial_report`'s current shape wasn't fully read in this plan's research pass) — **before implementing Step 3**, run `python -m pytest tests/ -k financial_report -v` against the *current* (pre-this-task) code first to print the real current response shape from an existing passing test if one exists (`grep -n "financial" tests/*.py`), or add a quick throwaway `print(r.get_json())` in this new test temporarily, and tighten these two new tests' assertions to match the real `data`/`month` keys before moving to Step 2's "verify it fails" run. Remove the throwaway print before committing.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_currency.py -k financial_report -v`
Expected: FAIL — `KeyError: 'currency'` (route doesn't return that field yet).

- [ ] **Step 3: Convert the query and response**

Modify `get_financial_report` (~`app.py:6514`):

```python
        # 1. Income: Payments marked as paid. Fall back to date if paid_at is null.
        # Converted into the tenant's reporting_currency using each payment's own
        # LOCKED rate (fx_rate_to_reporting) -- not a live/current rate -- so a
        # report over a date range spanning an FX-rate update always reflects
        # what those payments were actually worth when they happened. For an
        # opted-out (single-currency) tenant, fx_rate_to_reporting is always
        # exactly 1, so this is a no-op multiply-by-1 (see
        # docs/superpowers/specs/2026-08-27-multi-currency-accounting-design.md).
        income_query = db.session.query(
            month_key(func.coalesce(Payment.paid_at, Payment.date)).label('month'),
            func.sum(Payment.amount * Payment.fx_rate_to_reporting).label('total')
        ).filter(
```

(leave the rest of that query's `.filter(...)` clause and `.group_by('month').all()` unchanged).

Add `settings = get_tenant_settings(BusinessSettings, business_name="Default Business", address="", mobile="")` near the top of the function (if not already fetched), and add `'currency': settings.reporting_currency,` to the function's final `jsonify(...)` payload (locate the return statement with `grep -n "return jsonify" app.py` in the ~6600-6650 range to find the exact dict being built, and add the key without altering existing keys).

- [ ] **Step 4: Tighten and pass the Step-1 tests**

Update the two tests from Step 1 to assert the exact real values now visible from the route (e.g. `body['data'][0]['income'] == 42.0` and `== 10.0` respectively, using whatever the real key names turn out to be), then:

Run: `python -m pytest tests/test_multi_currency.py -v`
Expected: PASS (26 passed)

- [ ] **Step 5: Apply the same mechanical change to the remaining `func.sum(Payment.amount)` call sites**

For each of `app.py:3261` (`/api/reports/total-sales`), `app.py:3280` (`/api/reports/unpaid-payments`), `app.py:4229` (monthly-revenue), `app.py:6473` (`/api/reports/collector-progress`) — re-locate the exact current line with `grep -n "func.sum(Payment.amount)" app.py` (line numbers have shifted since this plan was written) and apply the same `Payment.amount` → `Payment.amount * Payment.fx_rate_to_reporting` change, plus add `"currency": settings.reporting_currency` to each route's response. Add one focused regression test per route to `tests/test_multi_currency.py` asserting an opted-out tenant's total is unchanged from a plain `SUM(amount)` (mirroring Step 1's opted-out test) — do not add full opted-in conversion tests for all four; Task 8's Step 1 tests already prove the conversion arithmetic itself is correct, these are only confirming the same pattern was applied correctly at each additional site.

Run: `python -m pytest tests/test_multi_currency.py -v` — expect all these new tests passing, then run the full suite (Step 6).

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_multi_currency.py
git commit -m "Convert Payment.amount SUMs to reporting-currency using each payment's locked rate"
```

---

## Task 9: Verify the full migration chain against real Postgres (non-optional)

Not a code task — a verification task, required before this plan is considered done, per this repo's documented history of migrations passing on SQLite and failing on Postgres.

- [ ] **Step 1: Bring up Postgres**

```bash
docker compose up -d db
# wait for healthy:
until docker compose exec -T db pg_isready -U servicesbills; do sleep 2; done
```

- [ ] **Step 2: Run the full migration chain from scratch**

```bash
DATABASE_URL=postgresql+psycopg2://servicesbills:localdevpass@localhost:5432/servicesbills \
  JWT_SECRET_KEY=test SECRET_KEY=test flask db upgrade
```
Paste the real output. Expected: completes with no traceback; any `NOTE:` lines are informational only (should not appear on a fresh DB — if any do, investigate why before proceeding, don't assume "the defensive check must be working as intended").

- [ ] **Step 3: Inspect real schema**

```bash
docker compose exec -T db psql -U servicesbills -c '\d payment'
docker compose exec -T db psql -U servicesbills -c '\d subscription_plan'
docker compose exec -T db psql -U servicesbills -c '\d business_settings'
docker compose exec -T db psql -U servicesbills -c '\d exchange_rate'
docker compose exec -T db psql -U servicesbills -c '\d currency'
docker compose exec -T db psql -U servicesbills -c 'SELECT * FROM currency;'
```
Confirm: `payment.currency`/`fx_rate_to_reporting`, `subscription_plan.currency`, `business_settings.multi_currency_enabled`/`reporting_currency` all present with the right types (`numeric(18,4)` for money columns, `numeric(18,8)` for the rate columns); `currency` table has exactly `USD`/`LBP` rows.

- [ ] **Step 4: Downgrade/upgrade round-trip**

```bash
DATABASE_URL=postgresql+psycopg2://servicesbills:localdevpass@localhost:5432/servicesbills \
  JWT_SECRET_KEY=test SECRET_KEY=test flask db downgrade -5
DATABASE_URL=postgresql+psycopg2://servicesbills:localdevpass@localhost:5432/servicesbills \
  JWT_SECRET_KEY=test SECRET_KEY=test flask db upgrade
```
(`-5` covers this plan's 5 new migrations — Tasks 1/2/3/4/6; adjust the count to match how many migrations actually landed, e.g. if Task 6's Numeric-conversion migration was combined with another.) Confirm both directions complete cleanly with no error.

- [ ] **Step 5: Insert-and-read round-trip for `Numeric` values**

```bash
docker compose exec -T db psql -U servicesbills -c "
INSERT INTO currency (code, name, decimal_places, active) VALUES ('EUR','Euro',2,true) ON CONFLICT DO NOTHING;
"
```
Then, with a Python shell against this same `DATABASE_URL`, create a tenant/plan/payment with a fractional `Numeric` amount (e.g. `123.4567`) and read it back, confirming no precision is silently lost (Postgres `NUMERIC` is exact; this is a sanity check that the ORM round-trip through `Numeric(18,4)` behaves as expected end to end, not just at the raw SQL layer).

- [ ] **Step 6: Tear down**

```bash
docker compose down
```
(Leave `pgdata` volume in place or remove it — either is fine; this was a scratch verification run, not a persistent environment.)

- [ ] **Step 7: Document the result**

Paste the real output of Steps 2-5 into the PR description under "Postgres verification" — this is the evidence the PR must show, not a claim.

---

## Task 10: Full suite, push, open PR

- [ ] **Step 1: Full local suite one more time**

```bash
python -m pytest -q
```
Expected: all green. Paste the real summary line.

- [ ] **Step 2: Push**

```bash
git push -u origin phase4b-multi-currency-accounting
```

- [ ] **Step 3: Open PR against `main`**, description covering:
  - Summary of what was built (data model, opt-in mechanism, FX locking, reporting conversion, Float→Numeric).
  - Every judgment call from the spec's "Open decisions" and the spec's own "Open questions for the business owner" section, each with its reasoning, called out explicitly for human review.
  - The `Collector.balance` correction (no such model exists — documented in the spec).
  - The `asdecimal=False` vs `Decimal`-everywhere decision made in Task 6, whichever way it went, and why.
  - Explicit confirmation the migration was verified against real Postgres, with a summary of Task 9's steps and output.
  - Explicit statement: **not merged, not deployed, needs human review** — especially the currency/precision choices, since this is real financial data. Do not merge to `main`. Do not push to `main`.

---

## Self-review pass (performed before treating this plan as final)

- **Spec coverage**: every data-model element in the spec (Currency, ExchangeRate, BusinessSettings.multi_currency_enabled/reporting_currency, SubscriptionPlan.currency, Payment.currency/fx_rate_to_reporting, the 23-column Float→Numeric list, the FX lookup semantics, the cross-currency plan-change guard, reporting conversion) has a corresponding task. The spec's deliberately-deferred frontend UI has no task here, matching the spec's explicit non-goal.
- **Type/name consistency across tasks**: `fx.get_rate`'s signature (`tenant_id, from_code, to_code, as_of=None`) is used identically in Task 4 (payment creation) and referenced identically in the spec. `Currency.code`/`ExchangeRate.from_currency`/`to_currency`/`SubscriptionPlan.currency`/`Payment.currency`/`BusinessSettings.reporting_currency` are all `db.String(3)` consistently. `ExchangeRate.rate` and `Payment.fx_rate_to_reporting` are both `Numeric(18, 8)` consistently (distinct from the `Numeric(18, 4)` used for money-amount columns) throughout every task that touches them.
- **Ordering dependency**: Task 4 (Payment columns) depends on Task 2 (BusinessSettings.reporting_currency) and Task 3 (SubscriptionPlan.currency) both existing first — task order above reflects this (2, 3 before 4). Task 8 (reporting) depends on Task 4's `fx_rate_to_reporting` existing. Task 7 (plan-change guard) depends on Task 3. Task 6 (Numeric conversion) is independent of Tasks 2-5's new columns but is sequenced after them so its full-suite regression check (Step 5) also exercises the new currency-aware code paths added in Tasks 2-5, not just pre-existing ones.
- **Ambiguity flagged rather than silently resolved**: Task 6 Step 5 explicitly calls out the `asdecimal=False` decision as one to make and document during implementation rather than pre-deciding it here with unverified confidence about every downstream arithmetic call site in a 7700-line file; Task 8 Step 1 explicitly flags that the exact JSON response shape of `get_financial_report` needs confirming against real (not assumed) output before the test assertions are finalized, rather than guessing at field names in this plan.
- **Scope discipline**: Task 8 explicitly bounds itself to `/api/reports/financial` plus a mechanical repeat across the other four identified `func.sum(Payment.amount)` sites, matching the spec's stated reporting scope (aggregate SUMs only, not every line-item display) — not silently expanding into every money-displaying endpoint in the app, and not silently leaving some report endpoints inconsistently un-converted either.
