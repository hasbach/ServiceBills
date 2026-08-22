# Upstream Portal Read-Only Status Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let staff, from a bridged customer's Subscriptions edit panel, trigger a read-only browser-automation check of that customer's real status on their upstream RADIUS portal (Terra first), and see both the portal's own status light and any drift against ServiceBills' own billing-cycle expiry date.

**Architecture:** A new `upstream_portal.py` adapter module (mirroring the existing `mikrotik.py` contract: every public function returns `(ok, value)` and never raises) drives headless Chromium via Playwright to log into the upstream portal, find one subscriber row by `Customer.upstream_username`, and read back status + expiry. A new Flask endpoint runs this synchronously on a staff button click and persists the result onto three new nullable `Customer` columns. Drift between the stored upstream expiry and ServiceBills' own `subscription_expiry_date` is computed on read (never stored) by a small pure helper.

**Tech Stack:** Flask + SQLAlchemy + Flask-Migrate (existing), Playwright (new dependency, headless Chromium), `python-dateutil` (existing, for lenient date parsing), React + MUI (existing frontend).

**Spec:** [docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md](../specs/2026-08-22-upstream-status-sync-design.md)

## Global Constraints

- Read-only only. No task in this plan may click Renew/Block/Unblock or any other mutating action on the upstream portal.
- Scoped to one upstream: Terra (PROradius product). No other upstream, and no CAPTCHA'd "radiusnew" portal, is touched by this plan.
- No scheduled or bulk sync. The only trigger is a staff-initiated button click for one customer at a time.
- The DB migration must be additive-only: new nullable columns on `Customer`, no existing column or table modified.
- A full production database backup is a required manual step before this migration is ever applied to production (see Task 2).
- Every public function in `upstream_portal.py` must return `(ok: bool, value)` and never raise, matching `mikrotik.py`'s existing contract.
- A failed sync must never clear or zero out a customer's previously-synced `upstream_actual_expiry` / `upstream_last_status` / `upstream_last_synced_at` -- only a successful sync updates them.

---

## Task 1: Add Playwright dependency

**Files:**
- Modify: `requirements.txt`
- Modify: `Dockerfile`

**Interfaces:**
- Produces: the `playwright` Python package and its headless Chromium binary, available to `import playwright.sync_api` in later tasks.

- [ ] **Step 1: Add the dependency**

