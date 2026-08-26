# Self-Serve Pro Plan via Whish Payment Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a tenant self-serve upgrade to Pro ($120/mo or $1000/yr) by paying through Whish (Lebanon's working payment gateway — Stripe is not viable for this market and stays dormant, untouched), with manual (not auto-charged) renewal, an email + in-app reminder before expiry, and a 3-day grace period before reverting to Free.

**Architecture:** A new parallel module `whish_billing.py` (sibling to the existing, now-dormant `billing.py`) holds the Whish HTTP client. A new `BillingPaymentAttempt` table tracks each checkout attempt (Whish has no subscription object to query later, unlike Stripe) via a single-use UUID token that Whish echoes back on its browser-redirect callback. `Tenant` gains `plan_expires_at`/`plan_expiry_reminder_sent_at`. A new daily scheduler job (registered using this codebase's established `_with_context` pattern, defined *before* `scheduler.add_job()` per the hard lesson from today's production crash-loop) sends the reminder and reverts lapsed plans after the grace period.

**Tech Stack:** Flask + SQLAlchemy + Alembic (backend), React + MUI (frontend), `requests` for the Whish HTTP calls, pytest with `monkeypatch` for mocking the external Whish API (no real credentials exist yet).

## Global Constraints

- Spec: [docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md](../specs/2026-08-26-whish-self-serve-billing-design.md) — read it first for the full Whish API contract and rationale.
- Stripe's code (`billing.py`, `/api/billing/checkout`, `/api/billing/portal`, `/api/stripe/webhook`) is **never modified or removed** — it stays dormant.
- Pricing: **$120.00/month, $1000.00/year, USD only.**
- Money columns use `db.Float`, matching this codebase's existing convention everywhere else (not pre-empting the separately-scoped Float→Numeric migration).
- **Hard requirement, non-negotiable given today's incident:** any new function referenced by `scheduler.add_job(func=...)` must be *defined* earlier in `app.py` than the `if os.environ.get("RUN_SCHEDULER", "1") == "1"` block that registers it (currently ~line 1975). Verify this explicitly by running `import app` with `RUN_SCHEDULER=1` set — every other test in this repo runs with `RUN_SCHEDULER=0` (see `tests/conftest.py`) and will NOT catch a misordered function.
- Follow this repo's defensive-migration pattern (existence-check via `inspect(bind)` before `ADD`/`DROP`) given its documented production-schema-drift history — see any recent migration under `migrations/versions/` for the pattern (e.g. `aa91943943d4_add_business_settings_upstream_sync_.py`).
- This repo has no frontend automated test suite — frontend tasks are verified by manual browser check (matching how the Settings toggle was verified earlier today), not new test files.
- Deploy workflow for this repo (do not skip steps): implement on a branch → run tests locally → user opens a PR → CI + a cloud Postgres dry-run verify it → user says "merge it locally" → merge without pushing → re-verify → user says "push it to production" → push to `origin/main` (Render auto-deploys) → confirm via `/api/health`. This plan's tasks stop at "implemented and locally tested, ready for a branch/PR" — the deploy steps happen after, per the user's explicit direction each time.

---

## Task 1: Data model — `Tenant` columns, `BillingPaymentAttempt`, migration

**Files:**
- Modify: `app.py:156-169` (`Tenant` class), `app.py:884-895` area (add new model near `UpgradeRequest`)
- Create: `migrations/versions/<new_revision>_add_whish_billing_fields.py`
- Test: Create `tests/test_whish_billing.py`

**Interfaces:**
- Produces: `Tenant.plan_expires_at` (nullable `DateTime`), `Tenant.plan_expiry_reminder_sent_at` (nullable `DateTime`), `Tenant.to_dict()` includes `"plan_expires_at"` (string `"%Y-%m-%d %H:%M:%S"` or `None`).
- Produces: `BillingPaymentAttempt` model with columns `id`, `tenant_id` (FK), `billing_cycle` (`String(10)`), `amount` (`Float`), `currency` (`String(3)`), `whish_external_id` (`String(64)`, unique, indexed), `callback_token` (`String(64)`), `status` (`String(10)`, default `'pending'`), `created_at` (`DateTime`), `completed_at` (`DateTime`, nullable).

- [ ] **Step 1: Write the failing test**

Create `tests/test_whish_billing.py`:

```python
"""Self-serve Pro plan via Whish (Lebanon payment gateway) -- see
docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md.
Stripe stays fully dormant; these tests never touch billing.py."""
import app as appmod
from tests.conftest import make_tenant


def test_tenant_has_plan_expiry_fields(app, client):
    make_tenant(client, "Biz Expiry", "expiry_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Expiry").first()
        assert tenant.plan_expires_at is None
        assert tenant.plan_expiry_reminder_sent_at is None
        d = tenant.to_dict()
        assert "plan_expires_at" in d and d["plan_expires_at"] is None


def test_billing_payment_attempt_model_roundtrip(app, client):
    make_tenant(client, "Biz Attempt", "attempt_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Attempt").first()
        attempt = appmod.BillingPaymentAttempt(
            tenant_id=tenant.id, billing_cycle="monthly", amount=120.0,
            currency="USD", whish_external_id="ext-1", callback_token="tok-1",
        )
        appmod.db.session.add(attempt)
        appmod.db.session.commit()
        fetched = appmod.BillingPaymentAttempt.query.filter_by(whish_external_id="ext-1").first()
        assert fetched is not None
        assert fetched.status == "pending"
        assert fetched.tenant_id == tenant.id
        assert fetched.completed_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whish_billing.py -v`
Expected: FAIL — `AttributeError: type object 'Tenant' has no attribute 'plan_expires_at'` (or `BillingPaymentAttempt` not defined).

- [ ] **Step 3: Add the columns and model**

In `app.py`, modify the `Tenant` class (currently lines 156-169):

```python
class Tenant(db.Model):
    __tablename__ = "tenant"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active")  # active, suspended
    plan = db.Column(db.String(20), nullable=False, default="free")      # free, pro, ...
    stripe_customer_id = db.Column(db.String(120), nullable=True)
    stripe_subscription_id = db.Column(db.String(120), nullable=True)
    # Self-serve Pro via Whish (see docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md):
    # null for Free; the paid-through timestamp for Pro. Whish has no
    # recurring-billing concept, so this is advanced manually on each
    # successful checkout, not by any webhook/subscription-lifecycle event.
    plan_expires_at = db.Column(db.DateTime, nullable=True)
    # Set once the pre-expiry reminder email has gone out, so the daily
    # scheduler job sends it once per expiry cycle, not once per tick.
    plan_expiry_reminder_sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "slug": self.slug,
                "status": self.status, "plan": self.plan,
                "plan_expires_at": self.plan_expires_at.strftime('%Y-%m-%d %H:%M:%S') if self.plan_expires_at else None}
```

Add a new model near `UpgradeRequest` (currently `app.py:884-895`), directly after that class's closing line:

