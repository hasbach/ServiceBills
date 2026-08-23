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
from datetime import datetime, timedelta

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


# Fixed far-future default -- deliberately NOT close to "now" by any
# realistic margin. _apply_near_expiry_override() (added after live
# discovery found 'near_expiry' can't be text-matched for the common
# case -- see upstream_portal_krypton.py) computes off the real clock, so
# a default expiry that was merely "months out" when this file was
# written would eventually drift inside the near-expiry window and start
# silently flipping the status-text-mapping tests below that use "Online"
# or "Active" (which fall through to 'unknown') without ever touching the
# override's own dedicated tests.
DEFAULT_TEST_EXPIRY = "2030-01-01 12:30:00"


def days_from_now(n):
    """Returns an expiry-cell-formatted string N days from the real
    clock -- used only by the near-expiry override tests below, which
    must test relative to "today" rather than a fixed date."""
    return (datetime.utcnow() + timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")


def make_cells(username="user1", status_text="Online", expiry=DEFAULT_TEST_EXPIRY, n_cols=12):
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


class FakeHeaderCell:
    """Models `page.locator('thead th:visible').nth(i)` -- only its
    `.evaluate(script, arg)` is used by the code under test, driving the
    DataTable's own column-search API directly (see
    _find_subscriber_row -- filling the visible per-column filter <input>
    was confirmed live not to reliably trigger this portal's search, so
    the real code no longer does that)."""

    def __init__(self, filled_holder, index):
        self._filled_holder = filled_holder
        self._index = index

    def evaluate(self, script, arg=None):
        self._filled_holder[self._index] = arg


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
        self.goto_wait_until_calls = []
        if rows is not None:
            self._rows = rows
        elif row_found:
            self._rows = [FakeRowLocator(cell_texts if cell_texts is not None else make_cells())]
        else:
            self._rows = []

    def set_default_timeout(self, ms):
        pass

    def goto(self, url, timeout=None, wait_until=None):
        self.goto_calls.append(url)
        self.goto_wait_until_calls.append(wait_until)
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
    assert value["expiry"].year == 2030 and value["expiry"].month == 1 and value["expiry"].day == 1


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


def test_get_subscriber_status_disabled_maps_to_blocked(monkeypatch):
    # Confirmed live on MyISP, 2026-08-23: filtering the Status column to
    # its "Blocked" dropdown option returns rows whose own Status cell
    # reads "Disabled", never literally "Blocked" -- the filter category
    # name and the rendered text are different strings on this portal.
    page = FakePage(cell_texts=make_cells(status_text="Disabled"))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "blocked")


# --- Near-expiry override (computed from the Expiry Date, not text) ---
# Confirmed live on MyISP, 2026-08-23: filtering to the "Near Expiry"
# Status category returns real subscribers whose own Status cell reads
# plain "Online" -- the category is a server-side query condition, never
# reflected in the cell's own text. _apply_near_expiry_override() computes
# this instead from the already-parsed Expiry Date. These tests use
# days_from_now() (relative to the real clock), not a fixed date string,
# because the thing under test is itself relative to "today".

def test_get_subscriber_status_online_near_expiry_overrides_to_near_expiry(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Online", expiry=days_from_now(2)))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "near_expiry")


def test_get_subscriber_status_unknown_near_expiry_overrides_to_near_expiry(monkeypatch):
    # "Active" (falls through to 'unknown') is also eligible for the
    # override -- surfacing a computed near_expiry beats leaving a
    # genuinely useful signal as an uninformative 'unknown'.
    page = FakePage(cell_texts=make_cells(status_text="Active", expiry=days_from_now(3)))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "near_expiry")


def test_get_subscriber_status_offline_not_overridden_even_when_near_expiry(monkeypatch):
    # A more specific, already-confirmed-live text signal (offline,
    # expired, blocked, quota_exceeded) takes priority over the computed
    # override -- only 'online'/'unknown' are eligible.
    page = FakePage(cell_texts=make_cells(status_text="Offline", expiry=days_from_now(1)))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "offline")


def test_get_subscriber_status_near_expiry_boundary_expires_today(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Online", expiry=days_from_now(0)))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "near_expiry")


def test_get_subscriber_status_near_expiry_boundary_5_days_included(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Online", expiry=days_from_now(5)))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "near_expiry")


def test_get_subscriber_status_near_expiry_boundary_6_days_excluded(monkeypatch):
    page = FakePage(cell_texts=make_cells(status_text="Online", expiry=days_from_now(6)))
    patch_playwright(monkeypatch, page)

    ok, value = krypton.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "online")


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
    cells[11] = DEFAULT_TEST_EXPIRY  # far-future -- see DEFAULT_TEST_EXPIRY's comment
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


def test_goto_waits_for_domcontentloaded_not_load(monkeypatch):
    # Confirmed via a real production timeout on Smart Networks, 2026-08-23:
    # Playwright's default wait_until="load" waits for every subresource
    # (images, fonts, third-party scripts) to finish, and something on
    # this portal's page never does from Render's network even though the
    # page itself is fully usable well before that -- `Page.goto: Timeout
    # 20000ms exceeded ... waiting until "load"` regardless of which
    # configured URL was tried. Waiting for domcontentloaded only, which
    # is all the code actually needs, fixed it.
    page = FakePage()
    patch_playwright(monkeypatch, page)

    krypton.get_subscriber_status(make_provider(), "user1")

    assert page.goto_wait_until_calls == ["domcontentloaded"]


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


def test_searches_username_column_before_searching_rows(monkeypatch):
    # Regression-shaped test mirroring the PROradius pagination fix: the
    # column search (resolved dynamically -- the fake records what was
    # searched via the same holder dict the real DataTable API call would
    # use) must be triggered before the row lookup.
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
