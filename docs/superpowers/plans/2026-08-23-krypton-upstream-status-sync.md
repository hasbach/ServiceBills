# Krypton Upstream Portal Status Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing read-only upstream status-sync feature (currently PROradius-only, covering Terra and Northern Telecom) to the second confirmed upstream product family, Krypton (MyISP, Smart Networks, Wise-ISP), reusing the same `Customer` fields, endpoint, and UI already shipped.

**Architecture:** A new sibling adapter module, `upstream_portal_krypton.py`, with the exact same never-raise `(ok, value)` contract as the existing `upstream_portal.py` (PROradius) and `mikrotik.py`. `app.py`'s existing sync endpoint dispatches to one adapter or the other based on `UpstreamProvider.product`. No database migration -- `product` is already an unconstrained string column, and the `Customer` status/expiry fields are reused as-is.

**Tech Stack:** Python, Playwright (already a dependency), pytest with fake-Playwright-double unit tests (no real browser or real portal in tests).

## Global Constraints

- Every public function in `upstream_portal_krypton.py` returns `(ok: bool, value)` and never raises -- a scrape failure must never block or crash a billing-side request. (Design spec, "Architecture".)
- Read-only only: never click Renew/Block/Unblock or any other mutating action on the portal -- only login, fill the username filter, and read cell text. (Design spec, "Explicit non-goals".)
- **Never attempt to solve, bypass, or detect the CAPTCHA.** If login does not reach the subscriber list within the timeout budget, for any reason including an unexpected CAPTCHA challenge, report `auth_failed` -- do not special-case or distinguish this from a wrong password. (Design spec, "Explicit non-goals" -- this is a hard boundary, not a v1 simplification.)
- Confirmed live selectors (do not re-derive, do not guess differently): login form is `input[name="login_username"]` / `input[name="login_password"]` / a `<button type="button">` with the exact text "Log In" (JS-driven AJAX login, not a native form submit -- must use `page.click`, not Enter-key submission). The subscriber table's id is `#example-1`, confirmed identical on both MyISP and Smart Networks. (Design spec, "Login flow".)
- Krypton's subscriber table has a user-customizable "Column visibility" toggle (confirmed live: 90 defined columns, only 35 rendered by default) -- column positions for Username/Status/Expiry Date **must** be resolved by header text at runtime, never hardcoded as a fixed index. (Design spec, "Subscriber list".)
- The subscriber list uses real server-side search (confirmed live via `DataTable().settings()[0].oFeatures.bServerSide === true` on both portals) -- filling the username filter and waiting a fixed delay before searching for the row is a known, previously-shipped bug (see `upstream_portal.py`'s `_find_subscriber_row` history) -- must actively wait for the filtered row to appear instead. (Design spec, "Subscriber list"; Global Constraint carried over from the PROradius incident.)
- Status vocabulary is `'online' | 'offline' | 'expired' | 'near_expiry' | 'blocked' | 'quota_exceeded' | 'unknown'` -- three new values beyond the PROradius adapter's set, additive-only, no schema change. `"Active"` (a value that appears in the portal's own filter dropdown) is deliberately left unmapped, falling to `'unknown'` -- its real meaning/color was never confirmed. (Design spec, "Status parsing".)
- No migration. `UpstreamProvider.product` gains `'krypton'` as a new accepted string value alongside the existing `'proradius' | 'radiusnew' | 'manual'`. (Design spec, "Data model".)

---

## File Structure