```python
class BillingPaymentAttempt(db.Model):
    """One Whish checkout attempt for a Pro-plan payment. Whish's API has no
    subscription/order object to query later -- this table is our own record
    of what a payment was for, looked up by whish_external_id when Whish's
    success/failure callback lands. See
    docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    billing_cycle = db.Column(db.String(10), nullable=False)  # 'monthly' or 'yearly'
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default='USD')
    whish_external_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    callback_token = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(10), nullable=False, default='pending')  # pending, succeeded, failed, expired
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whish_billing.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Write the migration**

Check the current migration head first:

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

Expected output: `HEAD: {'aa91943943d4'}` (if a different migration landed since, use that instead — this is why the check is a command, not a hardcoded assumption).

Create `migrations/versions/<new_revision>_add_whish_billing_fields.py` (generate a revision id the same way Alembic normally does — any unique 12-char lowercase-hex string not already used under `migrations/versions/`; the examples below use `b4e91f7a3c02` as a placeholder — replace it with a freshly generated one):

```python
"""add whish billing fields

Revision ID: b4e91f7a3c02
Revises: aa91943943d4
Create Date: 2026-08-26

Self-serve Pro plan via Whish (see
docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md).
Additive-only: two new nullable Tenant columns (a genuine no-op for every
existing tenant until they actually check out) and one new table. Follows
this repo's established defensive-migration discipline (see
c57bc44a51d0's docstring for why): checks for existing columns/tables
first, skips with a NOTE rather than crashing if already present, given
the documented history of this project's migrations disagreeing with the
real production schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'b4e91f7a3c02'
down_revision = 'aa91943943d4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    tenant_columns = {c['name'] for c in inspector.get_columns('tenant')}
    with op.batch_alter_table('tenant', schema=None) as batch_op:
        if 'plan_expires_at' not in tenant_columns:
            batch_op.add_column(sa.Column('plan_expires_at', sa.DateTime(), nullable=True))
        else:
            print("NOTE: tenant.plan_expires_at already exists -- skipping add (nothing to do).")
        if 'plan_expiry_reminder_sent_at' not in tenant_columns:
            batch_op.add_column(sa.Column('plan_expiry_reminder_sent_at', sa.DateTime(), nullable=True))
        else:
            print("NOTE: tenant.plan_expiry_reminder_sent_at already exists -- skipping add (nothing to do).")

    existing_tables = set(inspector.get_table_names())
    if 'billing_payment_attempt' in existing_tables:
        print("NOTE: billing_payment_attempt table already exists -- skipping create (nothing to do).")
        return
    op.create_table(
        'billing_payment_attempt',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenant.id'), nullable=False),
        sa.Column('billing_cycle', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
        sa.Column('whish_external_id', sa.String(length=64), nullable=False, unique=True),
        sa.Column('callback_token', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_billing_payment_attempt_tenant_id', 'billing_payment_attempt', ['tenant_id'])
    op.create_index('ix_billing_payment_attempt_whish_external_id', 'billing_payment_attempt', ['whish_external_id'], unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'billing_payment_attempt' in set(inspector.get_table_names()):
        op.drop_table('billing_payment_attempt')
    tenant_columns = {c['name'] for c in inspector.get_columns('tenant')}
    with op.batch_alter_table('tenant', schema=None) as batch_op:
        if 'plan_expiry_reminder_sent_at' in tenant_columns:
            batch_op.drop_column('plan_expiry_reminder_sent_at')
        if 'plan_expires_at' in tenant_columns:
            batch_op.drop_column('plan_expires_at')
```

- [ ] **Step 6: Commit**

```bash
git checkout -b phase4-whish-self-serve-billing
git add app.py migrations/versions/*_add_whish_billing_fields.py tests/test_whish_billing.py
git commit -m "Add Tenant plan-expiry fields and BillingPaymentAttempt model for Whish billing"
```

---

## Task 2: Pricing constants in `plans.py`

**Files:**
- Modify: `plans.py`
- Test: Add to `tests/test_whish_billing.py`

**Interfaces:**
- Produces: `plans.PLANS['pro']['whish_price_monthly']` (`120.0`), `plans.PLANS['pro']['whish_price_yearly']` (`1000.0`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_whish_billing.py`:

```python
import plans as plansmod


def test_pro_plan_has_whish_prices():
    assert plansmod.PLANS['pro']['whish_price_monthly'] == 120.0
    assert plansmod.PLANS['pro']['whish_price_yearly'] == 1000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whish_billing.py::test_pro_plan_has_whish_prices -v`
Expected: FAIL — `KeyError: 'whish_price_monthly'`

- [ ] **Step 3: Add the constants**

Modify `plans.py`:

```python
"""servicesBills subscription plan catalog.

Single source of truth mapping each plan to its Stripe Price ID (dormant --
see docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md for
why Stripe is not used) and its Whish self-serve prices, plus enforced
limits. max_customers=None means unlimited; whatsapp_api gates Meta Cloud
API mode.
"""
import os

PLANS = {
    "free": {
        "stripe_price": None,
        "whish_price_monthly": None,
        "whish_price_yearly": None,
        "max_customers": 50,
        "whatsapp_api": False,
    },
    "pro": {
        "stripe_price": os.environ.get("STRIPE_PRICE_PRO"),
        "whish_price_monthly": 120.0,
        "whish_price_yearly": 1000.0,
        "max_customers": None,
        "whatsapp_api": True,
    },
}

DEFAULT_PLAN = "free"


def limits(plan_name):
    """Return the limits dict for a plan, falling back to free."""
    return PLANS.get(plan_name, PLANS[DEFAULT_PLAN])


def plan_for_price(price_id):
    """Map a Stripe Price ID back to a plan name (free if unknown/None)."""
    if not price_id:
        return DEFAULT_PLAN
    for name, p in PLANS.items():
        if p["stripe_price"] and p["stripe_price"] == price_id:
            return name
    return DEFAULT_PLAN


def whish_price(plan_name, cycle):
    """Return the Whish price for plan_name/cycle, or None if not purchasable."""
    key = f"whish_price_{cycle}"
    return PLANS.get(plan_name, {}).get(key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whish_billing.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add plans.py tests/test_whish_billing.py
git commit -m "Add Whish monthly/yearly pricing constants to the plan catalog"
```

---

## Task 3: `whish_billing.py` — the Whish API client

**Files:**
- Create: `whish_billing.py`
- Test: Add to `tests/test_whish_billing.py`

**Interfaces:**
- Consumes: `Config.WHISH_CHANNEL`, `Config.WHISH_SECRET`, `Config.APP_BASE_URL` (from `config.py`, added in this task).
- Produces: `whish_billing.create_payment(external_id, amount, currency, callback_token, requestee, target, email, invoice) -> str` (returns the `collectUrl`), raising `whish_billing.WhishAPIError` on any failure. `whish_billing.WHISH_CREATE_URL` (str constant, so tests can assert what was called).

- [ ] **Step 1: Add config**

Modify `config.py`, right after the `STRIPE_WEBHOOK_SECRET` line:

```python
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
    # Whish (Lebanon payment gateway) -- see
    # docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md.
    # Not yet issued as of 2026-08-26; whish_enabled in /api/billing/config
    # stays False (hiding the self-serve button) until both are set.
    WHISH_CHANNEL = os.environ.get("WHISH_CHANNEL")
    WHISH_SECRET = os.environ.get("WHISH_SECRET")
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_whish_billing.py`:

```python
import pytest
import whish_billing


class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json = json_body
        self.status_code = status_code

    def json(self):
        return self._json


def test_create_payment_returns_collect_url_on_success(monkeypatch):
    monkeypatch.setattr(whish_billing.Config, "WHISH_CHANNEL", "chan1")
    monkeypatch.setattr(whish_billing.Config, "WHISH_SECRET", "sec1")

    captured = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"status": True, "data": {"collectUrl": "https://whish.money/pay/abc"}})
    monkeypatch.setattr(whish_billing.requests, "post", fake_post)

    url = whish_billing.create_payment(
        external_id="ext-1", amount=120.0, currency="USD", callback_token="tok-1",
        requestee="Biz Name", target="+96170000000", email="biz@example.com",
        invoice="ServiceBills Pro subscription",
    )
    assert url == "https://whish.money/pay/abc"
    assert captured["url"] == whish_billing.WHISH_CREATE_URL
    assert captured["headers"]["channel"] == "chan1"
    assert captured["headers"]["secret"] == "sec1"
    assert captured["json"]["externalId"] == "ext-1"
    assert captured["json"]["amount"] == 120.0
    assert captured["json"]["currency"] == "USD"
    assert "token=tok-1" in captured["json"]["successCallbackUrl"]


def test_create_payment_raises_on_failure_status(monkeypatch):
    monkeypatch.setattr(whish_billing.Config, "WHISH_CHANNEL", "chan1")
    monkeypatch.setattr(whish_billing.Config, "WHISH_SECRET", "sec1")
    monkeypatch.setattr(
        whish_billing.requests, "post",
        lambda *a, **k: _FakeResponse({"status": False, "code": "currency.not_supported"}),
    )
    with pytest.raises(whish_billing.WhishAPIError):
        whish_billing.create_payment(
            external_id="ext-2", amount=120.0, currency="USD", callback_token="tok-2",
            requestee="Biz", target="+961700", email="a@b.com", invoice="inv",
        )


def test_create_payment_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(whish_billing.Config, "WHISH_CHANNEL", "chan1")
    monkeypatch.setattr(whish_billing.Config, "WHISH_SECRET", "sec1")
    def raise_post(*a, **k):
        raise whish_billing.requests.exceptions.ConnectionError("timeout")
    monkeypatch.setattr(whish_billing.requests, "post", raise_post)
    with pytest.raises(whish_billing.WhishAPIError):
        whish_billing.create_payment(
            external_id="ext-3", amount=120.0, currency="USD", callback_token="tok-3",
            requestee="Biz", target="+961700", email="a@b.com", invoice="inv",
        )
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_whish_billing.py -k create_payment -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'whish_billing'`

- [ ] **Step 4: Write the module**

Create `whish_billing.py`:

```python
"""Whish payment gateway client (Lebanon self-serve Pro-plan billing).

