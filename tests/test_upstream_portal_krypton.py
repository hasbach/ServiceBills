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