Add a new line to `requirements.txt` (matching the file's existing unpinned-version convention), after the last line (`librouteros`):

```
playwright
```

- [ ] **Step 2: Install it locally and download the browser binary**

Run:
```bash
pip install playwright
python -m playwright install --with-deps chromium
```
Expected: both commands exit 0. This may take a few minutes on first run (downloads a real Chromium build).

- [ ] **Step 3: Install the browser binary in the Docker image too**

In `Dockerfile`, immediately after this existing line:
```dockerfile
RUN pip install --no-cache-dir -r requirements.txt
```
add:
```dockerfile
RUN playwright install --with-deps chromium
```
So that block reads:
```dockerfile
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium
```

- [ ] **Step 4: Verify the import works**

Run:
```bash
python -c "from playwright.sync_api import sync_playwright; print('ok')"
```
Expected: prints `ok` with no errors.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt Dockerfile
git commit -m "build: add Playwright + headless Chromium for upstream portal automation"
```

---

## Task 2: Database migration -- add upstream sync columns to Customer

**Files:**
- Modify: `app.py` (Customer model)
- Create: `migrations/versions/e2f8a4c19b70_add_upstream_status_sync_fields.py`
- Test: `tests/test_upstream_drift.py` (model-shape assertions only; the drift *logic* is tested in Task 3)

**Interfaces:**
- Produces: `Customer.upstream_actual_expiry` (DateTime, nullable), `Customer.upstream_last_status` (String(20), nullable), `Customer.upstream_last_synced_at` (DateTime, nullable).

- [ ] **Step 1: Add the three columns to the `Customer` model**

In `app.py`, the `Customer` model currently has this block (around line 296-301):

```python
    upstream_provider_id = db.Column(db.Integer, db.ForeignKey('upstream_provider.id'), nullable=True)
    upstream_username = db.Column(db.String(100), nullable=True)
    # Populated only when network_mode is 'local_mikrotik' -- the router this
    # customer authenticates against and their /ppp/secret name on it.
    mikrotik_server_id = db.Column(db.Integer, db.ForeignKey('mikrotik_server.id'), nullable=True)
    pppoe_username = db.Column(db.String(100), nullable=True)
```

Change it to add the three new columns right after `upstream_username`:

```python
    upstream_provider_id = db.Column(db.Integer, db.ForeignKey('upstream_provider.id'), nullable=True)
    upstream_username = db.Column(db.String(100), nullable=True)
    # Read-only mirror of this customer's real state on the upstream portal,
    # written only by upstream_portal.get_subscriber_status() via the
    # /upstream-status-sync endpoint -- never by billing logic. A failed sync
    # leaves all three untouched rather than clearing them. See
    # docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md.
    upstream_actual_expiry = db.Column(db.DateTime, nullable=True)
    upstream_last_status = db.Column(db.String(20), nullable=True)  # 'online' | 'offline' | 'expired' | 'unknown'
    upstream_last_synced_at = db.Column(db.DateTime, nullable=True)
    # Populated only when network_mode is 'local_mikrotik' -- the router this
    # customer authenticates against and their /ppp/secret name on it.
    mikrotik_server_id = db.Column(db.Integer, db.ForeignKey('mikrotik_server.id'), nullable=True)
    pppoe_username = db.Column(db.String(100), nullable=True)
```

- [ ] **Step 2: Write the migration file**

Create `migrations/versions/e2f8a4c19b70_add_upstream_status_sync_fields.py`:

```python
"""add upstream status sync fields to customer

Revision ID: e2f8a4c19b70
Revises: c57bc44a51d0
Create Date: 2026-08-22 00:00:00.000000

Adds the read-only upstream-portal mirror fields from
docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md: three
nullable columns on Customer, written only by the new
/customers/<id>/upstream-status-sync endpoint, never by billing logic.
Purely additive -- no existing column or table is touched.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2f8a4c19b70'
down_revision = 'c57bc44a51d0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('upstream_actual_expiry', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('upstream_last_status', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('upstream_last_synced_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.drop_column('upstream_last_synced_at')
        batch_op.drop_column('upstream_last_status')
        batch_op.drop_column('upstream_actual_expiry')
```

- [ ] **Step 3: Apply it locally and verify**

Run:
```bash
flask db upgrade
flask db current
```
Expected: `flask db current` reports `e2f8a4c19b70 (head)` with no errors.

- [ ] **Step 4: Write a model-shape test**

Create `tests/test_upstream_drift.py`:

```python
import app as appmod


def test_customer_has_upstream_sync_columns():
    cols = {c.name for c in appmod.Customer.__table__.columns}
    assert {'upstream_actual_expiry', 'upstream_last_status', 'upstream_last_synced_at'} <= cols
```

- [ ] **Step 5: Run it**

Run: `pytest tests/test_upstream_drift.py -v`
Expected: PASS (this test file gains more cases in Task 3).

- [ ] **Step 6: Commit**

```bash
git add app.py migrations/versions/e2f8a4c19b70_add_upstream_status_sync_fields.py tests/test_upstream_drift.py
git commit -m "feat: add upstream status sync columns to Customer"
```

- [ ] **Step 7: Production rollout note (manual, not automated by this plan)**

Before this migration is ever applied to the production database: take a full production database backup and record how to restore it, per the spec's "Rollout & data safety" section. Do not skip this because the migration is additive -- it's the agreed rollback point if anything about this feature goes wrong later.

---

## Task 3: Drift-detection helper

**Files:**
- Modify: `app.py`
- Test: `tests/test_upstream_drift.py`

**Interfaces:**
- Consumes: any object with `.upstream_actual_expiry` (datetime|None) and `.subscription_expiry_date` (datetime|None) attributes -- in practice a `Customer` instance.
- Produces: `_compute_upstream_drift(customer) -> dict|None`, called by Task 5's endpoint and serialization code. Return shape: `None` (no drift / nothing synced yet) or `{'severity': 'info'|'alert', 'days': int}` (`days` is always positive).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_upstream_drift.py`:

```python
import types
from datetime import datetime

import app as appmod


def _customer(upstream_actual_expiry, subscription_expiry_date):
    return types.SimpleNamespace(
        upstream_actual_expiry=upstream_actual_expiry,
        subscription_expiry_date=subscription_expiry_date,
    )


def test_drift_none_when_never_synced():
    c = _customer(None, datetime(2026, 9, 1))
    assert appmod._compute_upstream_drift(c) is None


def test_drift_none_when_dates_match():
    c = _customer(datetime(2026, 9, 1), datetime(2026, 9, 1))
    assert appmod._compute_upstream_drift(c) is None


def test_drift_info_when_upstream_is_later():
    # Staff manually topped up on the upstream portal -- harmless, informational.
    c = _customer(datetime(2026, 9, 5), datetime(2026, 9, 1))
    assert appmod._compute_upstream_drift(c) == {'severity': 'info', 'days': 4}


def test_drift_alert_when_upstream_is_earlier():
    # Upstream expires before ServiceBills thinks it does -- real outage risk.
    c = _customer(datetime(2026, 8, 28), datetime(2026, 9, 1))
    assert appmod._compute_upstream_drift(c) == {'severity': 'alert', 'days': 4}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_upstream_drift.py -v`
Expected: the 4 new tests FAIL with `AttributeError: module 'app' has no attribute '_compute_upstream_drift'`.

- [ ] **Step 3: Implement `_compute_upstream_drift`**

In `app.py`, immediately after the `_maybe_restore_mikrotik_access` function (it ends around line 2832 with `return {'attempted': True, 'ok': False, 'message': str(e)}` inside its `except` block), add:

```python
def _compute_upstream_drift(customer):
    """Compare ServiceBills' own subscription_expiry_date against the last
    upstream_actual_expiry synced from the portal. Computed on every read,
    never stored -- always consistent with whatever the two dates currently
    are. Returns None if nothing has been synced yet or the two dates match;
    otherwise {'severity': 'info'|'alert', 'days': <positive int>}.

    'info' means the upstream has MORE runway than ServiceBills' billing
    cycle (e.g. staff manually topped up on the portal) -- harmless.
    'alert' means the upstream expires SOONER than ServiceBills' billing
    cycle -- a real risk the customer could be cut off despite showing
    paid/active in ServiceBills. See
    docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md."""
    if not customer.upstream_actual_expiry or not customer.subscription_expiry_date:
        return None
    delta_days = (customer.upstream_actual_expiry.date() - customer.subscription_expiry_date.date()).days
    if delta_days > 0:
        return {'severity': 'info', 'days': delta_days}
    if delta_days < 0:
        return {'severity': 'alert', 'days': abs(delta_days)}
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_upstream_drift.py -v`
Expected: all 5 tests PASS (the 4 new ones plus the Task 2 column-shape test).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_upstream_drift.py
git commit -m "feat: add upstream expiry drift-detection helper"
```

---

## Task 4: `upstream_portal.py` adapter module

**Files:**
- Create: `upstream_portal.py`
- Test: `tests/test_upstream_portal.py`

**Interfaces:**
- Consumes: a provider-like object with `.id`, `.portal_url`, `.portal_username`, `.portal_password` (in practice an `UpstreamProvider` instance -- `portal_password` is already plaintext by the time Python code reads it, since it's stored via the existing `EncryptedString` column type which decrypts transparently on read).
- Produces: `get_subscriber_status(provider, username) -> (ok: bool, value)`, consumed by Task 5's endpoint. On success, `value = {'status': 'online'|'offline'|'expired'|'unknown', 'expiry': datetime|None}`. On failure, `value` is one of the strings `'auth_failed'`, `'not_found'`, `'timeout'`, `'scrape_failed'`.

**Important note on the selector constants below:** `LOGIN_USERNAME_SELECTOR`, `LOGIN_PASSWORD_SELECTOR`, `LOGIN_SUBMIT_SELECTOR`, `LOGIN_SUCCESS_SELECTOR`, and the status-text matching in `_parse_status` are best-guess values based on common portal patterns -- they have **not** been confirmed against Terra's real page yet. This task's unit tests mock the Playwright `Page` object entirely, so they pass regardless of whether these guesses are correct; they verify this module's *orchestration and error-classification logic*, not Terra's actual DOM. Task 7 (live discovery) and Task 8 (apply findings) confirm/correct these constants against the real site -- nothing else in this module should need to change because of that.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_upstream_portal.py`:

```python
"""Tests for upstream_portal.py -- no real browser or real portal involved.
`sync_playwright` is monkeypatched to a small fake Playwright/Browser/Page
double, mirroring the approach tests/test_mikrotik.py uses for librouteros:
assert on orchestration and error classification, not on Terra's real DOM
(which these fakes don't attempt to model)."""
import types

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import upstream_portal


def make_provider(id=1, portal_url="https://example.test/login", portal_username="u", portal_password="p"):
    return types.SimpleNamespace(id=id, portal_url=portal_url, portal_username=portal_username, portal_password=portal_password)


class FakeLocator:
    def __init__(self, text, found):
        self._text = text
        self._found = found

    def count(self):
        return 1 if self._found else 0

    @property
    def first(self):
        return self

    def inner_text(self):
        return self._text


class FakePage:
    def __init__(self, row_text="", row_found=True, login_succeeds=True, goto_raises=None):
        self._row_text = row_text
        self._row_found = row_found
        self._login_succeeds = login_succeeds
        self._goto_raises = goto_raises
        self.filled = {}
        self.clicked = []

    def set_default_timeout(self, ms):
        pass

    def goto(self, url, timeout=None):
        if self._goto_raises:
            raise self._goto_raises

    def fill(self, selector, value):
        self.filled[selector] = value

    def click(self, selector):
        self.clicked.append(selector)

    def wait_for_selector(self, selector, timeout=None):
        if not self._login_succeeds:
            raise PlaywrightTimeoutError("timed out waiting for login")

    def locator(self, selector, has_text=None):
        return FakeLocator(self._row_text, found=self._row_found)


class FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_page(self):
        return self._page

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, page):
        self._page = page

    def launch(self, headless=True):
        return FakeBrowser(self._page)


class FakePlaywright:
    def __init__(self, page):
        self.chromium = FakeChromium(page)


class FakeSyncPlaywrightCM:
    def __init__(self, page):
        self._page = page

    def __enter__(self):
        return FakePlaywright(self._page)

    def __exit__(self, *a):
        return False


def patch_playwright(monkeypatch, page):
    monkeypatch.setattr(upstream_portal, "sync_playwright", lambda: FakeSyncPlaywrightCM(page))


# --- Success cases ---

def test_get_subscriber_status_online(monkeypatch):
    page = FakePage(row_text="user1  Online  Expires 2026-09-01", row_found=True)
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert ok is True
    assert value["status"] == "online"
    assert value["expiry"].year == 2026 and value["expiry"].month == 9 and value["expiry"].day == 1


def test_get_subscriber_status_expired(monkeypatch):
    page = FakePage(row_text="user1  Expired  Expires 2026-01-15", row_found=True)
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "expired")