Reverse-engineered from Whish's own official WooCommerce gateway plugin
(no public API docs were available) -- see
docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md for
the full request/response contract and the security tradeoffs of Whish's
browser-redirect-plus-shared-token callback model (weaker than a signed
webhook, but it's Whish's own blessed integration pattern).

One-time-payment only: Whish has no subscription/recurring-billing concept,
unlike Stripe. Renewal is always a fresh call to create_payment(), triggered
either by the tenant or by the reminder-driven "Renew" button.
"""
import urllib.parse
import requests
from config import Config

WHISH_VERIFY_URL = "https://whish.money/itel-service/api/payment/account/balance"
WHISH_CREATE_URL = "https://whish.money/itel-service/api/payment/whish"
# Copied from the reference WooCommerce plugin's own constant. Whish's backend
# may or may not validate this strictly -- unconfirmed until a real sandbox
# test is possible (no credentials issued yet, see the design spec).
_INTEGRATION_VERSION = "1000"


class WhishAPIError(Exception):
    """Raised for any failure creating a Whish payment -- a non-2xx response,
    a network error, or a well-formed response with status=false."""


def _headers():
    site_netloc = urllib.parse.urlparse(Config.APP_BASE_URL).netloc or Config.APP_BASE_URL
    return {
        "Content-Type": "application/json",
        "channel": Config.WHISH_CHANNEL or "",
        "secret": Config.WHISH_SECRET or "",
        "pluginversion": _INTEGRATION_VERSION,
        "websiteurl": site_netloc,
    }


def create_payment(external_id, amount, currency, callback_token, requestee, target, email, invoice):
    """Create a one-time Whish payment and return the collectUrl to redirect
    the customer's browser to. Raises WhishAPIError on any failure -- never
    returns a falsy/partial result, matching billing.py's raise-based
    convention for the Stripe client this sits alongside."""
    success_url = f"{Config.APP_BASE_URL}/api/billing/whish/success?order={external_id}&token={callback_token}"
    failure_url = f"{Config.APP_BASE_URL}/api/billing/whish/failure?order={external_id}&token={callback_token}"
    payload = {
        "externalId": external_id,
        "successCallbackUrl": success_url,
        "failureCallbackUrl": failure_url,
        # Whish's API expects separate "thank you page" redirect URLs distinct
        # from the callback URLs above (see the reference plugin). This app has
        # no separate thank-you page -- point both at the Billing page directly,
        # since the callback routes above already 302 there once processed.
        "successRedirectUrl": f"{Config.APP_BASE_URL}/billing?status=success",
        "failureRedirectUrl": f"{Config.APP_BASE_URL}/billing?status=failed",
        "amount": amount,
        "invoice": invoice,
        "currency": currency,
        "requestee": requestee,
        "target": target,
        "email": email,
    }
    try:
        resp = requests.post(WHISH_CREATE_URL, json=payload, headers=_headers(), timeout=15)
    except requests.exceptions.RequestException as e:
        raise WhishAPIError(f"Whish request failed: {e}") from e

    try:
        body = resp.json()
    except ValueError as e:
        raise WhishAPIError(f"Whish returned a non-JSON response (status {resp.status_code})") from e

    if not body.get("status"):
        raise WhishAPIError(f"Whish payment creation failed: {body.get('code', 'unknown error')}")

    collect_url = (body.get("data") or {}).get("collectUrl")
    if not collect_url:
        raise WhishAPIError("Whish response missing data.collectUrl")
    return collect_url
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_whish_billing.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: Commit**

```bash
git add whish_billing.py config.py tests/test_whish_billing.py
git commit -m "Add Whish payment gateway client module"
```

---

## Task 4: Checkout route

**Files:**
- Modify: `app.py` (add route near the existing billing routes, `app.py:1193-1228` area)
- Test: Add to `tests/test_whish_billing.py`

**Interfaces:**
- Consumes: `whish_billing.create_payment(...)` (Task 3), `plans.whish_price(plan_name, cycle)` (Task 2), `BillingPaymentAttempt` (Task 1), `tenancy.current_tenant()`/`current_tenant_id()`, `tenancy.new_for_tenant()`.
- Produces: `POST /api/billing/whish/checkout` — JWT + admin required. Body `{"cycle": "monthly"|"yearly"}`. Returns `{"redirect": "<collectUrl>"}` (200) or `{"msg": ...}` (400 for a bad cycle, 502 if Whish itself fails).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_whish_billing.py`:

```python
def test_whish_checkout_rejects_bad_cycle(app, client):
    hdr = make_tenant(client, "Biz Checkout", "checkout_admin")
    r = client.post("/api/billing/whish/checkout", headers=hdr, json={"cycle": "weekly"})
    assert r.status_code == 400


def test_whish_checkout_creates_attempt_and_returns_redirect(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Checkout2", "checkout2_admin")

    def fake_create_payment(external_id, amount, currency, callback_token, requestee, target, email, invoice):
        # Exact-signature fake (not **kwargs) so a keyword-wiring bug in the
        # route (a typo'd or missing argument name) fails this test with a
        # TypeError, matching test_billing.py's test_checkout_route convention.
        assert amount == 120.0 and currency == 'USD'
        return "https://whish.money/pay/xyz"
    monkeypatch.setattr(appmod.whish_billing, "create_payment", fake_create_payment)
    r = client.post("/api/billing/whish/checkout", headers=hdr, json={"cycle": "monthly"})
    assert r.status_code == 200
    assert r.get_json()["redirect"] == "https://whish.money/pay/xyz"
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Checkout2").first()
        attempt = appmod.BillingPaymentAttempt.query.filter_by(tenant_id=tenant.id).first()
        assert attempt is not None
        assert attempt.billing_cycle == "monthly"
        assert attempt.amount == 120.0
        assert attempt.status == "pending"


def test_whish_checkout_returns_502_when_whish_fails(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz Checkout3", "checkout3_admin")
    def raise_error(external_id, amount, currency, callback_token, requestee, target, email, invoice):
        raise appmod.whish_billing.WhishAPIError("boom")
    monkeypatch.setattr(appmod.whish_billing, "create_payment", raise_error)
    r = client.post("/api/billing/whish/checkout", headers=hdr, json={"cycle": "yearly"})
    assert r.status_code == 502
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whish_billing.py -k whish_checkout -v`
Expected: FAIL — 404 (route doesn't exist yet)

- [ ] **Step 3: Add the route**

Modify `app.py` — add `import whish_billing` and `import uuid` near the top imports (check `uuid` isn't already imported; if it is, skip re-adding), then add the route right after the existing `billing_portal` route (`app.py:1215-1227`):

```python
import whish_billing


@app.route('/api/billing/whish/checkout', methods=['POST'])
@jwt_required()
@admin_required()
def billing_whish_checkout():
    data = request.json or {}
    cycle = data.get('cycle')
    if cycle not in ('monthly', 'yearly'):
        return jsonify({"msg": "cycle must be 'monthly' or 'yearly'"}), 400

    amount = plans.whish_price('pro', cycle)
    if amount is None:
        return jsonify({"msg": "Pro plan is not purchasable via Whish"}), 400

    tenant = current_tenant()
    settings = BusinessSettings.query.filter_by(tenant_id=tenant.id).first()

    external_id = uuid.uuid4().hex
    callback_token = uuid.uuid4().hex
    attempt = new_for_tenant(
        BillingPaymentAttempt,
        billing_cycle=cycle, amount=amount, currency='USD',
        whish_external_id=external_id, callback_token=callback_token, status='pending',
    )
    db.session.add(attempt)
    db.session.commit()

    try:
        redirect_url = whish_billing.create_payment(
            external_id=external_id, amount=amount, currency='USD', callback_token=callback_token,
            requestee=(settings.business_name if settings else tenant.name),
            target=(settings.mobile if settings else ''),
            email=(settings.email if settings else ''),
            invoice='ServiceBills Pro subscription',
        )
    except whish_billing.WhishAPIError as e:
        logging.error(f"Whish checkout failed for tenant {tenant.id}: {e}")
        attempt.status = 'failed'
        db.session.commit()
        return jsonify({"msg": "Could not start Whish checkout"}), 502

    return jsonify({"redirect": redirect_url}), 200
```

Check whether `import uuid` already exists near the top of `app.py` (`grep -n "^import uuid" app.py`) — if absent, add it alongside the other standard-library imports at the top of the file rather than inline.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whish_billing.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_whish_billing.py
git commit -m "Add POST /api/billing/whish/checkout route"
```

---

## Task 5: Success/failure callback routes

**Files:**
- Modify: `app.py` (add routes near the checkout route)
- Test: Add to `tests/test_whish_billing.py`

**Interfaces:**
- Produces: `GET /api/billing/whish/success?order=<id>&token=<token>` and `GET /api/billing/whish/failure?order=<id>&token=<token>` — both public (no JWT, matching how Whish's own redirect hits them with no session). Both return a 302 redirect to `{APP_BASE_URL}/billing?status=success|failed|error`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_whish_billing.py`:

```python
from datetime import datetime, timedelta


def _make_pending_attempt(app, tenant_name, cycle="monthly", amount=120.0, created_at=None):
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name=tenant_name).first()
        attempt = appmod.BillingPaymentAttempt(
            tenant_id=tenant.id, billing_cycle=cycle, amount=amount, currency="USD",
            whish_external_id=f"ext-{tenant.id}", callback_token="valid-token",
            status="pending", created_at=created_at or datetime.utcnow(),
        )
        appmod.db.session.add(attempt)
        appmod.db.session.commit()
        return tenant.id, attempt.whish_external_id


def test_whish_success_callback_upgrades_tenant_to_pro(app, client):
    make_tenant(client, "Biz Success", "success_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Success")

    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token", follow_redirects=False)
    assert r.status_code == 302
    assert "status=success" in r.headers["Location"]

    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan == "pro"
        assert tenant.plan_expires_at is not None
        assert tenant.plan_expires_at > datetime.utcnow() + timedelta(days=25)
        attempt = appmod.BillingPaymentAttempt.query.filter_by(whish_external_id=ext_id).first()
        assert attempt.status == "succeeded"
        assert attempt.completed_at is not None


def test_whish_success_callback_yearly_extends_by_a_year(app, client):
    make_tenant(client, "Biz Yearly", "yearly_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Yearly", cycle="yearly", amount=1000.0)
    client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token")
    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan_expires_at > datetime.utcnow() + timedelta(days=360)


def test_whish_success_callback_wrong_token_rejected(app, client):
    make_tenant(client, "Biz Wrong", "wrong_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Wrong")
    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=not-the-right-token", follow_redirects=False)
    assert r.status_code == 302
    assert "status=error" in r.headers["Location"]
    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan == "free"
        attempt = appmod.BillingPaymentAttempt.query.filter_by(whish_external_id=ext_id).first()
        assert attempt.status == "pending"  # untouched


def test_whish_success_callback_is_single_use(app, client):
    make_tenant(client, "Biz Replay", "replay_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Replay")
    client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token")
    with app.app_context():
        first_expiry = appmod.db.session.get(appmod.Tenant, tenant_id).plan_expires_at

    # Replay the exact same callback -- must not extend the expiry a second time.
    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token", follow_redirects=False)
    assert "status=error" in r.headers["Location"]
    with app.app_context():
        second_expiry = appmod.db.session.get(appmod.Tenant, tenant_id).plan_expires_at
        assert second_expiry == first_expiry


def test_whish_success_callback_rejects_expired_attempt(app, client):
    make_tenant(client, "Biz Stale", "stale_billing_admin")
    tenant_id, ext_id = _make_pending_attempt(
        app, "Biz Stale", created_at=datetime.utcnow() - timedelta(hours=25))
    r = client.get(f"/api/billing/whish/success?order={ext_id}&token=valid-token", follow_redirects=False)
    assert "status=error" in r.headers["Location"]
    with app.app_context():
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan == "free"


def test_whish_failure_callback_marks_attempt_failed(app, client):
    make_tenant(client, "Biz Failure", "failure_admin")
    tenant_id, ext_id = _make_pending_attempt(app, "Biz Failure")
    r = client.get(f"/api/billing/whish/failure?order={ext_id}&token=valid-token", follow_redirects=False)
    assert "status=failed" in r.headers["Location"]
    with app.app_context():
        attempt = appmod.BillingPaymentAttempt.query.filter_by(whish_external_id=ext_id).first()
        assert attempt.status == "failed"
        tenant = appmod.db.session.get(appmod.Tenant, tenant_id)
        assert tenant.plan == "free"


def test_whish_success_callback_unknown_order_is_safe(client):
    r = client.get("/api/billing/whish/success?order=does-not-exist&token=x", follow_redirects=False)
    assert r.status_code == 302
    assert "status=error" in r.headers["Location"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whish_billing.py -k "success_callback or failure_callback" -v`
Expected: FAIL — 404 (routes don't exist yet)

- [ ] **Step 3: Add the routes**

Modify `app.py` — add right after the `billing_whish_checkout` route from Task 4:

```python
_WHISH_ATTEMPT_MAX_AGE = timedelta(hours=24)


def _apply_whish_payment_success(attempt):
    """Advance the paying tenant's plan/expiry. Extends from the current
    plan_expires_at if it's still in the future (an early renewal), else
    from now() -- matches the design spec's renewal semantics."""
    tenant = db.session.get(Tenant, attempt.tenant_id)
    now = datetime.utcnow()
    base = tenant.plan_expires_at if (tenant.plan_expires_at and tenant.plan_expires_at > now) else now
    delta = relativedelta(years=1) if attempt.billing_cycle == 'yearly' else relativedelta(months=1)
    tenant.plan = 'pro'
    tenant.plan_expires_at = base + delta
    tenant.plan_expiry_reminder_sent_at = None
    attempt.status = 'succeeded'
    attempt.completed_at = now
    db.session.commit()


