"""Tests for upstream_portal.py -- no real browser or real portal involved.
`sync_playwright` is monkeypatched to a small fake Playwright/Browser/Page
double. These fakes model the real Terra page structure confirmed via live
discovery (2026-08-23): status is a CSS class on a chip in the username
cell, and expiry is a specific table column (index 9), not free row text."""
import types

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import upstream_portal


def make_provider(id=1, portal_url="https://example.test/login", portal_username="u", portal_password="p"):
    return types.SimpleNamespace(id=id, portal_url=portal_url, portal_username=portal_username, portal_password=portal_password)


def make_cells_with_username(username, expiry="2026-09-01 17:38"):
    # 10 columns, matching Terra's real order; only the username (column 0)
    # and Expiry (column 9) are ever read by the code under test, the rest
    # are unused placeholders.
    return [username, "Full Name", "Address", "Phone", "Reseller", "Service", "MAC", "IP", "2026-01-01 00:00", expiry]


def make_cells(expiry="2026-09-01 17:38"):
    return make_cells_with_username("user1", expiry)


class FakeCell:
    def __init__(self, text, chip_class=None):
        self._text = text
        # Only the username cell (column 0) carries a status chip in the
        # real portal -- non-username cells get chip_class=None.
        self._chip_class = chip_class

    def inner_text(self):
        return self._text

    def locator(self, selector, has_text=None):
        return FakeChipLocator(self._chip_class)


class FakeCellsLocator:
    def __init__(self, cell_texts, chip_class=None):
        self._cell_texts = cell_texts
        self._chip_class = chip_class

    def count(self):
        return len(self._cell_texts)

    def nth(self, i):
        if i >= len(self._cell_texts):
            return FakeCell("")
        chip_class = self._chip_class if i == 0 else None
        return FakeCell(self._cell_texts[i], chip_class=chip_class)


class FakeChipLocator:
    def __init__(self, chip_class):
        self._chip_class = chip_class

    def count(self):
        return 1 if self._chip_class else 0

    @property
    def first(self):
        return self

    def get_attribute(self, name):
        return self._chip_class if name == "class" else None


class FakeRowLocator:
    """A single fake <tr>. `chip_class` models the status chip that lives in
    the username cell (column 0) only."""

    def __init__(self, cell_texts, chip_class="bg-success/20 text-success-700"):
        self._cell_texts = cell_texts
        self._chip_class = chip_class

    def locator(self, selector, has_text=None):
        if selector == "td":
            return FakeCellsLocator(self._cell_texts, chip_class=self._chip_class)
        return FakeChipLocator(self._chip_class)

    def inner_text(self):
        # Used to emulate Playwright's `has_text` pre-filter, which matches
        # anywhere in the row's full text, not just the username cell.
        return " ".join(self._cell_texts)


class FakeRowsLocator:
    """Models `page.locator(selector, has_text=...)` over multiple <tr>s."""

    def __init__(self, rows, has_text=None):
        if has_text is not None:
            self._rows = [r for r in rows if has_text in r.inner_text()]
        else:
            self._rows = list(rows)

    def count(self):
        return len(self._rows)

    def nth(self, i):
        return self._rows[i]


class FakePage:
    def __init__(self, row_found=True, chip_class="bg-success/20 text-success-700", cell_texts=None,
                 rows=None, login_succeeds=True, goto_raises=None, filtered_wait_times_out=False):
        self._login_succeeds = login_succeeds
        self._goto_raises = goto_raises
        self._filtered_wait_times_out = filtered_wait_times_out
        self.filled = {}
        self.clicked = []
        self.goto_calls = []
        if rows is not None:
            self._rows = rows
        elif row_found:
            self._rows = [FakeRowLocator(cell_texts if cell_texts is not None else make_cells(), chip_class=chip_class)]
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
            raise PlaywrightTimeoutError("timed out waiting for login")
        if self._filtered_wait_times_out and "has-text" in selector:
            raise PlaywrightTimeoutError("timed out waiting for filtered row")

    def locator(self, selector, has_text=None):
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
    monkeypatch.setattr(upstream_portal, "sync_playwright", lambda: FakeSyncPlaywrightCM(page))