def test_login_fills_credentials_and_submits(monkeypatch):
    page = FakePage(row_text="user1 Online", row_found=True)
    patch_playwright(monkeypatch, page)

    upstream_portal.get_subscriber_status(make_provider(portal_username="alice", portal_password="s3cret"), "user1")

    assert page.filled[upstream_portal.LOGIN_USERNAME_SELECTOR] == "alice"
    assert page.filled[upstream_portal.LOGIN_PASSWORD_SELECTOR] == "s3cret"
    assert page.clicked == [upstream_portal.LOGIN_SUBMIT_SELECTOR]


# --- Failure cases ---

def test_get_subscriber_status_not_found(monkeypatch):
    page = FakePage(row_text="", row_found=False)
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "ghost")

    assert (ok, reason) == (False, "not_found")


def test_get_subscriber_status_auth_failed(monkeypatch):
    page = FakePage(login_succeeds=False)
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "auth_failed")


def test_get_subscriber_status_timeout(monkeypatch):
    page = FakePage(goto_raises=PlaywrightTimeoutError("portal did not respond"))
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "timeout")


def test_get_subscriber_status_scrape_failed_on_generic_error(monkeypatch):
    page = FakePage(goto_raises=PlaywrightError("navigation crashed"))
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "scrape_failed")


def test_browser_always_closed_even_on_failure(monkeypatch):
    page = FakePage(login_succeeds=False)
    patch_playwright(monkeypatch, page)
    browser_holder = {}
    real_launch = FakeChromium.launch

    def capturing_launch(self, headless=True):
        b = real_launch(self, headless)
        browser_holder["browser"] = b
        return b

    monkeypatch.setattr(FakeChromium, "launch", capturing_launch)

    upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert browser_holder["browser"].closed is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_upstream_portal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'upstream_portal'`.

- [ ] **Step 3: Implement `upstream_portal.py`**

Create `upstream_portal.py`:

```python
"""Browser-automation adapter for read-only status checks against upstream
RADIUS reseller portals (Concept A / 'upstream_bridge'). See
docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md.

Read-only only: this module never clicks Renew/Block/Unblock or any other
mutating action, only logs in, reads one subscriber's row, and logs out.
Every public function returns (ok: bool, value) and never raises -- a scrape
failure must never block or crash a billing-side request, same contract as
mikrotik.py.

Written and proven against Terra (PROradius product) first; parameterized by
provider.portal_url so the same code should serve the other PROradius
upstreams (IDM, Northern Telecom, Net360, Eaglenet) later without changes,
once the selector constants below are confirmed against each. The CAPTCHA'd
'radiusnew' family is explicitly out of scope for this module.
"""
import logging

from dateutil import parser as date_parser
from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

_TIMEOUT_MS = 25_000