@app.route('/api/billing/whish/success', methods=['GET'])
def billing_whish_success():
    # Public: Whish redirects the customer's browser here after payment (see
    # the design spec for why this is a token-match model, not a signed
    # webhook). Never raises on a bad/missing/replayed order+token -- always
    # redirects somewhere sane.
    external_id = request.args.get('order')
    token = request.args.get('token')
    attempt = BillingPaymentAttempt.query.filter_by(whish_external_id=external_id).first()

    if (not attempt or attempt.status != 'pending' or attempt.callback_token != token
            or attempt.created_at < datetime.utcnow() - _WHISH_ATTEMPT_MAX_AGE):
        logging.warning(f"Whish success callback rejected: order={external_id}")
        return redirect(f"{Config.APP_BASE_URL}/billing?status=error")

    _apply_whish_payment_success(attempt)
    return redirect(f"{Config.APP_BASE_URL}/billing?status=success")


@app.route('/api/billing/whish/failure', methods=['GET'])
def billing_whish_failure():
    external_id = request.args.get('order')
    token = request.args.get('token')
    attempt = BillingPaymentAttempt.query.filter_by(whish_external_id=external_id).first()
    if attempt and attempt.status == 'pending' and attempt.callback_token == token:
        attempt.status = 'failed'
        db.session.commit()
    return redirect(f"{Config.APP_BASE_URL}/billing?status=failed")