# --- Success cases ---

def test_get_subscriber_status_online(monkeypatch):
    page = FakePage(chip_class="bg-success/20 text-success-700", cell_texts=make_cells("2026-09-01 17:38"))
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert ok is True
    assert value["status"] == "online"
    assert value["expiry"].year == 2026 and value["expiry"].month == 9 and value["expiry"].day == 1


def test_get_subscriber_status_expired(monkeypatch):
    page = FakePage(chip_class="bg-warning/20 text-warning-700", cell_texts=make_cells("2026-01-15 00:00"))
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "expired")


def test_get_subscriber_status_offline(monkeypatch):
    page = FakePage(chip_class="bg-danger/20 text-danger-700", cell_texts=make_cells("2026-09-01 17:38"))
    patch_playwright(monkeypatch, page)

    ok, value = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, value["status"]) == (True, "offline")


def test_login_fills_credentials_and_submits(monkeypatch):
    page = FakePage()
    patch_playwright(monkeypatch, page)

    upstream_portal.get_subscriber_status(make_provider(portal_username="alice", portal_password="s3cret"), "user1")

    assert page.filled[upstream_portal.LOGIN_USERNAME_SELECTOR] == "alice"
    assert page.filled[upstream_portal.LOGIN_PASSWORD_SELECTOR] == "s3cret"
    assert page.clicked == [upstream_portal.LOGIN_SUBMIT_SELECTOR]


# --- Failure cases ---

def test_get_subscriber_status_not_found(monkeypatch):
    page = FakePage(row_found=False)
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


# --- Regression tests for bugs the live smoke test caught ---

def test_navigates_to_login_then_subscriber_list(monkeypatch):
    page = FakePage()
    patch_playwright(monkeypatch, page)

    upstream_portal.get_subscriber_status(make_provider(portal_url="https://example.test/login/"), "user1")

    assert page.goto_calls == ["https://example.test/login/", "https://example.test/users"]


def test_get_subscriber_status_rejects_substring_match(monkeypatch):
    # "user1" must not match a row whose username cell is "user10"
    page = FakePage(cell_texts=make_cells_with_username("user10"))
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "not_found")


def test_fills_username_filter_before_searching_rows(monkeypatch):
    # Regression test for the pagination bug found live on Northern Telecom
    # (50 customers, 10/page -- Terra's <10 customers happened to fit on one
    # page, masking this during Terra-only discovery). The username filter
    # box must be filled BEFORE the row lookup, so a match beyond whatever
    # page happens to be showing is never missed.
    page = FakePage(cell_texts=make_cells_with_username("user1"))
    patch_playwright(monkeypatch, page)

    upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert page.filled[upstream_portal.USERNAME_FILTER_SELECTOR] == "user1"


def test_filtered_search_timeout_is_not_found_not_timeout(monkeypatch):
    # Regression test for the second production bug: a genuine timeout
    # waiting for the FILTERED row (the portal's own search never turned up
    # a match within budget) means the user really isn't there -- must be
    # reported as 'not_found', not the generic 'timeout' reserved for the
    # table failing to load at all.
    page = FakePage(filtered_wait_times_out=True)
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "not_found")


def test_get_subscriber_status_ambiguous_match_is_scrape_failed(monkeypatch):
    # Two rows whose username cells both exactly equal the searched username
    # is a real anomaly -- refuse to silently guess which one is correct.
    page = FakePage(rows=[
        FakeRowLocator(make_cells_with_username("user1")),
        FakeRowLocator(make_cells_with_username("user1")),
    ])
    patch_playwright(monkeypatch, page)

    ok, reason = upstream_portal.get_subscriber_status(make_provider(), "user1")

    assert (ok, reason) == (False, "scrape_failed")