- **Create:** `upstream_portal_krypton.py` -- the Krypton adapter (login, dynamic column resolution, row lookup, status/expiry parsing).
- **Create:** `tests/test_upstream_portal_krypton.py` -- its unit tests, with fresh fake-Playwright doubles (not reused from `tests/test_upstream_portal.py` -- Krypton's DOM shape, particularly the visible-columns-only indexing and the `page.evaluate` column-resolution call, is different enough to need its own fakes, same precedent as PROradius getting its own).
- **Modify:** `app.py` -- import the new module; dispatch `sync_customer_upstream_status` on `provider.product`.
- **Modify:** `tests/test_upstream_status_sync.py` -- add a test proving the dispatch actually calls the Krypton module for a `product='krypton'` provider (existing tests only exercise the PROradius path).
- **Modify:** `frontend/src/components/UpstreamProviderManagementView.js` -- add `krypton` to `PRODUCT_LABELS` and the Product dropdown.
- **Modify:** `frontend/src/components/SubscriptionsView.js` -- extend `getUpstreamStatusColor` with `blocked` / `near_expiry` / `quota_exceeded`.

---

### Task 1: `upstream_portal_krypton.py` adapter module

**Files:**
- Create: `upstream_portal_krypton.py`
- Create: `tests/test_upstream_portal_krypton.py`

**Interfaces:**
- Produces: `get_subscriber_status(provider, username)` -> `(True, {'status': 'online'|'offline'|'expired'|'near_expiry'|'blocked'|'quota_exceeded'|'unknown', 'expiry': datetime|None})` on success, `(False, reason)` on failure where `reason` is one of `'auth_failed'`, `'not_found'`, `'timeout'`, `'scrape_failed'`. Never raises. `provider` is a duck-typed object with `.id`, `.portal_url`, `.portal_username`, `.portal_password` (matches the existing `UpstreamProvider` model and the `upstream_portal.py` adapter's own `provider` parameter -- no new type needed).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_upstream_portal_krypton.py`:

```python
"""Tests for upstream_portal_krypton.py -- no real browser or real portal
involved. `sync_playwright` is monkeypatched to a small fake Playwright/
Browser/Page double. These fakes model the real Krypton (MyISP / Smart
Networks) page structure confirmed via live discovery (2026-08-23):
status and expiry are plain text (not a CSS class like PROradius), column
positions must be resolved by header text at runtime because Krypton has a
user-customizable "Column visibility" toggle (confirmed live: only a subset
of defined columns render at once), and the subscriber list uses real
server-side search (a genuine network round trip on filter, not an instant
client-side operation)."""
import types

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import upstream_portal_krypton as krypton


def make_provider(id=1, portal_url="https://example.test/login.php", portal_username="u", portal_password="p"):
    return types.SimpleNamespace(id=id, portal_url=portal_url, portal_username=portal_username, portal_password=portal_password)


# Confirmed live default visible-column order (a subset of the real
# portal's 90 defined columns -- only what this adapter reads matters):
# Action, [checkbox], Name, Username, Status, Uptime, Address, IP,
# D. Quota, M. Quota, Service, Expiry Date, ...
DEFAULT_COLUMN_INDICES = {"Username": 3, "Status": 4, "Expiry Date": 11}


def make_cells(username="user1", status_text="Online", expiry="2026-09-06 12:30:00", n_cols=12):
    # A row wide enough to cover the default column positions; unused
    # positions are harmless placeholders.
    cells = ["placeholder"] * n_cols
    cells[3] = username
    cells[4] = status_text
    cells[11] = expiry
    return cells


class FakeCell:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class FakeCellsLocator:
    """Models `row.locator("td:visible")` -- only ever called with this
    exact selector by the code under test."""

    def __init__(self, cell_texts):
        self._cell_texts = cell_texts

    def count(self):
        return len(self._cell_texts)

    def nth(self, i):
        if i >= len(self._cell_texts):
            return FakeCell("")
        return FakeCell(self._cell_texts[i])


class FakeRowLocator:
    def __init__(self, cell_texts):
        self._cell_texts = cell_texts

    def locator(self, selector):
        assert selector == "td:visible", f"unexpected row-level selector {selector!r}"
        return FakeCellsLocator(self._cell_texts)


class FakeRowsLocator:
    """Models `page.locator(f"{TABLE} tbody tr", has_text=...)`."""

    def __init__(self, rows, has_text=None):
        def row_text(r):
            return " ".join(r._cell_texts)
        if has_text is not None:
            self._rows = [r for r in rows if has_text in row_text(r)]
        else:
            self._rows = list(rows)

    def count(self):
        return len(self._rows)

    def nth(self, i):
        return self._rows[i]


class FakeFilterInput:
    def __init__(self, holder, key):
        self._holder = holder
        self._key = key

    def fill(self, value):
        self._holder[self._key] = value


class FakeHeaderCell:
    """Models `page.locator('thead th:visible').nth(i)` -- only its
    `.locator('input')` (the per-column filter box) is used by the code
    under test."""

    def __init__(self, filled_holder, index):
        self._filled_holder = filled_holder
        self._index = index

    def locator(self, selector):
        assert selector == "input", f"unexpected header-cell selector {selector!r}"
        return FakeFilterInput(self._filled_holder, self._index)


class FakeVisibleHeadersLocator:
    def __init__(self, filled_holder):
        self._filled_holder = filled_holder

    def nth(self, i):
        return FakeHeaderCell(self._filled_holder, i)


class FakePage:
    def __init__(self, row_found=True, cell_texts=None, rows=None, login_succeeds=True,
                 goto_raises=None, column_indices=None, filtered_wait_times_out=False):
        self._login_succeeds = login_succeeds
        self._goto_raises = goto_raises
        self._filtered_wait_times_out = filtered_wait_times_out
        self._column_indices = column_indices if column_indices is not None else dict(DEFAULT_COLUMN_INDICES)
        self.filled = {}
        self.clicked = []
        self.goto_calls = []
        if rows is not None:
            self._rows = rows
        elif row_found:
            self._rows = [FakeRowLocator(cell_texts if cell_texts is not None else make_cells())]
        else:
            self._rows = []

    def set_default_timeout(self, ms):
        pass

    def goto(self, url, timeout=None):
        self.goto_calls.append(url)
        if self._goto_raises:
            raise self._goto_raises

    def fill(self, selector, value):
        self.filled[selector] = value

    def click(self, selector):
        self.clicked.append(selector)

    def wait_for_selector(self, selector, timeout=None):
        if not self._login_succeeds:
            raise PlaywrightTimeoutError("timed out waiting for subscriber list")
        if self._filtered_wait_times_out and "has-text" in selector:
            raise PlaywrightTimeoutError("timed out waiting for filtered row")

    def evaluate(self, script, arg=None):
        # The real implementation resolves column indices via one JS
        # evaluation over the live DOM; the fake just returns the
        # pre-configured indices for whichever header names are asked for,
        # in the same order, mirroring the real function's (name -> index)
        # contract without needing to interpret the JS string itself.
        return [self._column_indices.get(name, -1) for name in arg]

    def locator(self, selector, has_text=None):
        if selector == "thead th:visible":
            return FakeVisibleHeadersLocator(self.filled)
        return FakeRowsLocator(self._rows, has_text=has_text)


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
    monkeypatch.setattr(krypton, "sync_playwright", lambda: FakeSyncPlaywrightCM(page))


# --- Success cases: status-text mapping ---

def test_get_subscriber_status_online(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Online"))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert ok is True
    assert value["status"] == "online"
    assert value["expiry"].year == 2026 and value["expiry"].month == 9 and value["expiry"].day == 6


def test_get_subscriber_status_offline_over_2_days_maps_to_offline(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Offline > 2 days"))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "offline")


def test_get_subscriber_status_expired(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Expired"))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "expired")


def test_get_subscriber_status_near_expiry(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Near Expiry"))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "near_expiry")


def test_get_subscriber_status_blocked(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Blocked"))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "blocked")


def test_get_subscriber_status_quota_exceeded(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Q-Exceeded"))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "quota_exceeded")


def test_get_subscriber_status_active_is_unknown(monkeypatch):
    # "Active" is deliberately unmapped -- its real meaning/color was never
    # confirmed live, unlike the other seven vocabulary values.
    page = FakePage(cell_texts=make_cells(status_text="Active"))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "unknown")


# --- Column resolution ---

def test_reads_correct_columns_when_visible_order_differs_from_default(monkeypatch):
    # Proves the adapter resolves columns by header text, not a hardcoded
    # index -- a tenant with a customized "Column visibility" view would
    # have a different order than the default confirmed live. Username and
    # Status are swapped here relative to DEFAULT_COLUMN_INDICES, and the
    # row data is shaped to match: if the code hardcoded index 3 for
    # Username, it would read "Online" as the username and fail to find
    # "user1" at all.
    custom_indices = {"Username": 4, "Status": 3, "Expiry Date": 11}
    cells = ["placeholder"] * 12
    cells[4] = "user1"          # Username now at index 4
    cells[3] = "Online"         # Status now at index 3
    cells[11] = "2026-09-06 12:30:00"
    page = FakePage(cell_texts=cells, column_indices=custom_indices)
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert ok is True
    assert value["status"] == "online"


def test_missing_required_column_is_scrape_failed(monkeypatch):
    # The portal's page format changed enough that a required column
    # (Username) can no longer be found by header text at all.
    page = FakePage(column_indices={"Username": -1, "Status": 4, "Expiry Date": 11})
    patch_playwright(monkeypatch, page)

    ok, reason = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "scrape_failed")


# --- Login ---

def test_login_fills_credentials_and_clicks_log_in(monkeypatch):
    page = FakePage()
    patch_playwright(monkeypatch, page)

    krypton.get_subscriber_status(make_provider(portal_username="alice", portal_password="s3cret"), "user1")

    assert page.filled[krypton.LOGIN_USERNAME_SELECTOR] == "alice"
    assert page.filled[krypton.LOGIN_PASSWORD_SELECTOR] == "s3cret"
    assert page.clicked == [krypton.LOGIN_SUBMIT_SELECTOR]


def test_navigates_to_login_page(monkeypatch):
    page = FakePage()
    patch_playwright(monkeypatch, page)

    krypton.get_subscriber_status(make_provider(portal_url="https://example.test/login.php"), "user1")

    assert page.goto_calls == ["https://example.test/login.php"]


# --- Failure cases ---
# Login failure and an unexpected CAPTCHA challenge are DELIBERATELY
# indistinguishable in this adapter (see the module docstring and the
# design spec's "Explicit non-goals") -- both are simply "the subscriber
# list never appeared", reported identically as 'auth_failed'. There is
# only one test for this path, not two, because the code has no branch
# that could tell them apart -- writing two tests would only assert the
# same code path twice under different names.

def test_get_subscriber_status_auth_failed(monkeypatch):
    page = FakePage(login_succeeds=False)
    patch_playwright(monkeypatch, page)

    ok, reason = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "auth_failed")


def test_get_subscriber_status_not_found(monkeypatch):
    page = FakePage(row_found=False)
    patch_playwright(monkeypatch, page)

    ok, reason = krypton.get_subscriber_status(make_provider(), "ghost")

    assert (ok, reason) == (False, "not_found")


def test_get_subscriber_status_rejects_substring_match(monkeypatch):
    # "user1" must not match a row whose username cell is "user10".
    cells = make_cells(username="user10")
    page = FakePage(cell_texts=cells)
    patch_playwright(monkeypatch, page)

    ok, reason = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "not_found")


def test_get_subscriber_status_ambiguous_match_is_scrape_failed(monkeypatch):
    page = FakePage(rows=[
        FakeRowLocator(make_cells(username="user1")),
        FakeRowLocator(make_cells(username="user1")),
    ])
    patch_playwright(monkeypatch, page)

    ok, reason = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "scrape_failed")


def test_fills_username_filter_before_searching_rows(monkeypatch):
    # Regression-shaped test mirroring the PROradius pagination fix: the
    # username filter box (resolved dynamically -- the fake records what
    # was filled via the same holder dict the real filter input would use)
    # must be filled before the row lookup.
    page = FakePage(cell_texts=make_cells(username="user1"))
    patch_playwright(monkeypatch, page)

    krypton.get_subscriber_status(make_provider(), "user1")

    assert page.filled[3] == "user1"  # index 3 == the default Username column position


def test_filtered_search_timeout_is_not_found_not_timeout(monkeypatch):
    # Mirrors the PROradius fix: a genuine timeout waiting for the
    # FILTERED row (server-side search never turned up a match within
    # budget) means the user really isn't there -- 'not_found', not the
    # generic 'timeout' reserved for the table failing to load at all.
    page = FakePage(filtered_wait_times_out=True)
    patch_playwright(monkeypatch, page)

    ok, reason = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "not_found")


def test_get_subscriber_status_timeout(monkeypatch):
    page = FakePage(goto_raises=PlaywrightTimeoutError("portal did not respond"))
    patch_playwright(monkeypatch, page)

    ok, reason = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "timeout")


def test_get_subscriber_status_scrape_failed_on_generic_error(monkeypatch):
    page = FakePage(goto_raises=PlaywrightError("navigation crashed"))
    patch_playwright(monkeypatch, page)

    ok, reason = krypton.get_subscriber_status(make_provider(), "user1")

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

    krypton.get_subscriber_status(make_provider(), "user1")

    assert browser_holder["browser"].closed is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_upstream_portal_krypton.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'upstream_portal_krypton'` (or `ImportError`) -- the module doesn't exist yet.

- [ ] **Step 3: Create `upstream_portal_krypton.py`**

```python
"""Browser-automation adapter for read-only status checks against Krypton-
product upstream RADIUS reseller portals (MyISP, Smart Networks, and --
built generically but not yet live-verified -- Wise-ISP). See
docs/superpowers/specs/2026-08-23-krypton-upstream-status-sync-design.md.

A separate module from upstream_portal.py (the PROradius adapter, covering
Terra and Northern Telecom) because Krypton is a completely different
codebase -- classic server-rendered jQuery/DataTables pages, not a React
SPA -- sharing nothing with PROradius but the calling contract: every
public function returns (ok: bool, value) and never raises, same as
mikrotik.py and upstream_portal.py.

Read-only only: never clicks Renew/Block/Unblock or any other mutating
action, only logs in, fills the Username column's own search box, and
reads text -- no clicks anywhere on the subscriber list itself.

CAPTCHA HANDLING (deliberate, not a gap): a `captcha` input exists in both
confirmed portals' login forms, but its container is hidden by default,
and a correct-credentials login on both MyISP and Smart Networks went
straight through with zero CAPTCHA interaction on 2026-08-23 live
discovery -- consistent with a conditional anti-bruteforce measure, not a
blanket gate. This module NEVER attempts to detect, solve, or bypass the
CAPTCHA under any circumstance. Login succeeds by waiting for the
subscriber list to actually appear (Krypton redirects there directly, no
separate dashboard step); if that wait times out for ANY reason -- wrong
password, an unexpected CAPTCHA challenge, or anything else -- this is
reported as a plain 'auth_failed', identically and deliberately
indistinguishable from any other login failure. Do not add code that tries
to detect or react to the CAPTCHA specifically.
"""
import logging

from dateutil import parser as date_parser
from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# Same reasoning as upstream_portal.py's _TIMEOUT_MS: keeps the realistic
# worst-case total (~5 sequential waits here: login goto, wait-for-
# subscriber-list, wait-for-filtered-row, plus default timeouts on other
# calls) comfortably under the Dockerfile's `gunicorn --timeout 120`.
_TIMEOUT_MS = 10_000

# Confirmed live on both MyISP (pi.myisp.live) and Smart Networks
# (rad.smartnetworkslb.net) on 2026-08-23.
LOGIN_USERNAME_SELECTOR = 'input[name="login_username"]'
LOGIN_PASSWORD_SELECTOR = 'input[name="login_password"]'
# Confirmed live: a <button type="button"> (not a native form submit) with
# this exact text -- the page handles the click via its own JS (an AJAX
# login), so a real click is required, not a keyboard Enter.
LOGIN_SUBMIT_SELECTOR = 'button:has-text("Log In")'
# Confirmed identical on both portals via
# `window.jQuery('#example-1').DataTable()`.
SUBSCRIBER_TABLE_SELECTOR = '#example-1'

# Krypton has a user-customizable "Column visibility" toggle (confirmed
# live: 90 columns defined, only 35 rendered by default) -- a hardcoded
# column index (the approach that works fine for PROradius, which has no
# such toggle) would silently break for a tenant who's customized their own
# view. These are the only three columns this adapter needs; their
# positions are resolved by header TEXT at runtime, every call, via
# _resolve_column_indices() -- never assume a fixed index.
_REQUIRED_COLUMNS = ("Username", "Status", "Expiry Date")

# Status is plain readable text (confirmed live, e.g. "Online") -- unlike
# PROradius, which conveys status via a CSS class with no visible text.
# The portal's own Status column filter dropdown lists the full
# vocabulary: Online, Offline, Offline > 2 days, Active, Expired, Near
# Expiry, Q-Exceeded, Blocked. "Active" is deliberately NOT mapped here --
# its real meaning/color was never confirmed live, unlike the other seven
# -- it falls through to 'unknown', same safe fallback as any unrecognized
# text. Substring matching (not exact-equality) so "Offline > 2 days"
# still matches the 'offline' entry.
_STATUS_TEXT_MAP = (
    ('near expiry', 'near_expiry'),
    ('expired', 'expired'),
    ('q-exceeded', 'quota_exceeded'),
    ('blocked', 'blocked'),
    ('offline', 'offline'),
    ('online', 'online'),
)


class LoginFailed(Exception):
    pass


class SubscriberNotFound(Exception):
    pass


class AmbiguousSubscriberMatch(Exception):
    pass


class ScrapeStructureChanged(Exception):
    pass


def _login(page, provider):
    page.goto(provider.portal_url, timeout=_TIMEOUT_MS)
    page.fill(LOGIN_USERNAME_SELECTOR, provider.portal_username)
    page.fill(LOGIN_PASSWORD_SELECTOR, provider.portal_password)
    page.click(LOGIN_SUBMIT_SELECTOR)
    # Krypton redirects straight to the subscriber list on success (no
    # separate dashboard step, unlike PROradius) -- waiting for the table
    # to actually have a row IS the login-success signal. This is robust
    # to not knowing exactly what a FAILED login looks like (never tested
    # live, deliberately -- see module docstring): whatever a failed
    # attempt or an unexpected CAPTCHA challenge looks like, this table
    # never appears, so the wait times out and this is reported the same
    # way regardless of the real cause.
    try:
        page.wait_for_selector(f"{SUBSCRIBER_TABLE_SELECTOR} tbody tr", timeout=_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise LoginFailed("Login did not reach the subscriber list")


def _resolve_column_indices(page):
    """Returns {'Username': int, 'Status': int, 'Expiry Date': int} --
    each value is the 0-indexed position of that header among currently-
    VISIBLE header cells (offsetParent !== null), matched by exact header
    text. One atomic JS evaluation, not a separate Python-side
    visibility-check-then-text-read per column -- avoids both a race
    between those two reads and needing 3x the round trips."""
    indices = page.evaluate(
        """(headerNames) => {
            const visible = Array.from(document.querySelectorAll('thead th')).filter(th => th.offsetParent !== null);
            const labelOf = (th) => (th.childNodes[0] ? th.childNodes[0].textContent : th.textContent).trim();
            return headerNames.map(name => visible.findIndex(th => labelOf(th) === name));
        }""",
        list(_REQUIRED_COLUMNS),
    )
    result = dict(zip(_REQUIRED_COLUMNS, indices))
    missing = [name for name, idx in result.items() if idx < 0]
    if missing:
        raise ScrapeStructureChanged(f"required column(s) not found: {missing}")
    return result


def _find_subscriber_row(page, username, username_col_index):
    filter_input = page.locator('thead th:visible').nth(username_col_index).locator('input')
    filter_input.fill(username)
    # Confirmed live: filtering re-queries the portal's own backend (real
    # server-side search, not instant client-side) on both MyISP and Smart
    # Networks. Actively waiting for a matching row -- succeeding as soon
    # as it appears, up to the full timeout -- instead of a fixed delay is
    # a hard requirement here: a fixed delay caused a real production
    # regression on the PROradius adapter for exactly this class of
    # server-side-search UI (see upstream_portal.py's _find_subscriber_row
    # history) and must not be repeated.
    try:
        page.wait_for_selector(f'{SUBSCRIBER_TABLE_SELECTOR} tbody tr:has-text("{username}")', timeout=_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise SubscriberNotFound(username)
    # `has_text` is a coarse pre-filter across the whole row's visible
    # text. The exact-match check below against just the Username column
    # is what actually determines a real match -- prevents ever silently
    # persisting a different subscriber's status/expiry as this
    # customer's.
    candidates = page.locator(f"{SUBSCRIBER_TABLE_SELECTOR} tbody tr", has_text=username)
    matches = []
    for i in range(candidates.count()):
        row = candidates.nth(i)
        # td:visible, not plain td -- Krypton rows have the same 90-vs-35
        # visible/defined column split as the header row; indexing into
        # unfiltered <td>s would silently read the wrong column entirely.
        username_cell_text = row.locator("td:visible").nth(username_col_index).inner_text().strip()
        if username_cell_text == username:
            matches.append(row)
    if len(matches) == 0:
        raise SubscriberNotFound(username)
    if len(matches) > 1:
        raise AmbiguousSubscriberMatch(username, len(matches))
    return matches[0]


def _parse_status(row, status_col_index):
    text = row.locator("td:visible").nth(status_col_index).inner_text().strip().lower()
    for needle, status in _STATUS_TEXT_MAP:
        if needle in text:
            return status
    return "unknown"


def _parse_expiry(row, expiry_col_index):
    cells = row.locator("td:visible")
    if cells.count() <= expiry_col_index:
        return None
    text = cells.nth(expiry_col_index).inner_text().strip()
    if not text:
        return None
    try:
        return date_parser.parse(text)
    except (ValueError, OverflowError):
        return None


def get_subscriber_status(provider, username):
    """Logs into `provider`'s Krypton portal, finds `username` in the
    subscriber list, reads back their status + expiry.

    Returns (True, {'status': 'online'|'offline'|'expired'|'near_expiry'|
    'blocked'|'quota_exceeded'|'unknown', 'expiry': datetime|None}) on
    success. Returns (False, reason) on any failure, where reason is one
    of 'auth_failed', 'not_found', 'timeout', 'scrape_failed'. Never
    raises.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_default_timeout(_TIMEOUT_MS)
                _login(page, provider)
                columns = _resolve_column_indices(page)
                row = _find_subscriber_row(page, username, columns["Username"])
                status = _parse_status(row, columns["Status"])
                expiry = _parse_expiry(row, columns["Expiry Date"])
            finally:
                browser.close()
    except LoginFailed as e:
        logger.warning("Krypton portal login failed for provider %s: %s", provider.id, e)
        return False, "auth_failed"
    except SubscriberNotFound:
        return False, "not_found"
    except AmbiguousSubscriberMatch as e:
        matched_username, match_count = e.args
        logger.warning(
            "Krypton portal lookup for username %r on provider %s matched %d rows exactly -- "
            "refusing to guess which is correct", matched_username, provider.id, match_count
        )
        return False, "scrape_failed"
    except ScrapeStructureChanged as e:
        logger.warning("Krypton portal structure changed for provider %s: %s", provider.id, e)
        return False, "scrape_failed"
    except PlaywrightTimeoutError as e:
        logger.warning("Krypton portal timed out for provider %s: %s", provider.id, e)
        return False, "timeout"
    except PlaywrightError as e:
        logger.warning("Krypton portal scrape failed for provider %s: %s", provider.id, e)
        return False, "scrape_failed"

    return True, {"status": status, "expiry": expiry}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_upstream_portal_krypton.py -v`
Expected: all tests PASS (19 tests).

- [ ] **Step 5: Commit**

```bash
git add upstream_portal_krypton.py tests/test_upstream_portal_krypton.py
git commit -m "feat: add read-only Krypton upstream portal status adapter"
```

---

### Task 2: Wire Krypton into the sync endpoint

**Files:**
- Modify: `app.py` (add import near the existing `import upstream_portal` at line 89; modify `sync_customer_upstream_status`, currently at line 6455-6487)
- Modify: `tests/test_upstream_status_sync.py`

**Interfaces:**
- Consumes: `upstream_portal_krypton.get_subscriber_status(provider, username)` from Task 1, same `(ok, value)` contract as `upstream_portal.get_subscriber_status`.

**Context:** The endpoint currently calls `upstream_portal.get_subscriber_status(provider, customer.upstream_username)` unconditionally (`app.py:6467`). It must dispatch on `provider.product` instead. Everything else in the endpoint (the 400/404 checks, the `db.session.commit()`, the "never overwrite expiry with None" guard, the response shape) is unchanged and already correct for either product -- do not touch it beyond the one dispatch line.

- [ ] **Step 1: Write the failing test**

The existing `_setup_bridged_customer` helper in `tests/test_upstream_status_sync.py` hardcodes `"product": "proradius"` when creating the `UpstreamProvider`:

```python
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
    return customer_resp.get_json()["customer_id"] if customer_resp.status_code in (200, 201) else None
```

Give it an optional `product` parameter (default `"proradius"` preserves every existing call site unchanged):

```python
def _setup_bridged_customer(client, hdr, upstream_username="cust1", product="proradius"):
    plan_id = client.post("/api/subscription_plans", headers=hdr,
                          json={"name": "P", "price": 10, "billing_cycle": "monthly"}).get_json()["plan"]["id"]
    provider_id = client.post("/api/upstream-providers", headers=hdr,
                              json={"name": "Terra", "product": product,
                                    "portal_url": "https://acppro.terra.net.lb/login/",
                                    "portal_username": "reseller1", "portal_password": "pw"}
                              ).get_json()["provider"]["id"]
    customer_resp = client.post("/api/customers", headers=hdr,
                                json={"name": "Cust", "phone": "1", "address": "a",
                                      "subscription_plan_id": plan_id,
                                      "subscription_start_date": "2026-01-01",
                                      "upstream_provider_id": provider_id,
                                      "upstream_username": upstream_username})
    return customer_resp.get_json()["customer_id"] if customer_resp.status_code in (200, 201) else None
```

Then add this test to the same file, matching the existing tests' exact style (`make_tenant`, `appmod` already imported at the top of the file):

```python
def test_sync_dispatches_to_krypton_adapter_for_krypton_product(client, monkeypatch):
    hdr = make_tenant(client, "Biz E", "e_admin")
    customer_id = _setup_bridged_customer(client, hdr, product="krypton")
    assert customer_id is not None

    monkeypatch.setattr(
        appmod.upstream_portal_krypton, "get_subscriber_status",
        lambda provider, username: (True, {"status": "online", "expiry": None}),
    )
    monkeypatch.setattr(
        appmod.upstream_portal, "get_subscriber_status",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("PROradius adapter must not be called for a krypton provider")),
    )

    resp = client.post(f"/api/customers/{customer_id}/upstream-status-sync", headers=hdr)

    assert resp.status_code == 200
    assert resp.get_json()["upstream_last_status"] == "online"
```

The second `monkeypatch.setattr` (making the PROradius adapter raise if called at all) is what proves the dispatch actually happened -- not just that some adapter happened to return success.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_upstream_status_sync.py::test_sync_dispatches_to_krypton_adapter_for_krypton_product -v`
Expected: FAIL -- either an `AttributeError` (no `upstream_portal_krypton` imported in `app.py` yet) or the assertion that the PROradius adapter was wrongly called (since today's code always calls it regardless of `product`).

- [ ] **Step 3: Add the import and dispatch**

In `app.py`, next to the existing `import upstream_portal` (line 89):

```python
import upstream_portal
import upstream_portal_krypton
```

Then in `sync_customer_upstream_status` (`app.py:6455-6487`), replace:

```python
    ok, result = upstream_portal.get_subscriber_status(provider, customer.upstream_username)
```

with:

```python
    if provider.product == 'krypton':
        ok, result = upstream_portal_krypton.get_subscriber_status(provider, customer.upstream_username)
    else:
        ok, result = upstream_portal.get_subscriber_status(provider, customer.upstream_username)
```

(Everything else in the function -- the `if not ok:` branch, the field updates, the response -- is untouched; both adapters return the identical `(ok, value)` shape.)

Also update the `UpstreamProvider.product` column's inline comment (`app.py:193`) for accuracy -- there is no backend validation/allow-list for this field (confirmed: it's a plain unconstrained `String(20)`), so this is documentation only, not a functional change:

```python
    product = db.Column(db.String(20), nullable=False, default='manual')  # 'proradius', 'radiusnew', 'krypton', 'manual'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_upstream_status_sync.py -v`
Expected: all tests in this file PASS, including the new one and every pre-existing PROradius-path test (proving the `else` branch preserves current behavior exactly).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_upstream_status_sync.py
git commit -m "feat: dispatch upstream status sync to the Krypton adapter by product"
```

---

### Task 3: Frontend -- Krypton product option + new status colors

**Files:**
- Modify: `frontend/src/components/UpstreamProviderManagementView.js` (`PRODUCT_LABELS` at line 18; the Product `<TextField select>`'s `MenuItem`s at lines 227-229)
- Modify: `frontend/src/components/SubscriptionsView.js` (`getUpstreamStatusColor` at line 285)

**Interfaces:**
- Consumes: the three new status strings from Task 1 (`'blocked'`, `'near_expiry'`, `'quota_exceeded'`) -- must render with a real color, not silently fall through to the existing default gray, so staff can visually distinguish them from `'unknown'`.

This task has no automated frontend tests (matches this codebase's existing convention for `SubscriptionsView.js` -- verified by `npm run build` + a manual browser check, same as every other frontend task in the original upstream-status-sync plan).

- [ ] **Step 1: Add the `krypton` product option**

In `frontend/src/components/UpstreamProviderManagementView.js`, change line 18 from:

```javascript
const PRODUCT_LABELS = { proradius: 'PROradius', radiusnew: 'radiusnew', manual: 'Manual' };
```

to:

```javascript
const PRODUCT_LABELS = { proradius: 'PROradius', radiusnew: 'radiusnew', krypton: 'Krypton', manual: 'Manual' };
```

And in the Product dropdown (lines 227-229), add a `MenuItem` after the `radiusnew` one:

```javascript
                                <MenuItem value="manual">Manual (not yet classified / no automation planned)</MenuItem>
                                <MenuItem value="proradius">PROradius</MenuItem>
                                <MenuItem value="radiusnew">radiusnew</MenuItem>
                                <MenuItem value="krypton">Krypton</MenuItem>
```

- [ ] **Step 2: Add the three new status colors**

In `frontend/src/components/SubscriptionsView.js`, change line 285 from:

```javascript
    const getUpstreamStatusColor = (status) => ({ online: '#10B981', offline: '#EF4444', expired: '#F59E0B' }[status] || '#6B7280');
```

to:

```javascript
    // 'blocked'/'near_expiry'/'quota_exceeded' are Krypton-only values (see
    // upstream_portal_krypton.py) -- deliberately NOT copying Krypton's own
    // portal colors (it renders blocked=orange, offline=light blue) since
    // this app's status colors already mean the same thing across every
    // upstream product (offline=red, expired=orange from the PROradius
    // work) regardless of any one portal's own theme.
    const getUpstreamStatusColor = (status) => ({
        online: '#10B981',
        offline: '#EF4444',
        expired: '#F59E0B',
        blocked: '#DC2626',
        near_expiry: '#EAB308',
        quota_exceeded: '#8B5CF6',
    }[status] || '#6B7280');
```

- [ ] **Step 3: Build and verify**

Run: `cd frontend && npm run build`
Expected: builds successfully, no new errors (pre-existing warnings in this file are fine, matching every prior frontend task in this project).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/UpstreamProviderManagementView.js frontend/src/components/SubscriptionsView.js
git commit -m "feat: add Krypton product option and its status colors to Subscriptions UI"
```

---

### Task 4: Live discovery against a real Krypton portal

**This task is done by the controller directly with the user, not dispatched to an implementer subagent** -- it requires a live, credentialed browser session the user drives themselves (same reason Task 7 of the original PROradius plan was done this way, not delegated).

**Goal:** confirm the two details Task 1's module could not verify from prior live discovery, using MyISP (the tenant's own real account):

1. **The exact username filter input selector**, to confirm `page.locator('thead th:visible').nth(username_col_index).locator('input')` actually resolves to a real, fillable `<input>` (confirmed live only that the Username *header* exists at a given visible index and that 20 per-column filter inputs exist somewhere in `thead` -- not confirmed that indexing into `thead th:visible` by position and then finding `input` as a *child* of that specific `<th>` is structurally correct, as opposed to filter inputs living in a separate `<tr>` in `thead`, e.g. a `tfoot`-style filter row).
2. **The `_resolve_column_indices` JS evaluation actually works against the real DOM** as written, not just as manually verified via ad hoc one-off JS calls during the design's live discovery session.

**Do NOT test a wrong password or attempt to trigger the CAPTCHA during this task.** The design's whole point is to never need to know what a failed login looks like -- deliberately do not probe for it.

- [ ] **Step 1: Open MyISP and log in with a real linked customer already known to exist**

Open `https://pi.myisp.live/login.php` in the browser tool, have the user log in with their real credentials, confirm the subscriber list loads.

- [ ] **Step 2: Inspect the actual per-column filter markup for the Username column**

Using the browser tool's JS evaluation, find the currently-visible header cell whose text is "Username", and confirm it (or a structurally-findable sibling/child) contains an `<input>` that can be filled and that filling it actually narrows the table. Compare against what Task 1 assumed (`thead th:visible >> nth=username_index >> input`) and note any structural difference.

- [ ] **Step 3: Run the exact `_resolve_column_indices` JS snippet live**

Paste the exact JS string from `upstream_portal_krypton.py`'s `_resolve_column_indices` into the browser tool's JS evaluation against the real MyISP page (passing `["Username", "Status", "Expiry Date"]` as the argument), and confirm it returns three valid (non-negative) indices matching what manual inspection shows.

- [ ] **Step 4: Fix `upstream_portal_krypton.py` if either check in Steps 2-3 revealed a discrepancy**

If the filter input isn't a direct/indexable child of the visible `<th>` the way assumed, or the JS evaluation doesn't return the expected indices, correct `_find_subscriber_row` and/or `_resolve_column_indices` in `upstream_portal_krypton.py` to match the real structure, update the corresponding fakes in `tests/test_upstream_portal_krypton.py` to match, and re-run `pytest tests/test_upstream_portal_krypton.py -v` to confirm everything still passes with the corrected structure.

- [ ] **Step 5: Commit any corrections**

```bash
git add upstream_portal_krypton.py tests/test_upstream_portal_krypton.py
git commit -m "fix: confirm Krypton column/filter selectors against the live portal"
```

(Skip this commit if Steps 2-3 confirmed everything matched Task 1's assumptions exactly -- nothing to fix.)

---

### Task 5: Rollout & data safety, then a supervised smoke test

**This task is done by the controller directly with the user** -- same reasoning as Task 4, and matching exactly how the original PROradius plan's final task was actually executed (a live, iterative smoke test, not a one-shot mechanical step).

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass, including every test added in Tasks 1-2 and every pre-existing test (proving the PROradius path is untouched).

- [ ] **Step 2: Confirm the feature is inert for non-Krypton tenants**

No migration exists for this feature (see Global Constraints) -- confirm by inspection that `provider.product == 'krypton'` is the only new code path added to `sync_customer_upstream_status`, and that any `UpstreamProvider` with `product` in `{'proradius', 'radiusnew', 'manual'}` takes the unchanged `else` branch. `'radiusnew'` (the CAPTCHA'd family, e.g. Wise-ISP under its OTHER possible product classification, MyISP/Smart Networks/Wise-ISP if ever misclassified as `'radiusnew'` instead of `'krypton'`) would incorrectly fall to the PROradius adapter and fail cleanly as `scrape_failed` or `auth_failed` against the wrong page shape -- not a data-safety issue (still read-only, still never raises), but worth a quick manual check that the tenant's actual `UpstreamProvider.product` values for MyISP/Smart Networks/Wise-ISP are set to `'krypton'`, not left at `'manual'` or misclassified, before expecting this to work for them.

- [ ] **Step 3: Deploy and run one real sync against a real MyISP or Smart Networks customer**

With the user, link a real customer to their MyISP or Smart Networks `UpstreamProvider` (product set to `krypton`, matching Step 2) if not already linked, click "Refresh Upstream Status", and confirm the result (status + expiry) matches what the portal itself shows for that same customer -- the same acceptance bar used for both the original Terra smoke test and the Northern Telecom extension.

- [ ] **Step 4: Iterate on any live discrepancy**

If the live result doesn't match (wrong status, wrong expiry, a failure reason that doesn't match what actually happened), diagnose against the real page structure the same way the two PROradius production bugs were diagnosed after their own smoke tests (missing navigation, then a fixed-delay race condition) -- fix `upstream_portal_krypton.py`, add a regression test proving the specific bug is now covered, re-run `pytest tests/test_upstream_portal_krypton.py -v`, and re-test live. Repeat until a real customer's status syncs correctly.

- [ ] **Step 5: Commit the working state**

```bash
git add -A
git commit -m "fix: confirm Krypton adapter against a real live sync (MyISP/Smart Networks)"
```

(Only if Step 4 required changes. If Step 3 succeeded on the first try, there's nothing to commit here.)