```

Check whether `redirect` is already imported from `flask` at the top of `app.py` (`grep -n "^from flask import" app.py`) — add it to that import line if missing. Check whether `relativedelta` is already imported (it's used elsewhere in `app.py` per `recalculate_estimated_profit`'s neighbor `generate_missing_salary_charges` — confirm with `grep -n "from dateutil.relativedelta import relativedelta" app.py`); it should already be imported, reuse it rather than re-importing.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whish_billing.py -v`
Expected: PASS (17 passed)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_whish_billing.py
git commit -m "Add Whish success/failure callback routes"
```

---

## Task 6: `whish_enabled` config flag + `/api/tenant/me` already exposes `plan_expires_at`

**Files:**
- Modify: `app.py` (`billing_config` route, `app.py:1249-1255`)
- Test: Add to `tests/test_whish_billing.py`

**Interfaces:**
- Produces: `GET /api/billing/config` response gains `"whish_enabled": bool`. (No new endpoint needed for plan/expiry status — `GET /api/tenant/me` already returns `Tenant.to_dict()`, which Task 1 already extended with `plan_expires_at`; the frontend in Task 8 reuses the existing `apiService.tenantMe()` call already present in `BillingView.js` rather than adding a redundant route.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_whish_billing.py`:

```python
def test_billing_config_reports_whish_disabled_without_credentials(client, monkeypatch):
    monkeypatch.setattr(appmod.Config, "WHISH_CHANNEL", None)
    monkeypatch.setattr(appmod.Config, "WHISH_SECRET", None)
    hdr = make_tenant(client, "Biz NoWhish", "nowhish_admin")
    r = client.get("/api/billing/config", headers=hdr)
    assert r.get_json()["whish_enabled"] is False


def test_billing_config_reports_whish_enabled_with_credentials(client, monkeypatch):
    monkeypatch.setattr(appmod.Config, "WHISH_CHANNEL", "chan1")
    monkeypatch.setattr(appmod.Config, "WHISH_SECRET", "sec1")
    hdr = make_tenant(client, "Biz Whish", "whish_admin")
    r = client.get("/api/billing/config", headers=hdr)
    assert r.get_json()["whish_enabled"] is True


def test_tenant_me_reports_plan_expiry(client):
    hdr = make_tenant(client, "Biz TenantMe", "tenantme_admin")
    r = client.get("/api/tenant/me", headers=hdr)
    assert r.get_json()["plan_expires_at"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whish_billing.py -k "whish_enabled or tenant_me_reports" -v`