# Best-guess selectors for Terra's PROradius portal, NOT yet confirmed
# against the live site -- see the "Live discovery against Terra" task in
# docs/superpowers/plans/2026-08-22-upstream-status-sync.md. Update these
# constants (and _parse_status's text matching, if needed) once that task
# runs -- nothing else in this module should need to change just because one
# of these guesses was wrong.
LOGIN_USERNAME_SELECTOR = 'input[name="username"]'
LOGIN_PASSWORD_SELECTOR = 'input[name="password"]'
LOGIN_SUBMIT_SELECTOR = 'button[type="submit"]'
LOGIN_SUCCESS_SELECTOR = 'text=Logout'  # presence after submit == login succeeded
SUBSCRIBER_TABLE_SELECTOR = 'table'

_STATUS_TEXT_MAP = (
    ('expired', 'expired'),
    ('offline', 'offline'),
    ('online', 'online'),
)


class LoginFailed(Exception):
    pass


class SubscriberNotFound(Exception):
    pass


def _login(page, provider):
    page.goto(provider.portal_url, timeout=_TIMEOUT_MS)
    page.fill(LOGIN_USERNAME_SELECTOR, provider.portal_username)
    page.fill(LOGIN_PASSWORD_SELECTOR, provider.portal_password)
    page.click(LOGIN_SUBMIT_SELECTOR)
    try:
        page.wait_for_selector(LOGIN_SUCCESS_SELECTOR, timeout=_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise LoginFailed("Login did not reach the logged-in page")


def _find_subscriber_row(page, username):
    row = page.locator(f"{SUBSCRIBER_TABLE_SELECTOR} tr", has_text=username)
    if row.count() == 0:
        raise SubscriberNotFound(username)
    return row.first


def _parse_status(row_text):
    lowered = row_text.lower()
    for needle, status in _STATUS_TEXT_MAP:
        if needle in lowered:
            return status
    return "unknown"


def _parse_expiry(row_text):
    try:
        return date_parser.parse(row_text, fuzzy=True)
    except (ValueError, OverflowError):
        return None


def get_subscriber_status(provider, username):
    """Logs into `provider`'s portal, finds `username` in the subscriber
    list, reads back their status + expiry.

    Returns (True, {'status': 'online'|'offline'|'expired'|'unknown',
    'expiry': datetime|None}) on success. Returns (False, reason) on any
    failure, where reason is one of 'auth_failed', 'not_found', 'timeout',
    'scrape_failed'. Never raises.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_default_timeout(_TIMEOUT_MS)
                _login(page, provider)
                row = _find_subscriber_row(page, username)
                row_text = row.inner_text()
            finally:
                browser.close()
    except LoginFailed as e:
        logger.warning("Upstream portal login failed for provider %s: %s", provider.id, e)
        return False, "auth_failed"
    except SubscriberNotFound:
        return False, "not_found"
    except PlaywrightTimeoutError as e:
        logger.warning("Upstream portal timed out for provider %s: %s", provider.id, e)
        return False, "timeout"
    except PlaywrightError as e:
        logger.warning("Upstream portal scrape failed for provider %s: %s", provider.id, e)
        return False, "scrape_failed"

    return True, {"status": _parse_status(row_text), "expiry": _parse_expiry(row_text)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_upstream_portal.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add upstream_portal.py tests/test_upstream_portal.py
git commit -m "feat: add read-only upstream portal status adapter (Terra/PROradius)"
```

---

## Task 5: Backend endpoint + serialization

**Files:**
- Modify: `app.py`
- Test: `tests/test_upstream_status_sync.py`

**Interfaces:**
- Consumes: `upstream_portal.get_subscriber_status(provider, username)` (Task 4), `_compute_upstream_drift(customer)` (Task 3).
- Produces: `POST /api/customers/<int:customer_id>/upstream-status-sync`, returning `{'ok': True, 'upstream_last_status': str, 'upstream_actual_expiry': 'YYYY-MM-DD'|None, 'upstream_last_synced_at': 'YYYY-MM-DD HH:MM:SS', 'upstream_drift': dict|None}` on success (200), or `{'ok': False, 'error': <reason string from Task 4>}` on a scrape failure (502), or `{'error': <message>}` on a bad link (400/404).

- [ ] **Step 1: Add the module import**

In `app.py`, change this line (currently at line 88):

```python
import mikrotik
```

to:

```python
import mikrotik
import upstream_portal
```

- [ ] **Step 2: Write the failing endpoint tests**

Create `tests/test_upstream_status_sync.py`:

```python
from datetime import datetime, timedelta

import app as appmod
from tests.conftest import make_tenant


def _setup_bridged_customer(client, hdr, upstream_username="cust1"):
    plan_id = client.post("/api/subscription_plans", headers=hdr,
                          json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    provider_id = client.post("/api/upstream-providers", headers=hdr,
                              json={"name": "Terra", "product": "proradius",
                                    "portal_url": "https://acppro.terra.net.lb/login/",
                                    "portal_username": "reseller1", "portal_password": "pw"}
                              ).get_json()["provider"]["id"]
    customer_resp = client.post("/api/customers", headers=hdr,
                                json={"name": "Cust", "phone": "1", "address": "a",
                                      "subscription_plan_id": plan_id,
                                      "subscription_start_date": "2026-01-01",
                                      "upstream_provider_id": provider_id,
                                      "upstream_username": upstream_username})
    return customer_resp.get_json()["customer"]["id"] if customer_resp.status_code in (200, 201) else None


def test_sync_requires_upstream_link(client):
    hdr = make_tenant(client, "Biz A", "a_admin")
    plan_id = client.post("/api/subscription_plans", headers=hdr,
                          json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    customer_id = client.post("/api/customers", headers=hdr,
                              json={"name": "Cust", "phone": "1", "address": "a",
                                    "subscription_plan_id": plan_id,
                                    "subscription_start_date": "2026-01-01"}).get_json()["customer"]["id"]

    resp = client.post(f"/api/customers/{customer_id}/upstream-status-sync", headers=hdr)

    assert resp.status_code == 400


def test_sync_success_persists_fields_and_returns_drift(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz B", "b_admin")
    customer_id = _setup_bridged_customer(client, hdr)
    assert customer_id is not None

    with app.app_context():
        customer = appmod.Customer.query.get(customer_id)
        customer.subscription_expiry_date = datetime(2026, 9, 1)
        appmod.db.session.commit()

    monkeypatch.setattr(
        appmod.upstream_portal, "get_subscriber_status",
        lambda provider, username: (True, {"status": "online", "expiry": datetime(2026, 9, 5)}),
    )

    resp = client.post(f"/api/customers/{customer_id}/upstream-status-sync", headers=hdr)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["upstream_last_status"] == "online"
    assert body["upstream_actual_expiry"] == "2026-09-05"
    assert body["upstream_drift"] == {"severity": "info", "days": 4}

    with app.app_context():
        customer = appmod.Customer.query.get(customer_id)
        assert customer.upstream_last_status == "online"
        assert customer.upstream_last_synced_at is not None


def test_sync_failure_returns_502_and_leaves_fields_untouched(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz C", "c_admin")
    customer_id = _setup_bridged_customer(client, hdr)
    assert customer_id is not None

    monkeypatch.setattr(appmod.upstream_portal, "get_subscriber_status",
                        lambda provider, username: (False, "auth_failed"))

    resp = client.post(f"/api/customers/{customer_id}/upstream-status-sync", headers=hdr)

    assert resp.status_code == 502
    assert resp.get_json() == {"ok": False, "error": "auth_failed"}

    with app.app_context():
        customer = appmod.Customer.query.get(customer_id)
        assert customer.upstream_last_status is None
        assert customer.upstream_last_synced_at is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_upstream_status_sync.py -v`
Expected: FAIL with 404s (the route doesn't exist yet).

- [ ] **Step 4: Add the endpoint**

In `app.py`, insert this new section right after `record_upstream_renewal_cost` ends (it currently ends at line 6378 with `return jsonify({'error': str(e)}), 400`, followed by two blank lines and then `@app.route('/api/mikrotik-servers', ...)` at line 6381):

```python

# --- Read-only status sync against a customer's upstream portal account
# (Terra/PROradius first -- see
# docs/superpowers/specs/2026-08-22-upstream-status-sync-design.md). Never
# clicks anything on the portal, staff-triggered only, one customer at a
# time -- no scheduler calls this.

@app.route('/api/customers/<int:customer_id>/upstream-status-sync', methods=['POST'])
@jwt_required()
def sync_customer_upstream_status(customer_id):
    customer = tenant_query(Customer).filter_by(id=customer_id).first()
    if not customer:
        return jsonify({'message': 'Customer not found!'}), 404
    if not customer.upstream_provider_id or not customer.upstream_username:
        return jsonify({'error': 'Customer is not linked to an Upstream Provider.'}), 400
    provider = tenant_query(UpstreamProvider).filter_by(id=customer.upstream_provider_id).first()
    if not provider:
        return jsonify({'error': 'Linked Upstream Provider not found.'}), 404

    ok, result = upstream_portal.get_subscriber_status(provider, customer.upstream_username)
    if not ok:
        return jsonify({'ok': False, 'error': result}), 502

    customer.upstream_last_status = result['status']
    customer.upstream_actual_expiry = result['expiry']
    customer.upstream_last_synced_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        'ok': True,
        'upstream_last_status': customer.upstream_last_status,
        'upstream_actual_expiry': customer.upstream_actual_expiry.strftime('%Y-%m-%d') if customer.upstream_actual_expiry else None,
        'upstream_last_synced_at': customer.upstream_last_synced_at.strftime('%Y-%m-%d %H:%M:%S'),
        'upstream_drift': _compute_upstream_drift(customer),
    }), 200

```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_upstream_status_sync.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Surface the fields in the customer list response**

In `app.py`, the customer list endpoint currently builds this dict (around line 1835-1853):

```python
            customer_dict = {
                'id': c.id,
                'name': c.name,
                'phone': c.phone,
                'address': c.address,
                'subscription_plan_id': c.subscription_plan_id,
                'subscription_start_date': c.subscription_start_date.strftime('%Y-%m-%d'),
                'subscription_expiry_date': c.subscription_expiry_date.strftime('%Y-%m-%d') if c.subscription_expiry_date else None,
                'is_subscription_active': c.is_subscription_active,
                'balance': float(c.balance) if c.balance else 0.0,
                'discount': float(c.discount) if c.discount else 0.0,
                'cost_override': float(c.cost_override) if c.cost_override is not None else None,
                'reseller_id': c.reseller_id,
                'upstream_provider_id': c.upstream_provider_id,
                'upstream_username': c.upstream_username,
                'mikrotik_server_id': c.mikrotik_server_id,
                'pppoe_username': c.pppoe_username,
                'subscription_plan': c.subscription_plan.to_dict() if c.subscription_plan else None
            }
```

Add the three new fields plus the computed drift, right after `'upstream_username': c.upstream_username,`:

```python
            customer_dict = {
                'id': c.id,
                'name': c.name,
                'phone': c.phone,
                'address': c.address,
                'subscription_plan_id': c.subscription_plan_id,
                'subscription_start_date': c.subscription_start_date.strftime('%Y-%m-%d'),
                'subscription_expiry_date': c.subscription_expiry_date.strftime('%Y-%m-%d') if c.subscription_expiry_date else None,
                'is_subscription_active': c.is_subscription_active,
                'balance': float(c.balance) if c.balance else 0.0,
                'discount': float(c.discount) if c.discount else 0.0,
                'cost_override': float(c.cost_override) if c.cost_override is not None else None,
                'reseller_id': c.reseller_id,
                'upstream_provider_id': c.upstream_provider_id,
                'upstream_username': c.upstream_username,
                'upstream_actual_expiry': c.upstream_actual_expiry.strftime('%Y-%m-%d') if c.upstream_actual_expiry else None,
                'upstream_last_status': c.upstream_last_status,
                'upstream_last_synced_at': c.upstream_last_synced_at.strftime('%Y-%m-%d %H:%M:%S') if c.upstream_last_synced_at else None,
                'upstream_drift': _compute_upstream_drift(c),
                'mikrotik_server_id': c.mikrotik_server_id,
                'pppoe_username': c.pppoe_username,
                'subscription_plan': c.subscription_plan.to_dict() if c.subscription_plan else None
            }
```

- [ ] **Step 7: Write a test for the list serialization**

Add to `tests/test_upstream_status_sync.py`:

```python
def test_customer_list_includes_upstream_drift(app, client, monkeypatch):
    hdr = make_tenant(client, "Biz D", "d_admin")
    customer_id = _setup_bridged_customer(client, hdr)
    assert customer_id is not None

    with app.app_context():
        customer = appmod.Customer.query.get(customer_id)
        customer.subscription_expiry_date = datetime(2026, 9, 1)
        customer.upstream_actual_expiry = datetime(2026, 8, 30)
        customer.upstream_last_status = "expired"
        appmod.db.session.commit()

    resp = client.get("/api/customers", headers=hdr)

    assert resp.status_code == 200
    listed = [c for c in resp.get_json()["customers"] if c["id"] == customer_id][0]
    assert listed["upstream_last_status"] == "expired"
    assert listed["upstream_drift"] == {"severity": "alert", "days": 2}
```

- [ ] **Step 8: Run all tests for this feature**

Run: `pytest tests/test_upstream_status_sync.py tests/test_upstream_drift.py tests/test_upstream_portal.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add app.py tests/test_upstream_status_sync.py
git commit -m "feat: add upstream-status-sync endpoint and surface drift in customer list"
```

---

## Task 6: Frontend -- Refresh Upstream Status panel

**Files:**
- Modify: `frontend/src/context/AppContext.js`
- Modify: `frontend/src/components/SubscriptionsView.js`

**Interfaces:**
- Consumes: `POST /api/customers/<id>/upstream-status-sync` (Task 5), response shape `{'ok': bool, 'upstream_last_status'?: str, 'upstream_actual_expiry'?: str|null, 'upstream_last_synced_at'?: str, 'upstream_drift'?: {'severity': 'info'|'alert', 'days': int}|null, 'error'?: str}`.

- [ ] **Step 1: Add the API method**

In `frontend/src/context/AppContext.js`, this block currently exists (around line 141-144):

```js
    // Customer <-> Mikrotik live actions (staff-confirmed only, see spec)
    fetchCustomerMikrotikStatus: (customerId) => api.get(`/customers/${customerId}/mikrotik-status`),
    suspendCustomerMikrotik: (customerId) => api.post(`/customers/${customerId}/mikrotik-suspend`),
    unsuspendCustomerMikrotik: (customerId) => api.post(`/customers/${customerId}/mikrotik-unsuspend`),
```

Add right after it:

```js
    // Customer <-> Upstream Portal read-only status sync (staff-triggered, see spec)
    syncCustomerUpstreamStatus: (customerId) => api.post(`/customers/${customerId}/upstream-status-sync`),
```

- [ ] **Step 2: Add component state**

In `frontend/src/components/SubscriptionsView.js`, this block currently exists (around line 230-232):

```js
    const [mikrotikStatus, setMikrotikStatus] = useState(null);
    const [mikrotikStatusLoading, setMikrotikStatusLoading] = useState(false);
    const [mikrotikActionLoading, setMikrotikActionLoading] = useState(false);
```

Add right after it:

```js
    const [upstreamSyncStatus, setUpstreamSyncStatus] = useState(null);
    const [upstreamSyncLoading, setUpstreamSyncLoading] = useState(false);
```

- [ ] **Step 3: Reset stale status when the dialog opens for a different customer**

This block currently exists (around line 472-481):

```js
    // Network Status panel (Concept B) -- fetch fresh whenever the edit dialog
    // opens for a Mikrotik-linked customer; never fires on its own otherwise.
    useEffect(() => {
        if (editDialogOpen && editingCustomer?.mikrotik_server_id && editingCustomer?.pppoe_username) {
            fetchMikrotikStatus(editingCustomer.id);
        } else {
            setMikrotikStatus(null);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [editDialogOpen, editingCustomer?.id]);
```

Add right after it (the upstream sync is a slower live browser check, not a cheap API call like Mikrotik's -- it must stay a manual click, never auto-fired on dialog open, but any stale result from a *previous* customer must still be cleared):

```js
    // Upstream status sync (Concept A) is manual-only -- a real headless-browser
    // check takes several seconds, unlike the instant Mikrotik API call above, so
    // it never auto-fires on dialog open. Only clear a stale result from whatever
    // customer was previously open.
    useEffect(() => {
        setUpstreamSyncStatus(null);
    }, [editDialogOpen, editingCustomer?.id]);

    const fetchUpstreamStatus = async (customerId) => {
        setUpstreamSyncLoading(true);
        try {
            const response = await apiService.syncCustomerUpstreamStatus(customerId);
            setUpstreamSyncStatus(response.data);
        } catch (error) {
            setUpstreamSyncStatus({ ok: false, error: error.response?.data?.error || error.response?.data?.message || 'Failed to sync status' });
        } finally {
            setUpstreamSyncLoading(false);
        }
    };
```

- [ ] **Step 4: Add the UI panel**

This block currently exists (around line 1105-1142), ending the `Grid container` for the edit dialog's fields:

```jsx
                        {businessSettings?.network_mode === 'local_mikrotik' && editingCustomer?.mikrotik_server_id && editingCustomer?.pppoe_username && (
                            <Grid item xs={12}>
                                <Box sx={{ p: 2, borderRadius: '12px', border: `1px solid ${alpha(theme.palette.divider, 0.15)}`, bgcolor: '#f8fafc' }}>
                                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 1 }}>
                                        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Network Status (Mikrotik)</Typography>
                                        <Button size="small" onClick={() => fetchMikrotikStatus(editingCustomer.id)} disabled={mikrotikStatusLoading}>
                                            {mikrotikStatusLoading ? <CircularProgress size={16} /> : 'Refresh'}
                                        </Button>
                                    </Box>
                                    {mikrotikStatus ? (
                                        <Box sx={{ mt: 1, display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
                                            <Chip
                                                size="small"
                                                label={mikrotikStatus.secret_error ? `Error: ${mikrotikStatus.secret_error}` : `Secret: ${mikrotikStatus.secret_status || 'unknown'}`}
                                                color={mikrotikStatus.secret_status === 'enabled' ? 'success' : mikrotikStatus.secret_status === 'disabled' ? 'error' : 'default'}
                                            />
                                            <Chip
                                                size="small"
                                                variant="outlined"
                                                label={mikrotikStatus.active_session ? 'Currently connected' : 'Not connected'}
                                            />
                                            <Button size="small" variant="outlined" color="error" disabled={mikrotikActionLoading}
                                                onClick={() => handleMikrotikAction(editingCustomer.id, 'suspend')}>
                                                Suspend
                                            </Button>
                                            <Button size="small" variant="outlined" color="success" disabled={mikrotikActionLoading}
                                                onClick={() => handleMikrotikAction(editingCustomer.id, 'unsuspend')}>
                                                Unsuspend
                                            </Button>
                                        </Box>
                                    ) : (
                                        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                                            {mikrotikStatusLoading ? 'Checking…' : 'No status loaded yet.'}
                                        </Typography>
                                    )}
                                </Box>
                            </Grid>
                        )}
                    </Grid>
                </DialogContent>
```

Insert a new block for the upstream panel right after the Mikrotik panel's closing `)}` and before `</Grid>`:

```jsx
                        {businessSettings?.network_mode === 'local_mikrotik' && editingCustomer?.mikrotik_server_id && editingCustomer?.pppoe_username && (
                            <Grid item xs={12}>
                                <Box sx={{ p: 2, borderRadius: '12px', border: `1px solid ${alpha(theme.palette.divider, 0.15)}`, bgcolor: '#f8fafc' }}>
                                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 1 }}>
                                        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Network Status (Mikrotik)</Typography>
                                        <Button size="small" onClick={() => fetchMikrotikStatus(editingCustomer.id)} disabled={mikrotikStatusLoading}>
                                            {mikrotikStatusLoading ? <CircularProgress size={16} /> : 'Refresh'}
                                        </Button>
                                    </Box>
                                    {mikrotikStatus ? (
                                        <Box sx={{ mt: 1, display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
                                            <Chip
                                                size="small"
                                                label={mikrotikStatus.secret_error ? `Error: ${mikrotikStatus.secret_error}` : `Secret: ${mikrotikStatus.secret_status || 'unknown'}`}
                                                color={mikrotikStatus.secret_status === 'enabled' ? 'success' : mikrotikStatus.secret_status === 'disabled' ? 'error' : 'default'}
                                            />
                                            <Chip
                                                size="small"
                                                variant="outlined"
                                                label={mikrotikStatus.active_session ? 'Currently connected' : 'Not connected'}
                                            />
                                            <Button size="small" variant="outlined" color="error" disabled={mikrotikActionLoading}
                                                onClick={() => handleMikrotikAction(editingCustomer.id, 'suspend')}>
                                                Suspend
                                            </Button>
                                            <Button size="small" variant="outlined" color="success" disabled={mikrotikActionLoading}
                                                onClick={() => handleMikrotikAction(editingCustomer.id, 'unsuspend')}>
                                                Unsuspend
                                            </Button>
                                        </Box>
                                    ) : (
                                        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                                            {mikrotikStatusLoading ? 'Checking…' : 'No status loaded yet.'}
                                        </Typography>
                                    )}
                                </Box>
                            </Grid>
                        )}
                        {businessSettings?.network_mode === 'upstream_bridge' && editingCustomer?.upstream_provider_id && editingCustomer?.upstream_username && (
                            <Grid item xs={12}>
                                <Box sx={{ p: 2, borderRadius: '12px', border: `1px solid ${alpha(theme.palette.divider, 0.15)}`, bgcolor: '#f8fafc' }}>
                                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 1 }}>
                                        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Network Status (Upstream Portal)</Typography>
                                        <Button size="small" onClick={() => fetchUpstreamStatus(editingCustomer.id)} disabled={upstreamSyncLoading}>
                                            {upstreamSyncLoading ? <CircularProgress size={16} /> : 'Refresh Upstream Status'}
                                        </Button>
                                    </Box>
                                    {upstreamSyncStatus ? (
                                        upstreamSyncStatus.ok === false ? (
                                            <Typography variant="body2" color="error" sx={{ mt: 1 }}>
                                                {upstreamSyncStatus.error || 'Sync failed'}
                                            </Typography>
                                        ) : (
                                            <Box sx={{ mt: 1, display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
                                                <Chip
                                                    size="small"
                                                    label={`Portal status: ${upstreamSyncStatus.upstream_last_status || 'unknown'}`}
                                                    color={
                                                        upstreamSyncStatus.upstream_last_status === 'online' ? 'success' :
                                                        upstreamSyncStatus.upstream_last_status === 'offline' ? 'error' :
                                                        upstreamSyncStatus.upstream_last_status === 'expired' ? 'warning' : 'default'
                                                    }
                                                />
                                                <Chip
                                                    size="small"
                                                    variant="outlined"
                                                    label={`As of ${upstreamSyncStatus.upstream_last_synced_at || 'now'}`}
                                                />
                                                {upstreamSyncStatus.upstream_drift && (
                                                    <Chip
                                                        size="small"
                                                        color={upstreamSyncStatus.upstream_drift.severity === 'alert' ? 'error' : 'info'}
                                                        label={
                                                            upstreamSyncStatus.upstream_drift.severity === 'alert'
                                                                ? `⚠ Upstream expires ${upstreamSyncStatus.upstream_drift.days} day(s) before ServiceBills`
                                                                : `Upstream has ${upstreamSyncStatus.upstream_drift.days} extra day(s)`
                                                        }
                                                    />
                                                )}
                                            </Box>
                                        )
                                    ) : (
                                        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                                            {upstreamSyncLoading ? 'Checking…' : 'No status loaded yet.'}
                                        </Typography>
                                    )}
                                </Box>
                            </Grid>
                        )}
                    </Grid>
                </DialogContent>
```

- [ ] **Step 5: Build the frontend to check for syntax errors**

Run:
```bash
cd frontend && npm run build
```
Expected: builds successfully with no new errors (existing warnings from before this change are fine).

- [ ] **Step 6: Manual verification in the browser**

Start the dev server (or the built app), open a tenant with `network_mode = 'upstream_bridge'`, edit a customer linked to an `UpstreamProvider` with `upstream_username` set, and confirm the "Network Status (Upstream Portal)" panel appears with a "Refresh Upstream Status" button. Clicking it will fail at this point (Task 7/8 haven't confirmed real selectors yet) -- confirm it shows the error message from the backend rather than crashing the page.

- [ ] **Step 7: Commit**

```bash
cd .. && git add frontend/src/context/AppContext.js frontend/src/components/SubscriptionsView.js
git commit -m "feat: add Refresh Upstream Status panel to Subscriptions edit dialog"
```

---

## Task 7: Live discovery against Terra

**Files:** none modified -- this task produces findings consumed by Task 8, not code.

This task requires access to a real Terra (PROradius) account and must be run with the user, since Claude must never be given or enter the portal password directly.

- [ ] **Step 1: Open Terra's login page**

Use the project's Browser tool (already available in this environment) to navigate to `https://acppro.terra.net.lb/login/`.

- [ ] **Step 2: Have the user log in**

Ask the user to enter their own Terra credentials into the browser themselves (never type or request the password directly). Once logged in, confirm with the user that you're on the subscriber list / dashboard page.

- [ ] **Step 3: Inspect the login page's real markup**

Before the user logs in (or by checking the page's HTML via view-source together with the user), use `read_page` to record: the actual `name`/`id`/CSS selector for the username field, the password field, and the submit button, and what element reliably indicates "logged in" (a logout link's text, a dashboard heading, etc.).

- [ ] **Step 4: Inspect one subscriber row's real markup**

With the user's help, find a specific test subscriber in the list and use `read_page` / `get_page_text` to record:
- The table/row structure actually used (is it a `<table>`, or a div-based grid?).
- How status is actually represented -- literal text ("Online"/"Offline"/"Expired"), a CSS class name on a colored element (e.g. `class="status-dot green"`), or an icon with no text at all.
- The exact text format the expiry date appears in (e.g. `"2026-09-01"`, `"01/09/2026"`, `"Sep 1, 2026"`).

- [ ] **Step 5: Write up the findings**

Produce a short findings note (in the PR description or a scratch file, not committed) covering the four items from Steps 3-4, for Task 8 to apply. If status is conveyed by color/class rather than text, flag this explicitly -- Task 8's approach to `_parse_status` will differ (reading a class/style attribute instead of matching text).

---

## Task 8: Apply live findings and verify against the real portal

**Files:**
- Modify: `upstream_portal.py`

**Interfaces:** none new -- this task only corrects the constants/parsing from Task 4 using Task 7's findings; `get_subscriber_status`'s signature and return contract do not change.

- [ ] **Step 1: Update the selector constants**

In `upstream_portal.py`, replace `LOGIN_USERNAME_SELECTOR`, `LOGIN_PASSWORD_SELECTOR`, `LOGIN_SUBMIT_SELECTOR`, `LOGIN_SUCCESS_SELECTOR`, and `SUBSCRIBER_TABLE_SELECTOR` with the real values recorded in Task 7, Steps 3-4.

- [ ] **Step 2: Update status parsing if needed**

If Task 7 found that status is conveyed by a CSS class or attribute rather than visible text, replace `_parse_status`'s text-matching approach with one that reads that class/attribute from the row (e.g. via `row.locator(".status-dot").get_attribute("class")`), keeping the same three-way return value (`'online'`/`'offline'`/`'expired'`, falling back to `'unknown'`). If Task 7 confirmed text-based status works as originally guessed, no change is needed here.

- [ ] **Step 3: Update date parsing if needed**

If Task 7's real date format isn't already handled correctly by `dateutil.parser.parse(text, fuzzy=True)` (it handles most common formats, including `DD/MM/YYYY` vs `MM/DD/YYYY` ambiguity via the `dayfirst` argument if needed), adjust `_parse_expiry` accordingly -- e.g. add `dayfirst=True` if Terra uses day-first dates (common in Lebanon).

- [ ] **Step 4: Re-run the unit test suite**

Run: `pytest tests/test_upstream_portal.py -v`
Expected: all tests still PASS -- they mock the Page object directly, so changing selector *values* (not the module's structure) shouldn't break them. If a test does break, it means Step 1-3 changed the module's logic/shape, not just a constant's value -- review whether that's actually necessary before proceeding.

- [ ] **Step 5: One supervised real smoke test**

With the user watching: link one real (test) customer to Terra with their actual `upstream_username`, click "Refresh Upstream Status" in the running app, and confirm the returned status/expiry match what the user can see by eye on Terra's own portal for that subscriber. This is the acceptance check for the whole feature -- do not consider it done until this succeeds against the real site.

- [ ] **Step 6: Commit**

```bash
git add upstream_portal.py
git commit -m "fix: confirm Terra selectors and status parsing against the live portal"
```

---

## Self-Review Notes

- **Spec coverage:** Task 1 covers the Playwright infra note; Task 2 covers the additive migration + backup reminder; Task 3 covers drift detection with both severities; Task 4 covers the adapter with the `(ok, value)` contract and all four failure reasons; Task 5 covers the endpoint contract and serialization; Task 6 covers the two-indicator UI; Tasks 7-8 cover the "confirm against the real Terra site before considering it done" requirement. The "Later phases" section of the spec (bulk sync, radiusnew, Renew automation, AI agent) is explicitly out of scope for this plan, as agreed.
- **Type consistency:** `get_subscriber_status` is called the same way in Task 4's tests and Task 5's endpoint (`upstream_portal.get_subscriber_status(provider, customer.upstream_username)` returning `(ok, value)`). `_compute_upstream_drift` is called identically in Task 3's tests, Task 5's endpoint, and Task 5's list serialization.
- **No placeholders:** every step has complete code, except the selector constants in Task 4, which are explicitly documented as best-guess values with a named follow-up task (7-8) that corrects them via a concrete, bounded process -- not a vague "TODO" left for later.