Expected: FAIL — `KeyError: 'whish_enabled'` (the `test_tenant_me_reports_plan_expiry` case should already pass from Task 1's `to_dict()` change — run it to confirm).

- [ ] **Step 3: Extend the route**

Modify `app.py:1249-1255`:

```python
@app.route('/api/billing/config', methods=['GET'])
@jwt_required()
def billing_config():
    # Tells the UI which upgrade paths to show. Contact-to-upgrade is always on;
    # Stripe checkout appears only once keys + a Pro price are configured (kept
    # dormant, not removed -- see the Whish design spec for why); Whish appears
    # only once real channel/secret credentials have been issued.
    stripe_enabled = bool(Config.STRIPE_SECRET_KEY and plans.PLANS.get('pro', {}).get('stripe_price'))
    whish_enabled = bool(Config.WHISH_CHANNEL and Config.WHISH_SECRET)
    return jsonify({"stripe_enabled": stripe_enabled, "whish_enabled": whish_enabled, "contact_enabled": True}), 200
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whish_billing.py -v`
Expected: PASS (20 passed)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_whish_billing.py
git commit -m "Expose whish_enabled in /api/billing/config"
```

---

## Task 7: Renewal reminder + grace-period revert scheduler job

**This is the task where the crash-loop lesson applies directly — read twice before editing.**

**Files:**
- Modify: `app.py` — new functions go **immediately after `auto_sync_upstream_status_with_context()`'s closing line (currently ~`app.py:1953`) and before the `# Start the scheduler in ONE runner only.` comment / `if os.environ.get("RUN_SCHEDULER"...)` block (currently ~`app.py:1957`/`1975`)** — the same place every other scheduler-registered function already lives, for the exact reason today's crash-loop happened when one wasn't.
- Test: Add to `tests/test_whish_billing.py`

**Interfaces:**
- Produces: `check_pro_plan_expirations_for_tenant(tenant_id)` (business logic, called directly by tests, mirroring `auto_sync_upstream_status_for_tenant`'s testing pattern), `check_pro_plan_expirations_with_context()` (the `_with_context` wrapper registered with the scheduler).

- [ ] **Step 1: Confirm current line numbers before editing (they will have shifted from earlier tasks' edits)**

```bash
grep -n "def auto_sync_upstream_status_with_context\|RUN_SCHEDULER.*==.*1.*and not scheduler.running" app.py
```

Note the two line numbers — the new functions go between them.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_whish_billing.py`:

```python
def test_reminder_sent_once_within_window_not_sent_again(app, client):
    make_tenant(client, "Biz Reminder", "reminder_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Reminder").first()
        tenant.plan = 'pro'
        tenant.plan_expires_at = datetime.utcnow() + timedelta(days=3)  # inside the 5-day window
        appmod.db.session.commit()

        appmod.email_util.SENT.clear()
        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        assert len(appmod.email_util.SENT) == 1

        # A second run within the same cycle must not send it again.
        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        assert len(appmod.email_util.SENT) == 1

        tenant = appmod.db.session.get(appmod.Tenant, tenant.id)
        assert tenant.plan_expiry_reminder_sent_at is not None


def test_no_reminder_outside_the_window(app, client):
    make_tenant(client, "Biz NoReminder", "noreminder_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz NoReminder").first()
        tenant.plan = 'pro'
        tenant.plan_expires_at = datetime.utcnow() + timedelta(days=20)  # well outside the 5-day window
        appmod.db.session.commit()

        appmod.email_util.SENT.clear()
        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        assert len(appmod.email_util.SENT) == 0


def test_plan_stays_pro_within_grace_period(app, client):
    make_tenant(client, "Biz Grace", "grace_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Grace").first()
        tenant.plan = 'pro'
        tenant.plan_expires_at = datetime.utcnow() - timedelta(days=1)  # expired 1 day ago, inside 3-day grace
        appmod.db.session.commit()

        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        tenant = appmod.db.session.get(appmod.Tenant, tenant.id)
        assert tenant.plan == 'pro'


def test_plan_reverts_to_free_after_grace_period(app, client):
    make_tenant(client, "Biz Revert", "revert_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz Revert").first()
        tenant.plan = 'pro'
        tenant.plan_expires_at = datetime.utcnow() - timedelta(days=4)  # past the 3-day grace
        tenant.plan_expiry_reminder_sent_at = datetime.utcnow() - timedelta(days=9)
        appmod.db.session.commit()

        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        tenant = appmod.db.session.get(appmod.Tenant, tenant.id)
        assert tenant.plan == 'free'
        assert tenant.plan_expires_at is None
        assert tenant.plan_expiry_reminder_sent_at is None


def test_free_plan_tenant_is_a_noop(app, client):
    make_tenant(client, "Biz FreeNoop", "freenoop_admin")
    with app.app_context():
        tenant = appmod.Tenant.query.filter_by(name="Biz FreeNoop").first()
        appmod.email_util.SENT.clear()
        appmod.check_pro_plan_expirations_for_tenant(tenant.id)
        assert len(appmod.email_util.SENT) == 0
        assert appmod.db.session.get(appmod.Tenant, tenant.id).plan == 'free'
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_whish_billing.py -k "reminder or grace or revert or free_plan_tenant" -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'check_pro_plan_expirations_for_tenant'`

- [ ] **Step 4: Write the functions in the correct location**

Insert into `app.py` at the location found in Step 1 (after `auto_sync_upstream_status_with_context()`'s closing lines, before the scheduler-registration comment block):

```python
# --- Self-serve Pro plan via Whish: renewal reminder + grace-period revert.
# See docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md.
# Whish has no auto-renew -- this is what keeps a lapsed Pro plan from
# silently staying Pro forever, and what nudges a tenant to pay again before
# that happens. Defined here, not near the checkout/callback routes further
# down, for the exact same reason every other scheduler-registered function
# in this file lives here: scheduler.add_job(func=...) below evaluates its
# func argument at import time, so it must already be defined by the time
# that line runs -- getting this wrong crashed production earlier today.
_PRO_PLAN_REMINDER_WINDOW = timedelta(days=5)
_PRO_PLAN_GRACE_PERIOD = timedelta(days=3)


def check_pro_plan_expirations_for_tenant(tenant_id):
    """Send the once-per-cycle reminder email inside the reminder window, and
    revert plan='free' once the grace period has fully elapsed. Never raises
    -- one tenant's email failure must not stop the rest of the daily job
    (see the _with_context wrapper below), matching this file's established
    pattern for every other daily job."""
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant or tenant.plan != 'pro' or not tenant.plan_expires_at:
        return
    now = datetime.utcnow()

    if now > tenant.plan_expires_at + _PRO_PLAN_GRACE_PERIOD:
        tenant.plan = 'free'
        tenant.plan_expires_at = None
        tenant.plan_expiry_reminder_sent_at = None
        db.session.commit()
        return

    if now >= tenant.plan_expires_at - _PRO_PLAN_REMINDER_WINDOW and not tenant.plan_expiry_reminder_sent_at:
        settings = BusinessSettings.query.filter_by(tenant_id=tenant.id).first()
        to_email = settings.email if settings and settings.email else None
        if to_email:
            try:
                email_util.send(
                    to_email, "servicesBills: your Pro plan is expiring soon",
                    f"Your ServiceBills Pro plan expires on {tenant.plan_expires_at.strftime('%Y-%m-%d')}. "
                    f"Renew from the Billing page to keep uninterrupted access.",
                )
            except Exception as e:
                logging.warning(f"Pro-plan renewal reminder email failed for tenant {tenant_id}: {e}")
        tenant.plan_expiry_reminder_sent_at = now
        db.session.commit()


def check_pro_plan_expirations_with_context():
    with app.app_context():
        for t in Tenant.query.filter_by(plan='pro').all():
            try:
                check_pro_plan_expirations_for_tenant(t.id)
            except Exception as e:
                db.session.rollback()
                logging.error(f"Pro-plan expiration check failed for tenant {t.id}: {e}")
```

Then register the job inside the existing `if os.environ.get("RUN_SCHEDULER", "1") == "1" and not scheduler.running:` block, alongside the other `scheduler.add_job(...)` calls:

```python
    scheduler.add_job(func=check_pro_plan_expirations_with_context, trigger="interval", days=1, next_run_time=datetime.now())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_whish_billing.py -v`
Expected: PASS (25 passed)

- [ ] **Step 6: The RUN_SCHEDULER=1 smoke test — the actual regression guard**

This is the step that would have caught today's crash-loop before it ever reached production. Run it for real, don't skip it:

```bash
JWT_SECRET_KEY=test SECRET_KEY=test DATABASE_URL="sqlite:///$(pwd)/scratch_smoke_test.db" RUN_SCHEDULER=1 python -c "
import app
print('IMPORT OK -- jobs:', [j.func.__name__ for j in app.scheduler.get_jobs()])
app.scheduler.shutdown(wait=False)
"
rm -f scratch_smoke_test.db
```

Expected: `IMPORT OK -- jobs: [..., 'check_pro_plan_expirations_with_context']` with no `NameError`/traceback. If this fails, the new functions are still in the wrong place relative to `scheduler.add_job()` — fix before continuing, do not defer to a later task.

- [ ] **Step 7: Run the full test suite once more (regression check)**

Run: `python -m pytest -q`
Expected: all tests pass, matching the pre-existing count plus this task's new ones.

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_whish_billing.py
git commit -m "Add Pro-plan renewal reminder and grace-period revert scheduler job"
```

---

## Task 8: Frontend — `BillingView.js` Whish upgrade UI

**Files:**
- Modify: `frontend/src/context/AppContext.js` (add one `apiService` method near the existing billing ones, `AppContext.js:96-99`)
- Modify: `frontend/src/components/BillingView.js`

**Interfaces:**
- Consumes: `POST /api/billing/whish/checkout` (Task 4), `GET /api/billing/config`'s new `whish_enabled` (Task 6), `GET /api/tenant/me`'s `plan_expires_at` (Task 1) — `tenantMe()` and `billingConfig()` are already called in `BillingView.js`, no new fetch needed.

- [ ] **Step 1: Add the API method**

Modify `frontend/src/context/AppContext.js`, right after the existing `billingContact` line (`AppContext.js:99`):

```javascript
    billingContact: (payload) => api.post('/billing/contact', payload),
    billingWhishCheckout: (cycle) => api.post('/billing/whish/checkout', { cycle }),
```

- [ ] **Step 2: Update `BillingView.js`**

Replace the full file content of `frontend/src/components/BillingView.js`:

```javascript
import React, { useEffect, useState } from 'react';
import {
    Box, Typography, Card, CardContent, Button, Chip, Grid, CircularProgress, Alert,
    Dialog, DialogTitle, DialogContent, DialogActions, TextField, Stack, ToggleButtonGroup, ToggleButton,
} from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const FEATURES = {
    free: ['Up to 50 customers', 'Manual WhatsApp (deep-link)', 'Core billing, payments & receipts'],
    pro: ['Unlimited customers', 'WhatsApp Cloud API (auto-send)', 'All servicesBills features'],
};

const WHISH_PRICES = { monthly: '$120/mo', yearly: '$1000/yr (save ~30%)' };

function planExpiryBanner(tenant) {
    if (!tenant || tenant.plan !== 'pro' || !tenant.plan_expires_at) return null;
    const expiresAt = new Date(tenant.plan_expires_at.replace(' ', 'T'));
    const daysLeft = Math.ceil((expiresAt - new Date()) / (1000 * 60 * 60 * 24));
    if (daysLeft > 5) return null;
    if (daysLeft >= 0) {
        return { severity: 'warning', message: `Pro expires in ${daysLeft} day${daysLeft === 1 ? '' : 's'} — renew now to avoid interruption.` };
    }
    const graceDaysLeft = 3 + daysLeft;
    if (graceDaysLeft > 0) {
        return { severity: 'error', message: `Pro expired — renew within ${graceDaysLeft} day${graceDaysLeft === 1 ? '' : 's'} to avoid losing access.` };
    }
    return null;
}

const BillingView = () => {
    const { apiService, setSnackbar, user } = useAppContext();
    const [tenant, setTenant] = useState(null);
    const [plans, setPlans] = useState({});
    const [stripeEnabled, setStripeEnabled] = useState(false);
    const [whishEnabled, setWhishEnabled] = useState(false);
    const [cycle, setCycle] = useState('monthly');
    const [busy, setBusy] = useState(false);
    const [contactOpen, setContactOpen] = useState(false);
    const [contact, setContact] = useState({ name: '', email: user?.email || '', phone: '', message: '' });

    useEffect(() => {
        apiService.tenantMe().then((r) => setTenant(r.data)).catch(() => setTenant({ plan: 'free', status: 'active' }));
        apiService.listPlans().then((r) => setPlans(r.data)).catch(() => setPlans({}));
        apiService.billingConfig().then((r) => {
            setStripeEnabled(!!r.data.stripe_enabled);
            setWhishEnabled(!!r.data.whish_enabled);
        }).catch(() => { setStripeEnabled(false); setWhishEnabled(false); });
        const status = new URLSearchParams(window.location.search).get('status');
        if (status === 'success') setSnackbar({ open: true, message: 'Payment received — your plan has been updated.', severity: 'success' });
        if (status === 'failed') setSnackbar({ open: true, message: 'Payment failed or was canceled.', severity: 'error' });
        if (status === 'error') setSnackbar({ open: true, message: 'Could not verify that payment. Contact support if you were charged.', severity: 'error' });
        if (status === 'cancel') setSnackbar({ open: true, message: 'Checkout canceled.', severity: 'info' });
    }, [apiService, setSnackbar]);

    const upgradeStripe = async () => {
        setBusy(true);
        try {
            const r = await apiService.billingCheckout('pro');
            window.location.href = r.data.url;
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Checkout failed.', severity: 'error' });
            setBusy(false);
        }
    };

    const upgradeWhish = async () => {
        setBusy(true);
        try {
            const r = await apiService.billingWhishCheckout(cycle);
            window.location.href = r.data.redirect;
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Checkout failed.', severity: 'error' });
            setBusy(false);
        }
    };

    const manage = async () => {
        setBusy(true);
        try {
            const r = await apiService.billingPortal();
            window.location.href = r.data.url;
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Could not open billing portal.', severity: 'error' });
            setBusy(false);
        }
    };

    const submitContact = async () => {
        setBusy(true);
        try {
            const r = await apiService.billingContact({ plan: 'pro', ...contact });
            setContactOpen(false);
            setSnackbar({ open: true, message: r.data.msg || 'Request sent.', severity: 'success' });
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Could not send request.', severity: 'error' });
        } finally {
            setBusy(false);
        }
    };

    if (!tenant) return <Box sx={{ p: 4, textAlign: 'center' }}><CircularProgress /></Box>;

    const banner = planExpiryBanner(tenant);

    return (
        <Box sx={{ p: { xs: 2, md: 3 } }}>
            <Typography variant="h5" sx={{ mb: 2 }}>Billing &amp; Plan</Typography>
            <Box sx={{ mb: 3, display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                <Typography>Current plan:</Typography>
                <Chip label={(tenant.plan || 'free').toUpperCase()} color="primary" />
                <Chip label={tenant.status} color={tenant.status === 'active' ? 'success' : 'warning'} variant="outlined" />
                {tenant.plan === 'pro' && tenant.plan_expires_at && (
                    <Typography variant="body2" color="text.secondary">expires {tenant.plan_expires_at}</Typography>
                )}
            </Box>
            {tenant.status !== 'active' && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                    Your subscription is inactive. Upgrade or contact us to restore full access.
                </Alert>
            )}
            {banner && <Alert severity={banner.severity} sx={{ mb: 2 }}>{banner.message}</Alert>}
            <Grid container spacing={2}>
                {Object.keys(plans).map((name) => (
                    <Grid item xs={12} md={6} key={name}>
                        <Card variant="outlined" sx={{
                            borderColor: tenant.plan === name ? 'primary.main' : 'divider',
                            borderWidth: tenant.plan === name ? 2 : 1,
                        }}>
                            <CardContent>
                                <Typography variant="h6" sx={{ textTransform: 'capitalize', mb: 1 }}>{name}</Typography>
                                <Box component="ul" sx={{ pl: 2, mb: 2, color: 'text.secondary' }}>
                                    {(FEATURES[name] || []).map((f, i) => <li key={i}>{f}</li>)}
                                </Box>
                                {name === 'pro' && (
                                    <Stack spacing={1.5}>
                                        {whishEnabled && (
                                            <>
                                                <ToggleButtonGroup size="small" value={cycle} exclusive
                                                    onChange={(e, v) => v && setCycle(v)}>
                                                    <ToggleButton value="monthly">{WHISH_PRICES.monthly}</ToggleButton>
                                                    <ToggleButton value="yearly">{WHISH_PRICES.yearly}</ToggleButton>
                                                </ToggleButtonGroup>
                                                <Button variant="contained" disabled={busy} onClick={upgradeWhish}>
                                                    {tenant.plan === 'pro' ? 'Renew via Whish' : 'Upgrade via Whish'}
                                                </Button>
                                            </>
                                        )}
                                        {stripeEnabled && (
                                            <Button variant="outlined" disabled={busy} onClick={upgradeStripe}>
                                                Upgrade to Pro (Stripe)
                                            </Button>
                                        )}
                                        {tenant.plan !== 'pro' && (
                                            <Button variant={whishEnabled || stripeEnabled ? 'text' : 'contained'} disabled={busy}
                                                    onClick={() => setContactOpen(true)}>
                                                Contact us to upgrade
                                            </Button>
                                        )}
                                    </Stack>
                                )}
                            </CardContent>
                        </Card>
                    </Grid>
                ))}
            </Grid>
            {stripeEnabled && tenant.plan !== 'free' && (
                <Button sx={{ mt: 3 }} variant="outlined" disabled={busy} onClick={manage}>
                    Manage subscription
                </Button>
            )}

            <Dialog open={contactOpen} onClose={() => setContactOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle>Contact us to upgrade to Pro</DialogTitle>
                <DialogContent>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                        Leave your details and we'll get in touch to complete the upgrade.
                    </Typography>
                    <Stack spacing={2} sx={{ mt: 1 }}>
                        <TextField label="Your name" value={contact.name} onChange={(e) => setContact({ ...contact, name: e.target.value })} fullWidth />
                        <TextField label="Email" value={contact.email} onChange={(e) => setContact({ ...contact, email: e.target.value })} fullWidth />
                        <TextField label="Phone" value={contact.phone} onChange={(e) => setContact({ ...contact, phone: e.target.value })} fullWidth />
                        <TextField label="Message (optional)" value={contact.message} onChange={(e) => setContact({ ...contact, message: e.target.value })} fullWidth multiline minRows={2} />
                    </Stack>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setContactOpen(false)}>Cancel</Button>
                    <Button variant="contained" disabled={busy} onClick={submitContact}>Send request</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default BillingView;
```

- [ ] **Step 3: Manual verification (no automated frontend tests in this repo)**

Start the dev server and browser-check, mirroring how the upstream-sync toggle was verified earlier today:
1. `whish_enabled` is `false` (no credentials yet) — confirm the "Upgrade via Whish" button does NOT render, only "Contact us to upgrade" (and "Upgrade to Pro (Stripe)" if `STRIPE_SECRET_KEY` happens to be set locally).
2. Temporarily monkeypatch `Config.WHISH_CHANNEL`/`WHISH_SECRET` in a local `.env` (or patch `billing_config()` to return `whish_enabled: true` for this manual check only, then revert) to confirm the monthly/yearly toggle and "Upgrade via Whish" button render correctly, and that clicking it hits `POST /api/billing/whish/checkout` (check Network tab — expect a 502 since there's no real Whish endpoint reachable in dev, which is the correct behavior with no real credentials).
3. Manually set a tenant's `plan_expires_at` via `flask shell` (or a scratch script) to 2 days from now and confirm the amber reminder banner renders; set it to 1 day in the past and confirm the red grace-period banner renders with the correct day count.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/context/AppContext.js frontend/src/components/BillingView.js
git commit -m "Add Whish self-serve upgrade UI to the Billing page"
```

---

## Task 9: Frontend — `LandingView.js` real pricing

**Files:**
- Modify: `frontend/src/components/LandingView.js:15-18`

- [ ] **Step 1: Update the pricing card**

Modify the `PLANS` constant:

```javascript
const PLANS = [
    { name: 'Free', price: '$0', features: ['Up to 50 customers', 'Manual WhatsApp (deep-link)', 'Core billing & receipts'] },
    { name: 'Pro', price: '$120/mo', note: 'or $1000/yr — save ~30%', highlighted: true, features: ['Unlimited customers', 'WhatsApp Cloud API (auto-send)', 'All features'] },
];
```

Then modify the card rendering to show `p.note` under the price (currently `LandingView.js:77`, the `<Typography variant="h4">{p.price}</Typography>` line):

```javascript
                                <Typography variant="h4" sx={{ fontWeight: 800, mb: p.note ? 0.5 : 2 }}>{p.price}</Typography>
                                {p.note && <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{p.note}</Typography>}
```

- [ ] **Step 2: Manual verification**

Start the dev server, load the landing page (logged out), confirm the Pro card shows "$120/mo" with "or $1000/yr — save ~30%" underneath, and the "Get started" button still routes to `/register`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/LandingView.js
git commit -m "Show real Pro pricing on the landing page"
```

---

## Task 10: Deployment config — env var slots, `APP_BASE_URL` verification

**Files:**
- Modify: `render.yaml`
- Modify: `DEPLOY.md` (env var documentation section)

**Interfaces:** None (config/docs only, no code).

- [ ] **Step 1: Add Whish env var slots to `render.yaml`**

Modify `render.yaml`, right after the existing `STRIPE_PRICE_PRO` block (`render.yaml:51-52`):

```yaml
      - key: STRIPE_PRICE_PRO
        sync: false
      - key: WHISH_CHANNEL
        sync: false        # not yet issued -- self-serve Whish checkout stays hidden until this is set
      - key: WHISH_SECRET
        sync: false
```

- [ ] **Step 2: Document in `DEPLOY.md`**

Find the Stripe section of `DEPLOY.md` (referenced earlier in this project as containing `STRIPE_SECRET_KEY`/`APP_BASE_URL` documentation) and add a parallel note for Whish:

```markdown
### Whish (Lebanon self-serve Pro plan)

Set `WHISH_CHANNEL` and `WHISH_SECRET` once Whish support issues them for
this business's merchant account (not Stripe -- Stripe is not used for this
market, see docs/superpowers/specs/2026-08-26-whish-self-serve-billing-design.md).
Until both are set, the self-serve Whish checkout button stays hidden and
`/api/billing/whish/checkout` is simply unused -- no code change needed
when credentials do arrive, just set the two env vars and redeploy.

**`APP_BASE_URL` must be the real, working public domain** --
`https://servicebills.salloumservices.com`, not the `onrender.com` hostname
(confirmed broken/unreachable as this app's primary URL -- see the
`project-security-hotfix-roadmap` memory's note on this). Whish's payment
callback is a browser redirect to `{APP_BASE_URL}/api/billing/whish/success`;
a wrong `APP_BASE_URL` means paying customers land on a broken URL after
paying. Verify this in the Render dashboard's environment settings before
Whish credentials are added -- do not assume `RENDER_EXTERNAL_URL`'s
fallback value is correct.
```

- [ ] **Step 3: Commit**

```bash
git add render.yaml DEPLOY.md
git commit -m "Document Whish env vars and verify APP_BASE_URL for Whish callbacks"
```

---

## Final check before opening a PR

- [ ] Run the full suite once more: `python -m pytest -q` — expect all tests passing (existing count + ~25 new ones from this plan).
- [ ] Re-run the `RUN_SCHEDULER=1` import smoke test from Task 7, Step 6 one more time after all other tasks have landed, to catch any accidental re-ordering from a later task.
- [ ] Push the branch and follow this repo's established PR workflow (CI + cloud Postgres dry-run verification) — do not push to `origin/main` directly; wait for the user's explicit "merge it locally" / "push it to production" as with every prior phase.
